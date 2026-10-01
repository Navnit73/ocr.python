"""
OCR & Extraction API Endpoints with API Key Authentication, Rate Limiting, Batch & Async Webhooks.
"""

from typing import List, Optional, Union
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, Request, Response, UploadFile, status
from pydantic import BaseModel

from app.core.rate_limiter import check_rate_limit
from app.core.security import AuthenticatedClient, verify_api_key
from app.schemas.batch import BatchExtractionResponse
from app.schemas.ocr import (
    DocumentTypeEnum,
    ExtractionResponse,
    LanguageEnum,
)
from app.services.batch_service import BatchService
from app.services.file_validator import FileValidator
from app.services.pipeline import ExtractionPipeline
from app.services.webhook_service import WebhookService

router = APIRouter(prefix="/ocr", tags=["OCR & Extraction"])


class AsyncAcceptedResponse(BaseModel):
    """Returned when an asynchronous callback_url is supplied."""
    id: str
    status: str = "processing"
    message: str
    callback_url: str


def get_pipeline() -> ExtractionPipeline:
    """Dependency injector for ExtractionPipeline."""
    return ExtractionPipeline()


async def _run_async_extraction_and_webhook(
    pipeline: ExtractionPipeline,
    file_bytes: bytes,
    filename: str,
    content_type: str,
    document_type: str,
    language: str,
    clean_with_ai: bool,
    client_request_id: str,
    password: Optional[str],
    owner_hash: Optional[str],
    callback_url: str,
    callback_secret: Optional[str],
):
    """Background task worker that executes extraction and fires webhook."""
    import io
    file_obj = UploadFile(
        filename=filename,
        file=io.BytesIO(file_bytes),
        headers={"content-type": content_type},
    )
    res = await pipeline.process_document(
        file=file_obj,
        document_type=document_type,
        language=language,
        clean_with_ai=clean_with_ai,
        client_request_id=client_request_id,
        password=password,
        owner_hash=owner_hash,
    )
    await WebhookService.send_webhook(
        callback_url=callback_url,
        payload=res.model_dump(),
        secret=callback_secret,
    )


@router.post(
    "/extract",
    response_model=Union[ExtractionResponse, AsyncAcceptedResponse],
    status_code=status.HTTP_200_OK,
    summary="Extract text and structured data from documents (Up to 200 Pages)",
    description=(
        "Upload a bank statement, receipt, invoice, general document, PDF, or image (up to 200 pages / 100MB). "
        "Extracts OCR text, cleans it with DeepSeek AI, identifies document type, "
        "and returns structured 3-layer JSON (raw OCR, cleaned text, structured extraction).\n\n"
        "**Authentication**: Requires valid `X-API-Key` or `Authorization: Bearer <key>`.\n"
        "**Async Mode**: If `callback_url` is provided, returns `202 Accepted` immediately and posts final extraction to your webhook."
    ),
    dependencies=[Depends(check_rate_limit)],
)
async def extract_document(
    background_tasks: BackgroundTasks,
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
        description="Optional webhook URL. If provided, extraction runs in background and results are POSTed here.",
    ),
    callback_secret: Optional[str] = Form(
        default=None,
        description="Optional secret key for HMAC-SHA256 signature header (X-Webhook-Signature) on callback POST.",
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
    Extracts text and structured financial entities from uploaded documents.
    """
    effective_request_id = FileValidator.generate_or_sanitize_request_id(request_id or x_request_id)

    # If callback_url provided, schedule background task and return 202 Accepted immediately
    if callback_url:
        file_bytes = await file.read()
        filename = file.filename or "document.pdf"
        content_type = file.content_type or "application/pdf"

        background_tasks.add_task(
            _run_async_extraction_and_webhook,
            pipeline=pipeline,
            file_bytes=file_bytes,
            filename=filename,
            content_type=content_type,
            document_type=document_type.value,
            language=language.value,
            clean_with_ai=clean_with_ai,
            client_request_id=effective_request_id,
            password=password,
            owner_hash=auth_client.key_hash,
            callback_url=callback_url,
            callback_secret=callback_secret,
        )

        response.status_code = status.HTTP_202_ACCEPTED
        return AsyncAcceptedResponse(
            id=effective_request_id,
            status="processing",
            message="Document extraction queued. Results will be delivered to callback_url upon completion.",
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
        pipeline=pipeline,
    )
