"""
Asynchronous Job Management API Endpoints.
Supports job creation (HTTP 202 Accepted), real-time progress tracking,
Server-Sent Events (SSE) live streaming, cancellation, and retries.
"""

from typing import List, Optional
import uuid
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse

from app.core.rate_limiter import check_rate_limit
from app.core.security import AuthenticatedClient, verify_api_key
from app.db.repositories.job_repo import JobRepository
from app.schemas.job import (
    JobCreateResponse,
    JobListResponse,
    JobStatusEnum,
    JobStatusResponse,
)
from app.schemas.ocr import DocumentTypeEnum, LanguageEnum
from app.services.event_bus import JobEventBus
from app.services.file_validator import FileValidator
from app.services.storage_service import StorageService
from app.workers.worker_manager import WorkerManager

router = APIRouter(prefix="/jobs", tags=["Asynchronous Jobs & Background Workers"])


@router.post(
    "/upload",
    response_model=JobCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload document and queue asynchronous OCR processing (HTTP 202 Accepted)",
    description=(
        "Upload a document (up to 200 pages / 100MB) for asynchronous background processing.\n\n"
        "Returns HTTP `202 Accepted` immediately with a `job_id` and `status_url`.\n"
        "The processing continues in the background independently of the browser session.\n"
        "Track live progress via `GET /api/v1/jobs/{job_id}` or Server-Sent Events at `GET /api/v1/jobs/{job_id}/events`."
    ),
    dependencies=[Depends(check_rate_limit)],
)
async def upload_document_async(
    file: UploadFile = File(..., description="Document file: PDF (up to 200 pages), JPG, PNG, WEBP, or TIFF"),
    document_type: DocumentTypeEnum = Form(
        default=DocumentTypeEnum.AUTO,
        description="Target document type hint (auto, bank_statement, receipt, invoice, general)",
    ),
    language: LanguageEnum = Form(
        default=LanguageEnum.EN,
        description="OCR language hint",
    ),
    clean_with_ai: bool = Form(
        default=True,
        description="Whether to clean OCR text and extract structured entities using DeepSeek",
    ),
    request_id: Optional[str] = Form(
        default=None,
        description="Optional custom request/document ID from frontend",
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
        description="Optional secret key for HMAC-SHA256 signature verification on callback POST",
    ),
    user_email: Optional[str] = Form(
        default=None,
        description="Optional user email from logged-in session",
    ),
    x_request_id: Optional[str] = Header(
        default=None,
        alias="X-Request-ID",
        description="Optional custom request ID via HTTP Header",
    ),
    auth_client: AuthenticatedClient = Depends(verify_api_key),
) -> JobCreateResponse:
    """
    Accepts document upload, saves file to storage, creates queued job in MongoDB,
    and enqueues work to background processing workers.
    """
    effective_doc_id = FileValidator.generate_or_sanitize_request_id(request_id or x_request_id)
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    filename = file.filename or "document.pdf"
    normalized_email = (user_email or "guest").lower().strip()

    # 1. Validate Upload
    doc_category, file_bytes = await FileValidator.validate_upload(file)

    # 2. Save File Securely to Storage
    file_ref, checksum, file_size = await StorageService.save_file(
        file_bytes=file_bytes,
        filename=filename,
        document_id=effective_doc_id,
    )

    # 3. Create MongoDB Job Record
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
        "message": "Document uploaded successfully. Queued for background processing.",
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

    # 4. Submit Job to Background Worker
    await WorkerManager.submit_job(job_id, user_email=normalized_email)

    return JobCreateResponse(
        job_id=job_id,
        document_id=effective_doc_id,
        status="queued",
        message="Document uploaded successfully. Processing has started.",
        status_url=f"/api/v1/jobs/{job_id}",
    )


@router.get(
    "/{job_id}",
    response_model=JobStatusResponse,
    summary="Get background processing status and progress",
    description="Retrieve the current status, progress percentage, stage, and completion results for a job.",
    dependencies=[Depends(check_rate_limit)],
)
async def get_job_status(
    job_id: str,
    auth_client: AuthenticatedClient = Depends(verify_api_key),
) -> JobStatusResponse:
    """
    Returns current job progress and status, ensuring ownership matching to prevent IDOR.
    """
    job = await JobRepository.get_job(job_id, owner_hash=auth_client.key_hash)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' was not found or access is unauthorized.",
        )

    doc_id = job.get("document_id")
    result_url = f"/api/v1/documents/{doc_id}" if doc_id and job.get("status") == "completed" else None
    user_email = job.get("user_email") or job.get("metadata", {}).get("user_email")

    return JobStatusResponse(
        job_id=job["job_id"],
        document_id=doc_id,
        user_email=user_email,
        status=JobStatusEnum(job.get("status", "queued")),
        progress=job.get("progress", 0),
        total_pages=job.get("total_pages", 0),
        processed_pages=job.get("processed_pages", 0),
        current_stage=job.get("current_stage", "queued"),
        message=job.get("message", ""),
        created_at=job["created_at"],
        updated_at=job["updated_at"],
        started_at=job.get("started_at"),
        completed_at=job.get("completed_at"),
        result=job.get("result"),
        result_url=result_url,
        error=job.get("error"),
        retry_count=job.get("retry_count", 0),
        metadata=job.get("metadata", {}),
    )


@router.get(
    "/{job_id}/events",
    summary="Live Server-Sent Events (SSE) Stream for real-time progress updates",
    description=(
        "Connect via Server-Sent Events (`EventSource`) to receive real-time status and stage updates "
        "without manual polling. Streams until job completes or fails."
    ),
    responses={
        200: {
            "description": "text/event-stream real-time SSE stream",
            "content": {"text/event-stream": {}},
        }
    },
)
async def stream_job_events(
    job_id: str,
    auth_client: AuthenticatedClient = Depends(verify_api_key),
):
    """
    Streams SSE progress and completion events for the specified job.
    """
    job = await JobRepository.get_job(job_id, owner_hash=auth_client.key_hash)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' was not found.",
        )

    return StreamingResponse(
        JobEventBus.subscribe(job_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "",
    response_model=JobListResponse,
    summary="List background processing jobs for current user",
    description="Lists recent processing jobs with pagination and status filters.",
    dependencies=[Depends(check_rate_limit)],
)
async def list_jobs(
    status_filter: Optional[str] = Query(
        default=None,
        alias="status",
        description="Filter by status: queued, processing, completed, failed, cancelled, or all",
    ),
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page"),
    auth_client: AuthenticatedClient = Depends(verify_api_key),
) -> JobListResponse:
    """
    Lists jobs belonging to the authenticated account.
    """
    items, total = await JobRepository.list_jobs(
        owner_hash=auth_client.key_hash,
        status=status_filter,
        page=page,
        page_size=page_size,
    )
    total_pages = max(1, (total + page_size - 1) // page_size)

    formatted_items = []
    for job in items:
        doc_id = job.get("document_id")
        result_url = f"/api/v1/documents/{doc_id}" if doc_id and job.get("status") == "completed" else None
        formatted_items.append(
            JobStatusResponse(
                job_id=job["job_id"],
                document_id=doc_id,
                user_email=job.get("user_email") or job.get("metadata", {}).get("user_email"),
                status=JobStatusEnum(job.get("status", "queued")),
                progress=job.get("progress", 0),
                total_pages=job.get("total_pages", 0),
                processed_pages=job.get("processed_pages", 0),
                current_stage=job.get("current_stage", "queued"),
                message=job.get("message", ""),
                created_at=job["created_at"],
                updated_at=job["updated_at"],
                started_at=job.get("started_at"),
                completed_at=job.get("completed_at"),
                result=job.get("result"),
                result_url=result_url,
                error=job.get("error"),
                retry_count=job.get("retry_count", 0),
                metadata=job.get("metadata", {}),
            )
        )

    return JobListResponse(
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        items=formatted_items,
    )


@router.post(
    "/{job_id}/cancel",
    summary="Cancel a queued or processing background job",
    description="Cancels an ongoing or queued background OCR processing job.",
    dependencies=[Depends(check_rate_limit)],
)
async def cancel_job(
    job_id: str,
    auth_client: AuthenticatedClient = Depends(verify_api_key),
):
    """Cancels a job."""
    success = await JobRepository.cancel_job(job_id, owner_hash=auth_client.key_hash)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job '{job_id}' could not be cancelled (it may have already finished or does not exist).",
        )
    await JobEventBus.publish(job_id, "ocr.job.cancelled", {
        "job_id": job_id,
        "status": "cancelled",
        "message": "Job cancelled by user",
    })
    return {"status": "cancelled", "job_id": job_id, "message": "Job cancelled successfully"}


@router.post(
    "/{job_id}/retry",
    summary="Retry a failed background job",
    description="Re-enqueues a failed OCR job for processing.",
    dependencies=[Depends(check_rate_limit)],
)
async def retry_job(
    job_id: str,
    auth_client: AuthenticatedClient = Depends(verify_api_key),
):
    """Retries a failed job."""
    job = await JobRepository.get_job(job_id, owner_hash=auth_client.key_hash)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )
    if job.get("status") not in ("failed", "cancelled"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job '{job_id}' is currently in status '{job.get('status')}', only failed or cancelled jobs can be retried.",
        )

    await JobRepository.increment_retry(job_id)
    await WorkerManager.submit_job(job_id)
    return {"status": "queued", "job_id": job_id, "message": "Job re-queued for processing"}
