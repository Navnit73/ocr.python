"""
Intelligent Document Extraction Router.
Selects optimal extraction engine (PyMuPDF, PaddleOCR, IBM Docling, or Auto) with deterministic rules and automatic fallback.
"""

import asyncio
import logging
import time
from typing import Any, Dict, Optional, Tuple

import pymupdf

from app.core.config import get_settings
from app.schemas.ocr import ExtractionEngineEnum, ExtractionWarning
from app.services.docling_engine import DoclingEngine
from app.services.document_normalizer import DocumentNormalizer
from app.services.engine_interface import BaseExtractionEngine, EngineResult
from app.services.paddleocr_engine import PaddleOCREngine
from app.services.pymupdf_engine import PyMuPDFEngine

logger = logging.getLogger("extraction_router")


class ExtractionRouter:
    """Intelligent router selecting between PyMuPDF, PaddleOCR, and IBM Docling."""

    def __init__(self):
        self._pymupdf_engine = PyMuPDFEngine()
        self._paddleocr_engine = PaddleOCREngine()
        self._docling_engine = DoclingEngine.get_instance()

    def get_engine(self, engine_name: str) -> BaseExtractionEngine:
        """Resolves engine instance by name."""
        name = (engine_name or "").lower().strip()
        if name in (ExtractionEngineEnum.DOCLING.value, "docling"):
            return self._docling_engine
        elif name in (ExtractionEngineEnum.PADDLEOCR.value, "paddleocr"):
            return self._paddleocr_engine
        elif name in (ExtractionEngineEnum.PYMUPDF.value, "pymupdf"):
            return self._pymupdf_engine
        else:
            return self._docling_engine

    def select_engine_auto(
        self,
        file_bytes: bytes,
        filename: str,
        doc_category: str = "pdf",
        password: Optional[str] = None,
        extract_tables: bool = True,
    ) -> Tuple[BaseExtractionEngine, str]:
        """
        Determines the optimal extraction engine based on deterministic document inspection.
        Returns (engine_instance, reason_string).
        """
        settings = get_settings()

        # If Docling is disabled in settings, route between PyMuPDF and PaddleOCR
        if not getattr(settings, "docling_enabled", True):
            if doc_category == "image":
                return self._paddleocr_engine, "Docling disabled; routing image to PaddleOCR."
            return self._pymupdf_engine, "Docling disabled; routing PDF to PyMuPDF."

        # 1. Direct Image Files
        if doc_category == "image":
            return self._docling_engine, "Routing image to Docling for layout and OCR extraction."

        # 2. PDF Document Inspection
        try:
            doc = pymupdf.open(stream=file_bytes, filetype="pdf")
            if doc.is_encrypted or doc.needs_pass:
                if password:
                    doc.authenticate(password)
            
            total_pages = len(doc)
            if total_pages == 0:
                doc.close()
                return self._pymupdf_engine, "Empty PDF document."

            sample_pages = min(total_pages, 3)
            total_sample_chars = 0
            has_images = False

            for i in range(sample_pages):
                page = doc[i]
                text = page.get_text("text").strip()
                total_sample_chars += len(text)
                if page.get_images():
                    has_images = True

            doc.close()
            avg_chars_per_page = total_sample_chars / max(1, sample_pages)

            # Scanned PDF (little or no digital text)
            if avg_chars_per_page < 30:
                return self._docling_engine, f"Scanned PDF detected ({avg_chars_per_page:.0f} chars/page). Routing to Docling with OCR."

            # Digital PDF with table extraction requested
            if extract_tables:
                return self._docling_engine, f"Digital PDF with table extraction enabled. Routing to Docling."

            # High-page-count simple digital PDF where PyMuPDF speed is preferred
            if total_pages > 50 and not extract_tables:
                return self._pymupdf_engine, f"Large digital PDF ({total_pages} pages). Routing to PyMuPDF for high throughput."

            return self._docling_engine, "Routing to Docling as primary document extraction engine."

        except Exception as e:
            logger.warning(f"Error inspecting PDF for automatic routing: {e}. Defaulting to Docling.")
            return self._docling_engine, "Inspection fallback to Docling."

    async def route_and_extract(
        self,
        file_bytes: bytes,
        filename: str,
        doc_category: str = "pdf",
        requested_engine: str = ExtractionEngineEnum.AUTO.value,
        language: str = "en",
        password: Optional[str] = None,
        enable_ocr: bool = True,
        extract_tables: bool = True,
        **kwargs: Any,
    ) -> EngineResult:
        """
        Routes the document to the chosen or automatically determined engine with robust fallback.
        """
        t0 = time.perf_counter()
        req_engine_str = (requested_engine or ExtractionEngineEnum.AUTO.value).lower().strip()
        fallback_used = False
        routing_reason = "Explicit engine requested."

        # 1. Engine Selection
        if req_engine_str == ExtractionEngineEnum.AUTO.value:
            selected_engine, routing_reason = self.select_engine_auto(
                file_bytes=file_bytes,
                filename=filename,
                doc_category=doc_category,
                password=password,
                extract_tables=extract_tables,
            )
        else:
            selected_engine = self.get_engine(req_engine_str)

        logger.info(f"⚡ [ExtractionRouter] Selected engine '{selected_engine.engine_name}' for '{filename}' ({routing_reason})")

        # 2. Execution with Automatic Fallback
        try:
            result = await selected_engine.extract_document(
                file_bytes=file_bytes,
                filename=filename,
                doc_category=doc_category,
                language=language,
                password=password,
                enable_ocr=enable_ocr,
                extract_tables=extract_tables,
                **kwargs,
            )
        except Exception as primary_error:
            logger.error(f"❌ Primary engine '{selected_engine.engine_name}' failed: {primary_error}. Attempting fallback...")
            fallback_used = True

            # Determine fallback engine
            if selected_engine.engine_name == "docling":
                fallback_engine = self._paddleocr_engine if doc_category == "image" else self._pymupdf_engine
            elif selected_engine.engine_name == "paddleocr":
                fallback_engine = self._pymupdf_engine
            else:
                fallback_engine = self._paddleocr_engine

            try:
                result = await fallback_engine.extract_document(
                    file_bytes=file_bytes,
                    filename=filename,
                    doc_category=doc_category,
                    language=language,
                    password=password,
                    enable_ocr=enable_ocr,
                    extract_tables=extract_tables,
                    **kwargs,
                )
                result.warnings.append(
                    ExtractionWarning(
                        code="ENGINE_FALLBACK",
                        message=(
                            f"Primary engine '{selected_engine.engine_name}' failed ({str(primary_error)}). "
                            f"Successfully recovered using fallback engine '{fallback_engine.engine_name}'."
                        ),
                        severity="warning",
                    )
                )
            except Exception as fallback_error:
                logger.error(f"❌ Fallback engine '{fallback_engine.engine_name}' also failed: {fallback_error}")
                # Return empty result with structured warning
                result = EngineResult(
                    engine_name="failed",
                    raw_text="",
                    warnings=[
                        ExtractionWarning(
                            code="EXTRACTION_FAILED",
                            message=f"All extraction engines failed. Primary: {str(primary_error)}, Fallback: {str(fallback_error)}",
                            severity="error",
                        )
                    ],
                )

        # 3. Normalize Result
        normalized = DocumentNormalizer.normalize_engine_result(result)
        normalized.metadata["routing_engine"] = req_engine_str
        normalized.metadata["executed_engine"] = normalized.engine_name
        normalized.metadata["routing_reason"] = routing_reason
        normalized.metadata["fallback_used"] = fallback_used
        normalized.metadata["total_routing_time_ms"] = int((time.perf_counter() - t0) * 1000)

        return normalized
