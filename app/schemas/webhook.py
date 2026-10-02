"""
Webhook Schemas for Event-Driven Background Notifications.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class WebhookEventEnum(str, Enum):
    JOB_STARTED = "ocr.job.started"
    JOB_PROGRESS = "ocr.job.progress"
    JOB_COMPLETED = "ocr.job.completed"
    JOB_FAILED = "ocr.job.failed"


class WebhookPayload(BaseModel):
    """Payload dispatched to client callback_url upon event occurrence."""
    event: WebhookEventEnum = Field(..., description="Event type")
    event_id: str = Field(..., description="Unique event ID for deduplication/idempotency")
    job_id: str = Field(..., description="Unique job identifier")
    document_id: Optional[str] = Field(default=None, description="Document identifier")
    user_email: Optional[str] = Field(default=None, description="User email associated with job")
    status: str = Field(..., description="Current job status: started, processing, completed, failed")
    progress: Optional[int] = Field(default=None, ge=0, le=100, description="Processing percentage")
    current_stage: Optional[str] = Field(default=None, description="Current processing stage")
    timestamp: datetime = Field(..., description="Timestamp when event was generated")
    result_url: Optional[str] = Field(
        default=None,
        description="Authenticated URL to retrieve full extraction result without passing huge payloads",
    )
    result: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Full extraction result payload including document_type, extraction, raw_text, cleaned_text, and metadata",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Execution statistics (e.g. total_pages, processed_pages, processing_time_ms, user_email)",
    )
    error: Optional[str] = Field(default=None, description="Error details if event is failed")


class WebhookDeliveryRecord(BaseModel):
    """Log record of a webhook delivery attempt."""
    delivery_id: str
    job_id: str
    event: str
    url: str
    status_code: Optional[int] = None
    success: bool
    attempts: int
    error: Optional[str] = None
    delivered_at: datetime
