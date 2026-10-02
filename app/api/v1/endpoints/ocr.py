"""
OCR & Extraction API Endpoints with API Key Authentication, Rate Limiting, Async Background Workers & Batch.
"""

from typing import List, Optional, Union
import uuid
from fastapi import APIRouter, Depends, File, Form, Header, Request, Response, UploadFile, status
from pydantic import BaseModel

from app.core.rate_limiter import check_rate_limit
from app.core.security import AuthenticatedClient, verify_api_key
from app.db.repositories.job_repo import JobRepository
from app.schemas.batch import BatchExtractionResponse
from app.schemas.job import JobCreateResponse
from app.schemas.ocr import (
    DocumentTypeEnum,
    ExtractionResponse,
    LanguageEnum,
)
from app.services.batch_service import BatchService
from app.services.file_validator import FileValidator
from app.services.pipeline import ExtractionPipeline
from app.services.storage_service import StorageService
from app.workers.worker_manager import WorkerManager

router = APIRouter(prefix="/ocr", tags=["OCR & Extraction"])


class AsyncAcceptedResponse(BaseModel):
    """Returned when an asynchronous callback_url or async_mode is requested."""
    job_id: str
    document_id: str
    id: str  # Backward compatibility alias
    status: str = "queued"
    message: str
    status_url: str
    callback_url: Optional[str] = None


def get_pipeline() -> ExtractionPipeline:
    """Dependency injector for ExtractionPipeline."""
    return ExtractionPipeline()


@router.post(
    "/extract-async",
    response_model=JobCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload document and queue asynchronous OCR extraction (HTTP 202 Accepted)",
    description=(
        "Upload a document (up to 200 pages / 100MB) for asynchronous background processing.\n\n"
        "Immediately returns HTTP `202 Accepted` with a `job_id`, `document_id`, and `status_url`.\n"
        "The background worker picks up the job and executes OCR, AI cleaning, and structured extraction independently."
    ),
    dependencies=[Depends(check_rate_limit)],
)
async def extract_document_async(
    file: UploadFile = File(..., description="Document file: PDF (up to 200 pages), JPG, PNG, WEBP, or TIFF"),
    document_type: DocumentTypeEnum = Form(
        default=DocumentTypeEnum.AUTO,
        description="Target document type (auto, bank_statement, receipt, invoice, general)",
    ),
    language: LanguageEnum = Form(
        default=LanguageEnum.EN,
        description="OCR language hint (auto, en, hi, es, fr, de, ch)",
    ),
    clean_with_ai: bool = Form(
        default=True,
        description="Whether to clean OCR text and extract structured entities using DeepSeek",
    ),
    request_id: Optional[str] = Form(
        default=None,
        description="Optional unique document ID from frontend",
    ),
    password: Optional[str] = Form(
        default=None,
        description="Optional password for password-protected PDF documents",
    ),
    callback_url: Optional[str] = Form(
        default=None,
        description="Optional webhook URL to receive event notifications upon completion",
    ),
    callback_secret: Optional[str] = Form(
        default=None,
        description="Optional secret key for HMAC-SHA256 signature header (X-Webhook-Signature) on callback POST",
    ),
    user_email: Optional[str] = Form(
        default=None,
        description="Optional user email from logged-in frontend session",
    ),
    x_request_id: Optional[str] = Header(
        default=None,
        alias="X-Request-ID",
        description="Optional custom request ID via HTTP Header",
    ),
    auth_client: AuthenticatedClient = Depends(verify_api_key),
) -> JobCreateResponse:
    """
    Asynchronous upload endpoint: saves document to persistent storage, creates job record, and enqueues worker.
    """
    effective_doc_id = FileValidator.generate_or_sanitize_request_id(request_id or x_request_id)
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    filename = file.filename or "document.pdf"
    normalized_email = (user_email or "guest").lower().strip()

    # Validate
    doc_category, file_bytes = await FileValidator.validate_upload(file)

    # Save to storage
    file_ref, checksum, file_size = await StorageService.save_file(
        file_bytes=file_bytes,
        filename=filename,
        document_id=effective_doc_id,
    )

    # Create job in MongoDB
    job_doc = {
        "job_id": job_id,
        "document_id": effective_doc_id,
        "user_id": auth_client.key_hash,
        "user_email": normalized_email,
        "status": "queued",
        "progress": 0,
        "total_pages": 1,
        "processed_pages": 0,
        "current_stage": "queued",
        "message": "Document uploaded successfully. Processing has started.",
        "file_reference": file_ref,
        "callback_url": callback_url,
        "callback_secret": callback_secret,
        "document_type": document_type.value,
        "language": language.value,
        "clean_with_ai": clean_with_ai,
        "password": password,
        "metadata": {
            "filename": filename,
            "content_type": file.content_type,
            "file_size_bytes": file_size,
            "checksum_sha256": checksum,
            "doc_category": doc_category,
            "user_email": normalized_email,
        },
    }
    await JobRepository.create_job(job_doc)

    # Submit to worker queue
    await WorkerManager.submit_job(job_id, user_email=normalized_email)

    return JobCreateResponse(
        job_id=job_id,
        document_id=effective_doc_id,
        status="queued",
        message="Document uploaded successfully. Processing has started.",
        status_url=f"/api/v1/jobs/{job_id}",
    )


@router.post(
    "/extract",
    response_model=Union[ExtractionResponse, AsyncAcceptedResponse],
    status_code=status.HTTP_200_OK,
    summary="Extract text and structured data from documents (Up to 200 Pages)",
    description=(
        "Upload a bank statement, receipt, invoice, general document, PDF, or image (up to 200 pages / 100MB).\n\n"
        "**Synchronous Mode** (Default): Waits for processing and returns 3-layer JSON.\n"
        "**Async Mode**: If `async_mode=true` or `callback_url` is provided, returns HTTP `202 Accepted` immediately.\n"
        "**Authentication**: Requires valid `X-API-Key` or `Authorization: Bearer <key>`."
    ),
    dependencies=[Depends(check_rate_limit)],
)
async def extract_document(
    response: Response,
    file: UploadFile = File(..., description="Document file: PDF (up to 200 pages), JPG, PNG, WEBP, or TIFF"),
    document_type: DocumentTypeEnum = Form(
        default=DocumentTypeEnum.AUTO,
        description="Target document type (auto, bank_statement, receipt, invoice, general)",
    ),
    language: LanguageEnum = Form(
        default=LanguageEnum.EN,
        description="OCR language hint (auto, en, hi, es, fr, de, ch)",
    ),
    clean_with_ai: bool = Form(
        default=True,
        description="Whether to clean OCR text and extract structured entities using DeepSeek",
    ),
    async_mode: bool = Form(
        default=False,
        description="Whether to execute asynchronously via background workers (returns 202 Accepted)",
    ),
    request_id: Optional[str] = Form(
        default=None,
        description="Optional unique request ID from frontend. Returned in response.",
    ),
    password: Optional[str] = Form(
        default=None,
        description="Optional password for password-protected PDF documents",
    ),
    callback_url: Optional[str] = Form(
        default=None,
        description="Optional webhook URL. If provided, extraction runs asynchronously in background.",
    ),
    callback_secret: Optional[str] = Form(
        default=None,
        description="Optional secret key for HMAC-SHA256 signature header (X-Webhook-Signature) on callback POST.",
    ),
    user_email: Optional[str] = Form(
        default=None,
        description="Optional user email from logged-in frontend session",
    ),
    x_request_id: Optional[str] = Header(
        default=None,
        alias="X-Request-ID",
        description="Optional custom request ID via HTTP Header",
    ),
    auth_client: AuthenticatedClient = Depends(verify_api_key),
    pipeline: ExtractionPipeline = Depends(get_pipeline),
) -> Union[ExtractionResponse, AsyncAcceptedResponse]:
    """
    Extracts text and structured entities. Automatically supports both synchronous and asynchronous workflows.
    """
    effective_request_id = FileValidator.generate_or_sanitize_request_id(request_id or x_request_id)
    normalized_email = (user_email or "guest").lower().strip()

    # If async_mode requested or callback_url provided, route to background worker
    if async_mode or callback_url:
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        filename = file.filename or "document.pdf"
        doc_category, file_bytes = await FileValidator.validate_upload(file)

        file_ref, checksum, file_size = await StorageService.save_file(
            file_bytes=file_bytes,
            filename=filename,
            document_id=effective_request_id,
        )

        job_doc = {
            "job_id": job_id,
            "document_id": effective_request_id,
            "user_id": auth_client.key_hash,
            "user_email": normalized_email,
            "status": "queued",
            "progress": 0,
            "total_pages": 1,
            "processed_pages": 0,
            "current_stage": "queued",
            "message": "Document uploaded successfully. Processing has started.",
            "file_reference": file_ref,
            "callback_url": callback_url,
            "callback_secret": callback_secret,
            "document_type": document_type.value,
            "language": language.value,
            "clean_with_ai": clean_with_ai,
            "password": password,
            "metadata": {
                "filename": filename,
                "content_type": file.content_type,
                "file_size_bytes": file_size,
                "checksum_sha256": checksum,
                "doc_category": doc_category,
                "user_email": normalized_email,
            },
        }
        await JobRepository.create_job(job_doc)
        await WorkerManager.submit_job(job_id, user_email=normalized_email)

        response.status_code = status.HTTP_202_ACCEPTED
        return AsyncAcceptedResponse(
            job_id=job_id,
            document_id=effective_request_id,
            id=effective_request_id,
            status="processing" if callback_url else "queued",
            message="Document extraction queued. Results will be delivered to callback_url upon completion."
            if callback_url
            else "Document uploaded successfully. Processing has started.",
            status_url=f"/api/v1/jobs/{job_id}",
            callback_url=callback_url,
        )

    # Synchronous Execution
    return await pipeline.process_document(
        file=file,
        document_type=document_type.value,
        language=language.value,
        clean_with_ai=clean_with_ai,
        client_request_id=effective_request_id,
        password=password,
        owner_hash=auth_client.key_hash,
        user_email=normalized_email,
    )


@router.post(
    "/batch",
    response_model=BatchExtractionResponse,
    status_code=status.HTTP_200_OK,
    summary="Batch process multiple documents or a Zip archive",
    description=(
        "Upload up to 50 documents or a single `.zip` archive containing invoices, receipts, or statements. "
        "Processes all files concurrently with isolated error handling and computes consolidated inflow/outflow metrics.\n\n"
        "**Authentication**: Requires valid `X-API-Key` or `Authorization: Bearer <key>`."
    ),
    dependencies=[Depends(check_rate_limit)],
)
async def batch_extract(
    files: List[UploadFile] = File(..., description="List of document files or a .zip archive (up to 50 files)"),
    document_type: DocumentTypeEnum = Form(
        default=DocumentTypeEnum.AUTO,
        description="Target document type hint",
    ),
    language: LanguageEnum = Form(
        default=LanguageEnum.EN,
        description="OCR language hint",
    ),
    clean_with_ai: bool = Form(
        default=True,
        description="Whether to clean OCR text and extract structured entities using DeepSeek",
    ),
    user_email: Optional[str] = Form(
        default=None,
        description="Optional user email from logged-in frontend session",
    ),
    auth_client: AuthenticatedClient = Depends(verify_api_key),
    pipeline: ExtractionPipeline = Depends(get_pipeline),
) -> BatchExtractionResponse:
    """
    Processes multiple documents in a single batch request.
    """
    return await BatchService.process_batch(
        files=files,
        document_type=document_type.value,
        language=language.value,
        clean_with_ai=clean_with_ai,
        owner_hash=auth_client.key_hash,
        user_email=user_email,
        pipeline=pipeline,
    )
