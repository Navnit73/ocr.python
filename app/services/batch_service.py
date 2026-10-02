"""
Batch Processing Service for Multi-Document and Zip Archive Extraction.
"""

import asyncio
import io
import time
import zipfile
from typing import List, Optional
from fastapi import HTTPException, UploadFile, status

from app.core.config import get_settings
from app.schemas.batch import BatchExtractionResponse, BatchItemResult
from app.schemas.ocr import DocumentTypeEnum, ExtractionStatus
from app.services.file_validator import FileValidator
from app.services.pipeline import ExtractionPipeline


class BatchService:
    """Processes multiple uploaded documents or zip archives concurrently with failure isolation."""

    @classmethod
    async def process_batch(
        cls,
        files: List[UploadFile],
        document_type: str = DocumentTypeEnum.AUTO.value,
        language: str = "en",
        clean_with_ai: bool = True,
        owner_hash: Optional[str] = None,
        user_email: Optional[str] = None,
        pipeline: Optional[ExtractionPipeline] = None,
    ) -> BatchExtractionResponse:
        """
        Extracts up to settings.max_batch_files documents concurrently.
        Supports both individual files and expanding .zip archives.
        """
        settings = get_settings()
        start_time = time.perf_counter()
        batch_id = f"batch_{FileValidator.generate_or_sanitize_request_id()[4:]}"
        pipeline = pipeline or ExtractionPipeline()
        normalized_email = (user_email or "guest").lower().strip()

        # 1. Unpack any .zip files into in-memory UploadFile objects
        unpacked_files: List[UploadFile] = []
        for f in files:
            fname = f.filename or "upload"
            if fname.lower().endswith(".zip"):
                zip_bytes = await f.read()
                try:
                    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
                        for member_name in z.namelist():
                            if member_name.startswith("__MACOSX") or member_name.endswith("/"):
                                continue
                            member_bytes = z.read(member_name)
                            # Create an in-memory UploadFile wrapper
                            simulated_file = UploadFile(
                                filename=member_name,
                                file=io.BytesIO(member_bytes),
                                size=len(member_bytes),
                            )
                            unpacked_files.append(simulated_file)
                except Exception as e:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Failed to unpack zip archive '{fname}': {str(e)}"
                    )
            else:
                unpacked_files.append(f)

        if not unpacked_files:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No valid files provided for batch processing."
            )

        if len(unpacked_files) > settings.max_batch_files:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Batch size ({len(unpacked_files)} files) exceeds maximum allowed limit of {settings.max_batch_files} files."
            )

        # 2. Process with bounded concurrency (3 concurrent document pipelines)
        semaphore = asyncio.Semaphore(3)

        async def _process_single(file_obj: UploadFile) -> BatchItemResult:
            t0 = time.perf_counter()
            f_name = file_obj.filename or "document"
            async with semaphore:
                try:
                    res = await pipeline.process_document(
                        file=file_obj,
                        document_type=document_type,
                        language=language,
                        clean_with_ai=clean_with_ai,
                        owner_hash=owner_hash,
                        user_email=normalized_email,
                    )
                    dur_ms = int((time.perf_counter() - t0) * 1000)
                    return BatchItemResult(
                        id=res.id,
                        filename=f_name,
                        status=res.status,
                        document_type=res.document_type,
                        extraction=res.extraction,
                        raw_text=res.raw_text[:200] if res.raw_text else "",
                        warnings=res.warnings,
                        processing_time_ms=dur_ms,
                    )
                except Exception as ex:
                    dur_ms = int((time.perf_counter() - t0) * 1000)
                    return BatchItemResult(
                        id=FileValidator.generate_or_sanitize_request_id(),
                        filename=f_name,
                        status=ExtractionStatus.FAILED,
                        document_type="unknown",
                        extraction=None,
                        error=str(ex),
                        processing_time_ms=dur_ms,
                    )

        results = await asyncio.gather(*[_process_single(f) for f in unpacked_files])

        # 3. Calculate batch aggregates
        successful = [r for r in results if r.status in [ExtractionStatus.SUCCESS, ExtractionStatus.PARTIAL_SUCCESS]]
        failed = [r for r in results if r.status == ExtractionStatus.FAILED]

        total_inflow = 0.0
        total_outflow = 0.0

        for r in successful:
            if r.extraction:
                # Bank statement
                for t in r.extraction.get("transactions", []):
                    total_inflow += float(t.get("credit") or 0.0)
                    total_outflow += float(t.get("debit") or 0.0)
                # Invoice / Receipt
                if not r.extraction.get("transactions"):
                    tot = r.extraction.get("total")
                    if tot is not None:
                        total_outflow += float(tot)

        total_duration = int((time.perf_counter() - start_time) * 1000)

        return BatchExtractionResponse(
            batch_id=batch_id,
            total_files=len(results),
            successful_count=len(successful),
            failed_count=len(failed),
            total_processing_time_ms=total_duration,
            consolidated_inflow=round(total_inflow, 2),
            consolidated_outflow=round(total_outflow, 2),
            net_consolidated_savings=round(total_inflow - total_outflow, 2),
            items=results,
        )
