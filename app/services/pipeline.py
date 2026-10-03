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
        user_email: Optional[str] = None,
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
        effective_doc_type = document_type
        ai_cleaned_flag = False
        part_extractions: List[Dict[str, Any]] = []
        part_cleaned_texts: List[str] = []
        settings = get_settings()
        chunk_size = settings.pdf_chunk_size  # Default: 10 pages per part

        # 2. Multi-Part 10-Page Chunked Extraction Pipeline
        t_ocr_start = time.perf_counter()
        t_ai_clean_ms = 0
        t_struct_extract_ms = 0
        t_classify_ms = 0

        if doc_category == "pdf":
            total_pages = await PDFService.get_pdf_page_count_async(file_bytes, password=password)
            num_parts = max(1, (total_pages + chunk_size - 1) // chunk_size)
            ocr_concurrency = min(settings.worker_concurrency, 8)
            ocr_semaphore = asyncio.Semaphore(ocr_concurrency)

            await self._report_progress(
                progress_callback,
                processed_pages=0,
                total_pages=total_pages,
                stage="ocr_extraction",
                progress_pct=10,
                message=f"Starting extraction for {total_pages} pages in {num_parts} part{'s' if num_parts > 1 else ''} (10 pages per part)",
            )

            for part_idx in range(1, num_parts + 1):
                start_page = (part_idx - 1) * chunk_size + 1
                end_page = min(part_idx * chunk_size, total_pages)
                part_pages_count = end_page - start_page + 1

                # Report Part Start
                current_processed = len(pages)
                pct_start = int(10 + (45 * (current_processed / max(1, total_pages))))
                await self._report_progress(
                    progress_callback,
                    processed_pages=current_processed,
                    total_pages=total_pages,
                    stage="ocr_extraction",
                    progress_pct=pct_start,
                    message=f"Processing Part {part_idx} of {num_parts} (Pages {start_page}-{end_page})",
                )

                # Process ONLY pages in this 10-page chunk (avoids loading 200 pages into memory)
                chunk_page_results = await PDFService.process_pdf_chunk_async(
                    pdf_bytes=file_bytes,
                    start_page=start_page,
                    end_page=end_page,
                    password=password,
                )

                async def _process_chunk_page(presult) -> PageExtraction:
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

                    return res

                # Extract OCR / digital text for pages in this chunk
                part_pages = list(await asyncio.gather(*[_process_chunk_page(p) for p in chunk_page_results]))
                pages.extend(part_pages)

                is_chunk_scanned = any(p.is_scanned for p in part_pages)
                if is_chunk_scanned:
                    ocr_used = True
                    ocr_engine_name = "paddleocr"

                # Text extracted from this 10-page part
                part_raw_text = "\n\n".join(p.text for p in part_pages if p.text).strip()

                # Document Classification on Part 1 if AUTO
                if (effective_doc_type == DocumentTypeEnum.AUTO.value or not effective_doc_type) and part_idx == 1:
                    t_cls_start = time.perf_counter()
                    if part_raw_text:
                        effective_doc_type = await self.classifier.classify_document(part_raw_text)
                    else:
                        effective_doc_type = DocumentTypeEnum.GENERAL.value
                    t_classify_ms += int((time.perf_counter() - t_cls_start) * 1000)

                # AI Processing for this part (Optimized: Digital Fast-Path & Parallel Execution)
                part_clean_text = part_raw_text
                if clean_with_ai and part_raw_text:
                    if not is_chunk_scanned:
                        # Digital PDF Fast-Path: Text is 100% digital vector text with no OCR noise.
                        # Whitespace normalization is instantaneous (0.1ms), avoiding redundant LLM cleaning roundtrip.
                        part_clean_text = AICleaner.normalize_whitespace(part_raw_text)
                        ai_cleaned_flag = self.client.is_configured()

                        t_ext_start = time.perf_counter()
                        part_struct, extract_warnings = await self.extractor.extract(
                            text=part_clean_text,
                            document_type=effective_doc_type,
                        )
                        all_warnings.extend(extract_warnings)
                        if part_struct:
                            part_extractions.append(part_struct)
                        t_struct_extract_ms += int((time.perf_counter() - t_ext_start) * 1000)
                    else:
                        # Scanned PDF: Run text cleaning and structured entity extraction concurrently in parallel
                        t_ai_start = time.perf_counter()
                        clean_task = self.ai_cleaner.clean_ocr_text(part_raw_text)
                        extract_task = self.extractor.extract(text=part_raw_text, document_type=effective_doc_type)

                        (c_text, clean_warnings, _), (part_struct, extract_warnings) = await asyncio.gather(
                            clean_task, extract_task
                        )

                        part_clean_text = c_text
                        all_warnings.extend(clean_warnings)
                        all_warnings.extend(extract_warnings)
                        if part_struct:
                            part_extractions.append(part_struct)
                        ai_cleaned_flag = self.client.is_configured()
                        t_elapsed = int((time.perf_counter() - t_ai_start) * 1000)
                        t_ai_clean_ms += t_elapsed
                        t_struct_extract_ms += t_elapsed
                else:
                    part_clean_text = AICleaner.normalize_whitespace(part_raw_text)
                    if part_raw_text:
                        t_ext_start = time.perf_counter()
                        part_struct, extract_warnings = self.extractor._heuristic_fallback(
                            text=part_clean_text,
                            document_type=effective_doc_type,
                        )
                        all_warnings.extend(extract_warnings)
                        if part_struct:
                            part_extractions.append(part_struct)
                        t_struct_extract_ms += int((time.perf_counter() - t_ext_start) * 1000)

                part_cleaned_texts.append(part_clean_text)

                # Progress after completing this 10-page part
                current_processed = len(pages)
                pct_part_done = int(10 + (50 * (current_processed / max(1, total_pages))))
                await self._report_progress(
                    progress_callback,
                    processed_pages=current_processed,
                    total_pages=total_pages,
                    stage="ocr_extraction",
                    progress_pct=pct_part_done,
                    message=f"Completed Part {part_idx} of {num_parts} ({current_processed}/{total_pages} pages processed)",
                )

                # Explicitly release references
                chunk_page_results = None
                part_pages = None

        else:
            # Direct Image Upload (1 page)
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
            part_raw_text = page_extraction.text

            if effective_doc_type == DocumentTypeEnum.AUTO.value or not effective_doc_type:
                t_cls_start = time.perf_counter()
                effective_doc_type = await self.classifier.classify_document(part_raw_text)
                t_classify_ms = int((time.perf_counter() - t_cls_start) * 1000)

            part_clean_text = part_raw_text
            if clean_with_ai and part_raw_text:
                t_ai_start = time.perf_counter()
                clean_task = self.ai_cleaner.clean_ocr_text(part_raw_text)
                extract_task = self.extractor.extract(text=part_raw_text, document_type=effective_doc_type)

                (c_text, clean_warnings, _), (part_struct, extract_warnings) = await asyncio.gather(
                    clean_task, extract_task
                )

                part_clean_text = c_text
                all_warnings.extend(clean_warnings)
                all_warnings.extend(extract_warnings)
                if part_struct:
                    part_extractions.append(part_struct)
                ai_cleaned_flag = self.client.is_configured()
                t_elapsed = int((time.perf_counter() - t_ai_start) * 1000)
                t_ai_clean_ms = t_elapsed
                t_struct_extract_ms = t_elapsed
            else:
                part_clean_text = AICleaner.normalize_whitespace(part_raw_text)
                if part_clean_text or part_raw_text:
                    t_ext_start = time.perf_counter()
                    part_struct, extract_warnings = self.extractor._heuristic_fallback(
                        text=part_clean_text or part_raw_text,
                        document_type=effective_doc_type,
                    )
                    all_warnings.extend(extract_warnings)
                    if part_struct:
                        part_extractions.append(part_struct)
                    t_struct_extract_ms = int((time.perf_counter() - t_ext_start) * 1000)

            part_cleaned_texts.append(part_clean_text)

            await self._report_progress(
                progress_callback,
                processed_pages=1,
                total_pages=1,
                stage="ocr_extraction",
                progress_pct=60,
                message="Image OCR completed",
            )

        stage_timings["ocr_extraction"] = int((time.perf_counter() - t_ocr_start) * 1000)
        if t_classify_ms > 0:
            stage_timings["classification"] = t_classify_ms
        if t_ai_clean_ms > 0:
            stage_timings["ai_cleaning"] = t_ai_clean_ms

        # Assemble Full Document Text & Consolidate Multi-Part Extractions
        raw_text = "\n\n".join(p.text for p in pages if p.text).strip()
        cleaned_text = "\n\n".join(t for t in part_cleaned_texts if t).strip() if part_cleaned_texts else raw_text

        if not raw_text:
            all_warnings.append(
                ExtractionWarning(
                    code="LOW_TEXT_YIELD",
                    message="Little or no text could be extracted from the document.",
                )
            )

        # Merge Structured Data from All 10-Page Parts
        await self._report_progress(
            progress_callback,
            processed_pages=total_pages,
            total_pages=total_pages,
            stage="structured_extraction",
            progress_pct=85,
            message="Consolidating structured financial entities and transactions",
        )
        t0_merge = time.perf_counter()
        structured_data, merge_warnings = self.extractor.merge_extractions(
            extractions=part_extractions,
            document_type=effective_doc_type,
        )
        all_warnings.extend(merge_warnings)
        t_struct_extract_ms += int((time.perf_counter() - t0_merge) * 1000)
        stage_timings["structured_extraction"] = t_struct_extract_ms

        total_time_ms = int((time.perf_counter() - start_time) * 1000)
        normalized_email = (user_email or "guest").lower().strip()

        metadata = ProcessingMetadata(
            pages=len(pages),
            ocr_used=ocr_used,
            ocr_engine=ocr_engine_name if ocr_used else "pymupdf_digital",
            ai_cleaned=ai_cleaned_flag,
            ai_model=settings.deepseek_model if ai_cleaned_flag else None,
            processing_time_ms=total_time_ms,
            stage_timings_ms=stage_timings,
            user_email=normalized_email,
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
            user_email=normalized_email,
        )

        # 6. Persistence: Store in ResultCache, DocumentRepository, and shared ExtractionRepository
        ResultCache.set(req_id, response_obj.model_dump(), owner_hash=owner_hash)

        try:
            await DocumentRepository.save_document({
                "document_id": req_id,
                "job_id": job_id,
                "user_id": owner_hash,
                "user_email": normalized_email,
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
