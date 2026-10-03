"""
PaddleOCR Extraction Engine.
Provides deep OCR extraction for scanned documents, images, and non-selectable PDFs with bounding-box geometry and reading-order reconstruction.
"""

import asyncio
import logging
import time
from typing import Any, List, Optional
import numpy as np

from app.core.config import get_settings
from app.schemas.ocr import ExtractionWarning, OCRLine, PageExtraction
from app.services.engine_interface import BaseExtractionEngine, EngineResult, ExtractedHeading, ExtractedTable
from app.services.image_service import ImageService
from app.services.ocr_service import OCRService
from app.services.pdf_service import PDFService

logger = logging.getLogger("paddleocr_engine")


class PaddleOCREngine:
    """Extraction engine utilizing PaddleOCR for high-precision scanned and image document OCR."""

    @property
    def engine_name(self) -> str:
        return "paddleocr"

    async def extract_document(
        self,
        file_bytes: bytes,
        filename: str,
        doc_category: str = "pdf",
        language: str = "en",
        password: Optional[str] = None,
        enable_ocr: bool = True,
        extract_tables: bool = True,
        **kwargs: Any,
    ) -> EngineResult:
        """
        Executes PaddleOCR extraction across all pages of a PDF or image.
        """
        return await asyncio.to_thread(
            self._extract_sync,
            file_bytes=file_bytes,
            filename=filename,
            doc_category=doc_category,
            language=language,
            password=password,
        )

    def _extract_sync(
        self,
        file_bytes: bytes,
        filename: str,
        doc_category: str = "pdf",
        language: str = "en",
        password: Optional[str] = None,
    ) -> EngineResult:
        """Synchronous worker thread executing PaddleOCR."""
        t0 = time.perf_counter()
        warnings: List[ExtractionWarning] = []
        pages: List[PageExtraction] = []
        headings: List[ExtractedHeading] = []
        tables: List[ExtractedTable] = []

        try:
            if doc_category == "pdf":
                # Render all pages to high-res images for OCR
                page_results, total_pages = PDFService.process_pdf(
                    pdf_bytes=file_bytes,
                    password=password,
                )
                for presult in page_results:
                    if presult.is_scanned and presult.image_bytes:
                        pe = OCRService._extract_sync(
                            image_bytes=presult.image_bytes,
                            page_number=presult.page_number,
                            lang=language,
                            is_scanned=True,
                        )
                    else:
                        # PyMuPDF extracted digital text
                        pe = PageExtraction(
                            page_number=presult.page_number,
                            text=presult.text,
                            confidence=1.0,
                            is_scanned=False,
                        )
                    pages.append(pe)
            else:
                # Direct image upload
                pe = OCRService._extract_sync(
                    image_bytes=file_bytes,
                    page_number=1,
                    lang=language,
                    is_scanned=True,
                )
                pages.append(pe)

        except Exception as e:
            logger.error(f"PaddleOCR extraction error: {e}", exc_info=True)
            warnings.append(
                ExtractionWarning(
                    code="PADDLEOCR_EXECUTION_ERROR",
                    message=f"PaddleOCR engine encountered an error: {str(e)}",
                    severity="error",
                )
            )

        raw_text = "\n\n".join(p.text for p in pages if p.text).strip()
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        # Detect potential headings based on short uppercase / capitalized lines
        for p in pages:
            for line in p.lines:
                txt = line.text.strip()
                if (txt.isupper() or len(txt.split()) <= 4) and len(txt) > 3 and len(txt) < 60:
                    headings.append(
                        ExtractedHeading(
                            text=txt,
                            level=2,
                            page_number=p.page_number,
                        )
                    )

        confidences = [p.confidence for p in pages if p.confidence > 0]
        avg_conf = float(np.mean(confidences)) if confidences else 0.85

        return EngineResult(
            engine_name=self.engine_name,
            pages=pages,
            raw_text=raw_text,
            markdown=raw_text,
            tables=tables,
            headings=headings,
            is_scanned=True,
            confidence=round(avg_conf, 4),
            warnings=warnings,
            metadata={
                "processing_time_ms": elapsed_ms,
                "total_pages": len(pages),
                "headings_count": len(headings),
            },
        )
