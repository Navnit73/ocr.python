"""
Job Schemas for Asynchronous Processing, Progress Tracking, and Server-Sent Events.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.schemas.ocr import ExtractionResponse


class JobStatusEnum(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobStageEnum(str, Enum):
    QUEUED = "queued"
    VALIDATION = "validation"
    FILE_UPLOAD = "file_upload"
    PDF_PARSING = "pdf_parsing"
    OCR_EXTRACTION = "ocr_extraction"
    AI_CLEANING = "ai_cleaning"
    CLASSIFICATION = "classification"
    STRUCTURED_EXTRACTION = "structured_extraction"
    PERSISTENCE = "persistence"
    WEBHOOK_DISPATCH = "webhook_dispatch"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobCreateResponse(BaseModel):
    """Returned immediately upon queuing an upload with HTTP 202 Accepted."""
    job_id: str = Field(..., description="Unique job identifier")
    document_id: str = Field(..., description="Unique document identifier associated with the job")
    status: str = Field(default="queued", description="Initial job status")
    message: str = Field(
        default="Document uploaded successfully. Processing has started.",
        description="User-friendly status message",
    )
    status_url: str = Field(..., description="URL endpoint to poll for status and progress")


class JobStatusResponse(BaseModel):
    """Detailed job status and progress tracking representation."""
    job_id: str = Field(..., description="Unique job identifier")
    document_id: Optional[str] = Field(default=None, description="Document ID if available")
    user_email: Optional[str] = Field(default=None, description="User email associated with job")
    status: JobStatusEnum = Field(..., description="Current status: queued, processing, completed, failed, cancelled")
    progress: int = Field(default=0, ge=0, le=100, description="Processing completion percentage (0-100)")
    total_pages: int = Field(default=0, ge=0, description="Total number of document pages")
    processed_pages: int = Field(default=0, ge=0, description="Number of pages processed so far")
    current_stage: str = Field(default="queued", description="Current processing stage")
    message: str = Field(default="", description="Descriptive progress message")
    created_at: datetime = Field(..., description="Timestamp when job was created")
    updated_at: datetime = Field(..., description="Timestamp when job was last updated")
    started_at: Optional[datetime] = Field(default=None, description="Timestamp when processing began")
    completed_at: Optional[datetime] = Field(default=None, description="Timestamp when processing completed")
    result: Optional[ExtractionResponse] = Field(default=None, description="Final extraction response (when completed)")
    result_url: Optional[str] = Field(default=None, description="Direct URL to fetch document extraction")
    error: Optional[str] = Field(default=None, description="Error message if job failed")
    retry_count: int = Field(default=0, ge=0, description="Number of retries attempted")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional job metadata and timings")


class JobListResponse(BaseModel):
    """Paginated list of jobs."""
    total: int
    page: int
    page_size: int
    total_pages: int
    items: List[JobStatusResponse]


class JobEventPayload(BaseModel):
    """Payload pushed via Server-Sent Events (SSE)."""
    event: str
    job_id: str
    document_id: Optional[str] = None
    user_email: Optional[str] = None
    status: JobStatusEnum
    progress: int
    total_pages: int
    processed_pages: int
    current_stage: str
    message: str
    timestamp: datetime
    result_url: Optional[str] = None
    error: Optional[str] = None
