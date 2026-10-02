"""
Document Schemas for Document Persistence and Retrieval.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.schemas.ocr import ExtractionResponse, ExtractionWarning, PageExtraction, ProcessingMetadata


class DocumentListItem(BaseModel):
    """Summary item in document listing endpoint."""
    id: str = Field(..., description="Document ID")
    job_id: Optional[str] = Field(default=None, description="Associated processing Job ID")
    filename: str = Field(..., description="Uploaded file name")
    content_type: str = Field(default="application/pdf", description="MIME type")
    file_size_bytes: int = Field(default=0, description="File size in bytes")
    document_type: str = Field(..., description="Classified document type")
    status: str = Field(default="success", description="Extraction status")
    pages_count: int = Field(default=1, description="Number of pages")
    summary: Optional[Dict[str, Any]] = Field(default=None, description="Key extracted KPI summary")
    created_at: datetime = Field(..., description="Upload/extraction timestamp")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)


class DocumentListResponse(BaseModel):
    """Paginated document listing response."""
    total: int
    page: int
    page_size: int
    total_pages: int
    items: List[DocumentListItem]


class DocumentDetailResponse(BaseModel):
    """Complete document record including extraction, text, and pages."""
    id: str
    job_id: Optional[str] = None
    filename: str
    content_type: str
    file_size_bytes: int
    document_type: str
    status: str
    extraction: Optional[Dict[str, Any]] = None
    raw_text: str = ""
    cleaned_text: Optional[str] = None
    pages: List[PageExtraction] = Field(default_factory=list)
    metadata: ProcessingMetadata
    warnings: List[ExtractionWarning] = Field(default_factory=list)
    created_at: datetime
