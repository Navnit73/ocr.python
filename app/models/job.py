"""
Job Model for Celery background processing tracking.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
from app.core.constants import JobStatus, OCREngineType


class JobModel(BaseModel):
    """Tracks asynchronous processing jobs executed by Celery workers."""
    id: Optional[str] = Field(default=None, description="MongoDB Document ID")
    document_id: str = Field(description="Associated Document ID")
    user_id: str = Field(description="Owner User ID")
    celery_task_id: Optional[str] = Field(default=None, description="Celery AsyncResult task ID")
    
    status: JobStatus = Field(default=JobStatus.QUEUED)
    progress_percent: int = Field(default=0, ge=0, le=100)
    current_step: Optional[str] = Field(default=None, description="e.g. 'preprocessing', 'ocr_engine', 'llm_correction'")
    
    # Engine Settings used for this job
    engine: OCREngineType = Field(default=OCREngineType.PADDLEOCR)
    enable_ai_correction: bool = True
    enable_table_extraction: bool = True
    
    error_message: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3
    
    result_summary: Optional[Dict[str, Any]] = None
    
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
