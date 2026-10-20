"""
OCR Service implementing PaddleOCR with non-blocking async execution and reading-order sorting.
Falls back to RapidOCR (ONNX runtime, bundled models) when PaddleOCR or its paddlepaddle runtime is unavailable.
"""

import asyncio
import logging
import threading
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
    _rapid_engine: Optional[object] = None
    _rapid_unavailable = False
    _rapid_lock = threading.Lock()

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
                for text, conf, bbox in cls._paddle_lines(engine.ocr(preprocessed)):
                    ocr_lines.append(OCRLine(text=text, confidence=round(conf, 4), bbox=bbox))
                    confidences.append(conf)
            except Exception as e:
                logger.error(f"PaddleOCR execution error on page {page_number}: {e}")

        # PaddleOCR missing (e.g. no paddlepaddle runtime) or empty: use RapidOCR on the original image
        if not ocr_lines:
            for text, conf, bbox in cls._rapid_lines(image_bytes, page_number):
                ocr_lines.append(OCRLine(text=text, confidence=round(conf, 4), bbox=bbox))
                confidences.append(conf)

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
        # One output line per visual row keeps table cells (date, description, amounts) together for extraction.
        full_text_lines = cls._row_texts(sorted_lines)

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
    def _paddle_lines(result) -> List[Tuple[str, float, Optional[list]]]:
        """Normalizes PaddleOCR 2.x ([[bbox, (text, conf)], ...]) and 3.x ({rec_texts, rec_scores, rec_polys}) output."""
        lines: List[Tuple[str, float, Optional[list]]] = []
        if not result or result[0] is None:
            return lines
        page = result[0]
        if hasattr(page, "get") and page.get("rec_texts") is not None:
            polys = page.get("rec_polys")
            polys = [] if polys is None else polys
            for i, (text, conf) in enumerate(zip(page.get("rec_texts") or [], page.get("rec_scores") or [])):
                bbox = np.asarray(polys[i]).tolist() if i < len(polys) else None
                if str(text).strip():
                    lines.append((str(text).strip(), float(conf), bbox))
            return lines
        for item in page:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            bbox, text_info = item[0], item[1]
            if isinstance(text_info, (tuple, list)) and len(text_info) >= 2:
                text, conf = str(text_info[0]).strip(), float(text_info[1])
            else:
                text, conf = str(text_info).strip(), 0.90
            if text:
                lines.append((text, conf, bbox if isinstance(bbox, list) else None))
        return lines

    @classmethod
    def _get_rapid_engine(cls):
        if cls._rapid_engine is None and not cls._rapid_unavailable:
            with cls._rapid_lock:
                if cls._rapid_engine is None and not cls._rapid_unavailable:
                    try:
                        from rapidocr import RapidOCR

                        cls._rapid_engine = RapidOCR()
                        logger.info("RapidOCR fallback engine initialized.")
                    except Exception as e:
                        cls._rapid_unavailable = True
                        logger.warning(f"RapidOCR fallback unavailable: {e}")
        return cls._rapid_engine

    @classmethod
    def _rapid_lines(cls, image_bytes: bytes, page_number: int) -> List[Tuple[str, float, Optional[list]]]:
        engine = cls._get_rapid_engine()
        if engine is None:
            return []
        try:
            out = engine(image_bytes)
        except Exception as e:
            logger.error(f"RapidOCR execution error on page {page_number}: {e}")
            return []
        texts, scores = getattr(out, "txts", None) or (), getattr(out, "scores", None) or ()
        boxes = getattr(out, "boxes", None)
        lines = []
        for i, (text, conf) in enumerate(zip(texts, scores)):
            bbox = np.asarray(boxes[i]).tolist() if boxes is not None and i < len(boxes) else None
            if str(text).strip():
                lines.append((str(text).strip(), float(conf), bbox))
        return lines

    @staticmethod
    def _row_texts(sorted_lines: List[OCRLine]) -> List[str]:
        """Joins reading-ordered lines that share a visual row with a column gap; lines without boxes stay as-is."""
        if any(not l.bbox or len(l.bbox) < 4 for l in sorted_lines):
            return [l.text for l in sorted_lines]
        rows: List[List[OCRLine]] = []
        last_y = None
        heights = [max(np.ptp(np.array(l.bbox)[:, 1]), 1.0) for l in sorted_lines]
        threshold = max(float(np.median(heights)) * 0.6, 10.0) if heights else 10.0
        for line in sorted_lines:
            y = float(np.mean(np.array(line.bbox)[:, 1]))
            if rows and last_y is not None and abs(y - last_y) < threshold:
                rows[-1].append(line)
            else:
                rows.append([line])
                last_y = y
        return ["   ".join(l.text for l in row) for row in rows]

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
