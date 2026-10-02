"""
Documents API Endpoints for Stored Extraction Retrieval, Search, and Lifecycle Management.
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.core.rate_limiter import check_rate_limit
from app.core.security import AuthenticatedClient, verify_api_key
from app.db.repositories.document_repo import DocumentRepository
from app.schemas.document import DocumentDetailResponse, DocumentListResponse
from app.schemas.ocr import ExtractionResponse
from app.services.result_cache import ResultCache

router = APIRouter(prefix="/documents", tags=["Stored Documents & Extractions"])


@router.get(
    "",
    response_model=DocumentListResponse,
    summary="List stored document extractions (Paginated & Filtered)",
    description=(
        "Lists previously extracted documents for the authenticated API key.\n\n"
        "Supports filtering by `document_type`, keyword search across filename and OCR text, and pagination."
    ),
    dependencies=[Depends(check_rate_limit)],
)
async def list_documents(
    document_type: Optional[str] = Query(
        default=None,
        description="Filter by document type: bank_statement, invoice, receipt, general, or all",
    ),
    search: Optional[str] = Query(
        default=None,
        description="Search keyword within document filename or extracted text",
    ),
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page"),
    auth_client: AuthenticatedClient = Depends(verify_api_key),
) -> DocumentListResponse:
    """
    Retrieves paginated list of extracted documents for caller.
    """
    items, total = await DocumentRepository.list_documents(
        owner_hash=auth_client.key_hash,
        document_type=document_type,
        search=search,
        page=page,
        page_size=page_size,
    )
    total_pages = max(1, (total + page_size - 1) // page_size)

    return DocumentListResponse(
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        items=items,
    )


@router.get(
    "/{document_id}",
    response_model=ExtractionResponse,
    summary="Retrieve complete extraction JSON by Document ID",
    description="Retrieves the full 3-layer extraction result (raw OCR, cleaned text, structured entities) for a document.",
    dependencies=[Depends(check_rate_limit)],
)
async def get_document_by_id(
    document_id: str,
    auth_client: AuthenticatedClient = Depends(verify_api_key),
) -> ExtractionResponse:
    """
    Fetches full extraction details for a document. Checks MongoDB first, with fallback to in-memory ResultCache.
    Enforces owner verification to prevent IDOR vulnerabilities.
    """
    # 1. Check MongoDB
    doc = await DocumentRepository.get_document(document_id, owner_hash=auth_client.key_hash)
    if doc:
        return ExtractionResponse(
            id=doc["document_id"],
            status=doc.get("status", "success"),
            document_type=doc.get("document_type", "general"),
            extraction=doc.get("extraction"),
            raw_text=doc.get("raw_text", ""),
            cleaned_text=doc.get("cleaned_text"),
            pages=doc.get("pages", []),
            metadata=doc.get("metadata", {}),
            warnings=doc.get("warnings", []),
        )

    # 2. Check in-memory ResultCache as fallback
    cached = ResultCache.get(document_id, caller_hash=auth_client.key_hash)
    if cached:
        return ExtractionResponse(**cached)

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Document '{document_id}' was not found or access is unauthorized.",
    )


@router.delete(
    "/{document_id}",
    summary="Delete a stored document extraction",
    description="Permanently deletes a document extraction from the database and storage.",
    dependencies=[Depends(check_rate_limit)],
)
async def delete_document_by_id(
    document_id: str,
    auth_client: AuthenticatedClient = Depends(verify_api_key),
):
    """
    Deletes document record from MongoDB and in-memory cache.
    """
    deleted = await DocumentRepository.delete_document(document_id, owner_hash=auth_client.key_hash)
    ResultCache.delete(document_id, caller_hash=auth_client.key_hash)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document '{document_id}' was not found or access is unauthorized.",
        )

    return {"status": "deleted", "document_id": document_id, "message": "Document deleted successfully"}
