"""
End-to-End Stateless OCR & Extraction Pipeline Orchestrator.
"""

import asyncio
import logging
import time
from typing import List, Optional
from fastapi import UploadFile

from app.core.config import get_settings
from app.schemas.ocr import (
    DocumentTypeEnum,
    ExtractionResponse,
    ExtractionStatus,
    ExtractionWarning,
    PageExtraction,
    ProcessingMetadata,
)
from app.services.file_validator import FileValidator, managed_temp_file
from app.services.pdf_service import PDFService
from app.services.image_service import ImageService
from app.services.ocr_service import OCRService
from app.services.deepseek_client import DeepSeekClient
from app.services.ai_cleaner import AICleaner
from app.services.classifier import DocumentClassifier
from app.services.extractor import StructuredExtractor
from app.services.result_cache import ResultCache

logger = logging.getLogger("pipeline")


class ExtractionPipeline:
    """Orchestrates the 3-Layer stateless OCR extraction pipeline."""

    def __init__(
        self,
        deepseek_client: Optional[DeepSeekClient] = None,
    ):
        self.client = deepseek_client or DeepSeekClient()
        self.ai_cleaner = AICleaner(self.client)
        self.classifier = DocumentClassifier(self.client)
        self.extractor = StructuredExtractor(self.client)

    async def process_document(
        self,
        file: UploadFile,
        document_type: str = DocumentTypeEnum.AUTO.value,
        language: str = "en",
        clean_with_ai: bool = True,
        client_request_id: Optional[str] = None,
        password: Optional[str] = None,
        owner_hash: Optional[str] = None,
    ) -> ExtractionResponse:
        """
        Executes the full extraction pipeline for an uploaded file.
        """
        start_time = time.perf_counter()
        stage_timings: dict[str, int] = {}
        all_warnings: List[ExtractionWarning] = []

        # 1. Validation & ID generation
        t0 = time.perf_counter()
        req_id = FileValidator.generate_or_sanitize_request_id(client_request_id)
        doc_category, file_bytes = await FileValidator.validate_upload(file)
        stage_timings["validation"] = int((time.perf_counter() - t0) * 1000)

        pages: List[PageExtraction] = []
        ocr_used = False
        ocr_engine_name = "pymupdf_digital"

        # 2. Text Extraction & OCR (with concurrent page processing for multi-page documents)
        t0 = time.perf_counter()
        if doc_category == "pdf":
            page_results, total_pages = PDFService.process_pdf(file_bytes, password=password)
            ocr_semaphore = asyncio.Semaphore(4)  # Limit concurrent OCR threads to 4

            async def _process_page(presult) -> PageExtraction:
                if not presult.is_scanned:
                    return PageExtraction(
                        page_number=presult.page_number,
                        text=presult.text,
                        confidence=1.0,
                        is_scanned=False,
                    )
                else:
                    async with ocr_semaphore:
                        return await OCRService.extract_from_image_bytes(
                            image_bytes=presult.image_bytes,
                            page_number=presult.page_number,
                            lang=language,
                            is_scanned=True,
                        )

            # Process all pages concurrently preserving index order
            pages = list(await asyncio.gather(*[_process_page(p) for p in page_results]))
            ocr_used = any(p.is_scanned for p in pages)
            if ocr_used:
                ocr_engine_name = "paddleocr"
        else:
            # Direct Image upload
            ocr_used = True
            ocr_engine_name = "paddleocr"
            page_extraction = await OCRService.extract_from_image_bytes(
                image_bytes=file_bytes,
                page_number=1,
                lang=language,
                is_scanned=True,
            )
            pages = [page_extraction]

        stage_timings["ocr_extraction"] = int((time.perf_counter() - t0) * 1000)

        # Assemble Layer 1: Raw text
        raw_text = "\n\n".join(p.text for p in pages if p.text).strip()
        if not raw_text:
            all_warnings.append(
                ExtractionWarning(
                    code="LOW_TEXT_YIELD",
                    message="Little or no text could be extracted from the document.",
                )
            )

        # 3. Layer 2: AI Cleaning
        cleaned_text = raw_text
        ai_cleaned_flag = False
        settings = get_settings()

        if clean_with_ai and raw_text:
            t0 = time.perf_counter()
            cleaned_text, clean_warnings, _ = await self.ai_cleaner.clean_ocr_text(raw_text)
            all_warnings.extend(clean_warnings)
            ai_cleaned_flag = self.client.is_configured()
            stage_timings["ai_cleaning"] = int((time.perf_counter() - t0) * 1000)
        else:
            cleaned_text = AICleaner.normalize_whitespace(raw_text)

        # 4. Document Classification
        effective_doc_type = document_type
        if document_type == DocumentTypeEnum.AUTO.value or not document_type:
            t0 = time.perf_counter()
            effective_doc_type = await self.classifier.classify_document(cleaned_text or raw_text)
            stage_timings["classification"] = int((time.perf_counter() - t0) * 1000)

        # 5. Layer 3: Structured Extraction
        t0 = time.perf_counter()
        text_for_extraction = cleaned_text if cleaned_text else raw_text
        structured_data, extract_warnings = await self.extractor.extract(
            text=text_for_extraction,
            document_type=effective_doc_type,
        )
        all_warnings.extend(extract_warnings)
        stage_timings["structured_extraction"] = int((time.perf_counter() - t0) * 1000)

        total_time_ms = int((time.perf_counter() - start_time) * 1000)

        metadata = ProcessingMetadata(
            pages=len(pages),
            ocr_used=ocr_used,
            ocr_engine=ocr_engine_name if ocr_used else "pymupdf_digital",
            ai_cleaned=ai_cleaned_flag,
            ai_model=settings.deepseek_model if ai_cleaned_flag else None,
            processing_time_ms=total_time_ms,
            stage_timings_ms=stage_timings,
        )

        response_obj = ExtractionResponse(
            id=req_id,
            status=ExtractionStatus.SUCCESS if raw_text else ExtractionStatus.PARTIAL_SUCCESS,
            document_type=effective_doc_type,
            extraction=structured_data,
            raw_text=raw_text,
            cleaned_text=cleaned_text,
            pages=pages,
            metadata=metadata,
            warnings=all_warnings,
        )

        # Cache in-memory for download export by ID (scoped with owner_hash to prevent IDOR)
        ResultCache.set(req_id, response_obj.model_dump(), owner_hash=owner_hash)

        return response_obj
