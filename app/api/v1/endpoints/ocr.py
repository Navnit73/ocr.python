"""
OCR & Extraction API Endpoints.
"""

from typing import Optional
from fastapi import APIRouter, Depends, File, Form, Header, UploadFile, status

from app.schemas.ocr import (
    DocumentTypeEnum,
    ExtractionResponse,
    LanguageEnum,
)
from app.services.pipeline import ExtractionPipeline

router = APIRouter(prefix="/ocr", tags=["OCR & Extraction"])


def get_pipeline() -> ExtractionPipeline:
    """Dependency injector for ExtractionPipeline."""
    return ExtractionPipeline()


@router.post(
    "/extract",
    response_model=ExtractionResponse,
    status_code=status.HTTP_200_OK,
    summary="Extract text and structured data from documents",
    description=(
        "Upload a bank statement, receipt, invoice, general document, PDF, or image. "
        "Extracts OCR text, cleans it with DeepSeek AI, identifies document type, "
        "and returns structured 3-layer JSON (raw OCR, cleaned text, structured extraction)."
    ),
)
async def extract_document(
    file: UploadFile = File(..., description="Document file: PDF, JPG, PNG, WEBP, or TIFF"),
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
    x_request_id: Optional[str] = Header(
        default=None,
        alias="X-Request-ID",
        description="Optional custom request ID via HTTP Header",
    ),
    pipeline: ExtractionPipeline = Depends(get_pipeline),
) -> ExtractionResponse:
    """
    Extracts text and structured financial entities from uploaded documents.
    """
    effective_request_id = request_id or x_request_id

    return await pipeline.process_document(
        file=file,
        document_type=document_type.value,
        language=language.value,
        clean_with_ai=clean_with_ai,
        client_request_id=effective_request_id,
    )
