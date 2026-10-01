"""
Document Metadata Model (Stored in MongoDB).
Never stores raw binary data; only references Cloudinary storage IDs and secure URLs.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from app.core.constants import DocumentStatus


class DocumentPageInfo(BaseModel):
    """Metadata for an individual page in a multi-page document."""
    page_number: int
    width: Optional[int] = None
    height: Optional[int] = None
    preview_url: Optional[str] = None
    preview_public_id: Optional[str] = None


class DocumentModel(BaseModel):
    """Document entity tracking stored files, Cloudinary pointers, and processing status."""
    id: Optional[str] = Field(default=None, description="MongoDB Document ID")
    user_id: str = Field(description="Owner User ID")
    original_filename: str
    file_size_bytes: int
    mime_type: str
    file_hash: Optional[str] = Field(default=None, description="SHA-256 checksum of original file")
    
    # Cloudinary storage references
    storage_public_id: str = Field(description="Cloudinary asset public ID")
    storage_url: str = Field(description="Direct access or Cloudinary secure URL")
    storage_format: str
    
    # Processing Status
    status: DocumentStatus = Field(default=DocumentStatus.PENDING)
    page_count: int = Field(default=1)
    pages: List[DocumentPageInfo] = Field(default_factory=list)
    
    # Metadata & Tags
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
