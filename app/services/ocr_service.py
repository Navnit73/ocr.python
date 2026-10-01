"""
OCR Service implementing PaddleOCR with non-blocking async execution and reading-order sorting.
"""

import asyncio
import logging
from typing import Dict, List, Optional, Tuple
import numpy as np

from app.core.config import get_settings
from app.schemas.ocr import OCRLine, PageExtraction
from app.services.image_service import ImageService

logger = logging.getLogger("ocr_service")


class OCRService:
    """Singleton/Manager for OCR engines with threadpool offloading and fallback support."""

    _instances: Dict[str, object] = {}
    _lock = asyncio.Lock()

    @classmethod
    def _get_engine(cls, lang: str = "en"):
        """
        Lazily instantiates and caches OCR engine per language.
        """
        if lang not in cls._instances:
            try:
                from paddleocr import PaddleOCR
                # Initialize PaddleOCR engine with modern orientation parameter
                try:
                    cls._instances[lang] = PaddleOCR(
                        use_textline_orientation=True,
                        lang=lang if lang in ["en", "ch", "hi", "fr", "german"] else "en",
                    )
                except TypeError:
                    cls._instances[lang] = PaddleOCR(
                        use_angle_cls=True,
                        lang=lang if lang in ["en", "ch", "hi", "fr", "german"] else "en",
                    )
                logger.info(f"PaddleOCR initialized successfully for language '{lang}'")
            except Exception as e:
                logger.warning(f"PaddleOCR engine initialization warning: {e}. Using fallback reader.")
                cls._instances[lang] = None
        return cls._instances[lang]

    @classmethod
    async def extract_from_image_bytes(
        cls,
        image_bytes: bytes,
        page_number: int = 1,
        lang: str = "en",
        is_scanned: bool = True,
    ) -> PageExtraction:
        """
        Extracts OCR text from image bytes asynchronously using a worker thread.
        """
        return await asyncio.to_thread(
            cls._extract_sync,
            image_bytes,
            page_number,
            lang,
            is_scanned,
        )

    @classmethod
    def _extract_sync(
        cls,
        image_bytes: bytes,
        page_number: int = 1,
        lang: str = "en",
        is_scanned: bool = True,
    ) -> PageExtraction:
        """
        Synchronous OCR worker running in a worker thread.
        """
        img = ImageService.load_image_from_bytes(image_bytes)
        preprocessed = ImageService.preprocess_image(img, as_3channel=True)

        ocr_lines: List[OCRLine] = []
        confidences: List[float] = []

        engine = cls._get_engine(lang)

        if engine is not None:
            try:
                # PaddleOCR predict
                result = engine.ocr(preprocessed)
                if result and len(result) > 0 and result[0] is not None:
                    for item in result[0]:
                        if len(item) >= 2:
                            bbox = item[0]  # list of 4 points [[x1,y1], [x2,y2], ...]
                            text_info = item[1]  # (text, confidence)
                            if isinstance(text_info, (tuple, list)) and len(text_info) >= 2:
                                text = str(text_info[0]).strip()
                                conf = float(text_info[1])
                            else:
                                text = str(text_info).strip()
                                conf = 0.90

                            if text:
                                ocr_lines.append(
                                    OCRLine(
                                        text=text,
                                        confidence=round(conf, 4),
                                        bbox=bbox if isinstance(bbox, list) else None,
                                    )
                                )
                                confidences.append(conf)
            except Exception as e:
                logger.error(f"PaddleOCR execution error on page {page_number}: {e}")

        # If engine was None or returned 0 lines, try PyMuPDF fallback
        if not ocr_lines:
            fallback_text = cls._fallback_ocr(image_bytes)
            if fallback_text:
                for line in fallback_text.splitlines():
                    clean_line = line.strip()
                    if clean_line:
                        ocr_lines.append(OCRLine(text=clean_line, confidence=0.85))
                        confidences.append(0.85)

        # Sort OCR lines according to natural geometric reading order
        sorted_lines = cls._sort_lines_reading_order(ocr_lines)
        full_text_lines = [l.text for l in sorted_lines]

        avg_confidence = float(np.mean(confidences)) if confidences else (1.0 if not is_scanned else 0.0)
        combined_text = "\n".join(full_text_lines)

        return PageExtraction(
            page_number=page_number,
            text=combined_text,
            confidence=round(avg_confidence, 4),
            lines=sorted_lines,
            is_scanned=is_scanned,
        )

    @staticmethod
    def _sort_lines_reading_order(lines: List[OCRLine]) -> List[OCRLine]:
        """
        Sorts OCR lines in natural reading order (top-to-bottom, left-to-right).
        """
        if not lines or len(lines) <= 1:
            return lines

        # If bounding boxes are missing, preserve order
        if any(not l.bbox or len(l.bbox) < 4 for l in lines):
            return lines

        def get_bbox_stats(line: OCRLine):
            pts = np.array(line.bbox)
            ymin = np.min(pts[:, 1])
            ymax = np.max(pts[:, 1])
            xmin = np.min(pts[:, 0])
            height = max(ymax - ymin, 1.0)
            ycenter = (ymin + ymax) / 2.0
            return ycenter, xmin, height

        stats = [get_bbox_stats(l) for l in lines]
        median_height = float(np.median([s[2] for s in stats])) if stats else 20.0
        row_threshold = max(median_height * 0.6, 10.0)

        # Sort by vertical center first
        indexed_lines = sorted(
            enumerate(lines),
            key=lambda item: stats[item[0]][0]
        )

        # Group lines into rows based on row_threshold
        rows: List[List[Tuple[int, OCRLine]]] = []
        for idx, line in indexed_lines:
            y_center = stats[idx][0]
            placed = False
            for row in rows:
                row_y_center = np.mean([stats[i][0] for i, _ in row])
                if abs(y_center - row_y_center) < row_threshold:
                    row.append((idx, line))
                    placed = True
                    break
            if not placed:
                rows.append([(idx, line)])

        # Sort each row horizontally (xmin)
        result: List[OCRLine] = []
        for row in rows:
            sorted_row = sorted(row, key=lambda item: stats[item[0]][1])
            result.extend([line for _, line in sorted_row])

        return result

    @staticmethod
    def _fallback_ocr(image_bytes: bytes) -> str:
        """
        Fallback OCR using PyMuPDF pixmap/document OCR if available.
        """
        try:
            import pymupdf
            doc = pymupdf.open()
            img_doc = pymupdf.open("png", image_bytes)
            pdfbytes = img_doc.convert_to_pdf()
            img_doc.close()
            doc = pymupdf.open("pdf", pdfbytes)
            page = doc[0]
            try:
                tp = page.get_textpage_ocr(flags=0, dpi=300)
                text = tp.extractText()
                doc.close()
                return text.strip()
            except Exception:
                text = page.get_text("text").strip()
                doc.close()
                return text
        except Exception:
            return ""
