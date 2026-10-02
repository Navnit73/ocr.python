"""
End-to-End Stateless OCR & Extraction Pipeline Orchestrator.
Supports large multi-page PDF streaming (up to 200 pages), concurrent OCR chunking,
progress callbacks for real-time frontend notifications, and MongoDB persistence.
"""

import asyncio
import gc
import logging
import time
from typing import Any, Callable, Coroutine, List, Optional, Union
from fastapi import UploadFile

from app.core.config import get_settings
from app.db.repositories.document_repo import DocumentRepository
from app.schemas.ocr import (
    DocumentTypeEnum,
    ExtractionResponse,
    ExtractionStatus,
    ExtractionWarning,
    PageExtraction,
    ProcessingMetadata,
)
from app.services.file_validator import FileValidator
from app.services.pdf_service import PDFService
from app.services.ocr_service import OCRService
from app.services.deepseek_client import DeepSeekClient
from app.services.ai_cleaner import AICleaner
from app.services.classifier import DocumentClassifier
from app.services.extractor import StructuredExtractor
from app.services.result_cache import ResultCache

logger = logging.getLogger("pipeline")

# Type alias for progress callback
ProgressCallbackType = Callable[[int, int, str, int, str], Coroutine[Any, Any, None]]


class ExtractionPipeline:
    """Orchestrates the 3-Layer stateless OCR extraction pipeline with live progress tracking."""

    def __init__(
        self,
        deepseek_client: Optional[DeepSeekClient] = None,
    ):
        self.client = deepseek_client or DeepSeekClient()
        self.ai_cleaner = AICleaner(self.client)
        self.classifier = DocumentClassifier(self.client)
        self.extractor = StructuredExtractor(self.client)

    async def _report_progress(
        self,
        callback: Optional[ProgressCallbackType],
        processed_pages: int,
        total_pages: int,
        stage: str,
        progress_pct: int,
        message: str,
    ) -> None:
        """Helper to invoke progress callback safely without breaking execution."""
        if callback:
            try:
                await callback(processed_pages, total_pages, stage, progress_pct, message)
            except Exception as e:
                logger.warning(f"Error in progress callback: {e}")

    async def process_document(
        self,
        file: Optional[UploadFile] = None,
        file_bytes: Optional[bytes] = None,
        filename: Optional[str] = None,
        document_type: str = DocumentTypeEnum.AUTO.value,
        language: str = "en",
        clean_with_ai: bool = True,
        client_request_id: Optional[str] = None,
        password: Optional[str] = None,
        owner_hash: Optional[str] = None,
        job_id: Optional[str] = None,
        progress_callback: Optional[ProgressCallbackType] = None,
    ) -> ExtractionResponse:
        """
        Executes the full extraction pipeline for an uploaded file or raw file bytes.
        Supports up to 200-page documents with fine-grained progress updates.
        """
        start_time = time.perf_counter()
        stage_timings: dict[str, int] = {}
        all_warnings: List[ExtractionWarning] = []

        # 1. Validation & ID generation
        t0 = time.perf_counter()
        req_id = FileValidator.generate_or_sanitize_request_id(client_request_id)
        effective_filename = filename or (file.filename if file else "document.pdf")

        if file_bytes is None:
            if file is None:
                raise ValueError("Either 'file' or 'file_bytes' must be provided to process_document.")
            doc_category, file_bytes = await FileValidator.validate_upload(file)
        else:
            doc_category = FileValidator.detect_category_from_bytes(file_bytes, effective_filename)
            FileValidator.validate_bytes_and_size(file_bytes, effective_filename)

        stage_timings["validation"] = int((time.perf_counter() - t0) * 1000)

        await self._report_progress(
            progress_callback,
            processed_pages=0,
            total_pages=1,
            stage="validation",
            progress_pct=5,
            message="Document validated successfully",
        )

        pages: List[PageExtraction] = []
        ocr_used = False
        ocr_engine_name = "pymupdf_digital"
        total_pages = 1

        # 2. Text Extraction & OCR
        t0 = time.perf_counter()
        if doc_category == "pdf":
            page_results, total_pages = PDFService.process_pdf(file_bytes, password=password)
            settings = get_settings()
            ocr_concurrency = min(settings.worker_concurrency, 8)
            ocr_semaphore = asyncio.Semaphore(ocr_concurrency)
            processed_count = 0

            await self._report_progress(
                progress_callback,
                processed_pages=0,
                total_pages=total_pages,
                stage="ocr_extraction",
                progress_pct=10,
                message=f"Starting extraction for {total_pages} pages",
            )

            async def _process_page(presult) -> PageExtraction:
                nonlocal processed_count
                try:
                    if not presult.is_scanned:
                        res = PageExtraction(
                            page_number=presult.page_number,
                            text=presult.text,
                            confidence=1.0,
                            is_scanned=False,
                        )
                    else:
                        async with ocr_semaphore:
                            res = await OCRService.extract_from_image_bytes(
                                image_bytes=presult.image_bytes,
                                page_number=presult.page_number,
                                lang=language,
                                is_scanned=True,
                            )
                except Exception as e:
                    logger.error(f"Error processing page {presult.page_number}: {e}")
                    all_warnings.append(
                        ExtractionWarning(
                            code="PAGE_EXTRACTION_ERROR",
                            message=f"Page {presult.page_number} encountered an error: {str(e)}",
                            severity="warning",
                        )
                    )
                    res = PageExtraction(
                        page_number=presult.page_number,
                        text=presult.text or "",
                        confidence=0.0,
                        is_scanned=presult.is_scanned,
                    )

                processed_count += 1
                # Calculate progress from 10% to 55% during OCR phase
                pct = int(10 + (45 * (processed_count / max(1, total_pages))))
                await self._report_progress(
                    progress_callback,
                    processed_pages=processed_count,
                    total_pages=total_pages,
                    stage="ocr_extraction",
                    progress_pct=pct,
                    message=f"Extracted page {processed_count} of {total_pages}",
                )
                return res

            # Process all pages with bounded concurrency preserving order
            pages = list(await asyncio.gather(*[_process_page(p) for p in page_results]))
            ocr_used = any(p.is_scanned for p in pages)
            if ocr_used:
                ocr_engine_name = "paddleocr"
        else:
            # Direct Image upload
            ocr_used = True
            ocr_engine_name = "paddleocr"
            total_pages = 1
            await self._report_progress(
                progress_callback,
                processed_pages=0,
                total_pages=1,
                stage="ocr_extraction",
                progress_pct=25,
                message="Running OCR on image",
            )
            page_extraction = await OCRService.extract_from_image_bytes(
                image_bytes=file_bytes,
                page_number=1,
                lang=language,
                is_scanned=True,
            )
            pages = [page_extraction]
            await self._report_progress(
                progress_callback,
                processed_pages=1,
                total_pages=1,
                stage="ocr_extraction",
                progress_pct=55,
                message="Image OCR completed",
            )

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
            await self._report_progress(
                progress_callback,
                processed_pages=total_pages,
                total_pages=total_pages,
                stage="ai_cleaning",
                progress_pct=65,
                message="Cleaning text and normalizing characters with AI",
            )
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
            await self._report_progress(
                progress_callback,
                processed_pages=total_pages,
                total_pages=total_pages,
                stage="classification",
                progress_pct=75,
                message="Classifying document type",
            )
            t0 = time.perf_counter()
            effective_doc_type = await self.classifier.classify_document(cleaned_text or raw_text)
            stage_timings["classification"] = int((time.perf_counter() - t0) * 1000)

        # 5. Layer 3: Structured Extraction
        await self._report_progress(
            progress_callback,
            processed_pages=total_pages,
            total_pages=total_pages,
            stage="structured_extraction",
            progress_pct=85,
            message="Extracting structured financial entities and transactions",
        )
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

        # 6. Persistence: Store in ResultCache and MongoDB DocumentRepository
        ResultCache.set(req_id, response_obj.model_dump(), owner_hash=owner_hash)

        try:
            await DocumentRepository.save_document({
                "document_id": req_id,
                "job_id": job_id,
                "user_id": owner_hash,
                "filename": effective_filename,
                "content_type": "application/pdf" if doc_category == "pdf" else "image/png",
                "file_size_bytes": len(file_bytes),
                "document_type": effective_doc_type,
                "status": response_obj.status.value,
                "pages_count": len(pages),
                "extraction": structured_data,
                "raw_text": raw_text,
                "cleaned_text": cleaned_text,
                "pages": [p.model_dump() for p in pages],
                "metadata": metadata.model_dump(),
                "warnings": [w.model_dump() for w in all_warnings],
            })
        except Exception as e:
            logger.warning(f"Could not persist document {req_id} to MongoDB: {e}")

        # Trigger garbage collection after large document processing
        if total_pages > 10:
            gc.collect()

        await self._report_progress(
            progress_callback,
            processed_pages=total_pages,
            total_pages=total_pages,
            stage="completed",
            progress_pct=100,
            message="Processing completed successfully",
        )

        return response_obj
