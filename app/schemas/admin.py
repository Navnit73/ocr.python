"""
Admin Dashboard Schemas for Real System Health, Metrics, and Job Inspection.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.schemas.job import JobStatusResponse


class WorkerHealthInfo(BaseModel):
    """Health and concurrency metrics for background workers."""
    mode: str = Field(default="async_worker_pool", description="Worker execution mode: celery or async_worker_pool")
    active_workers: int = Field(default=1, description="Active worker process count")
    concurrency_limit: int = Field(default=4, description="Max concurrent processing limit")
    current_active_jobs: int = Field(default=0, description="Jobs currently being processed")
    celery_connected: bool = Field(default=False, description="Whether Celery/Redis connection is live")
    redis_connected: bool = Field(default=False, description="Whether Redis broker connection is live")
    mongodb_connected: bool = Field(default=True, description="Whether MongoDB connection is live")
    uptime_seconds: float = Field(default=0.0, description="Worker pool uptime in seconds")
    cpu_percent: Optional[float] = Field(default=None, description="Current CPU usage percentage")
    memory_percent: Optional[float] = Field(default=None, description="Current Memory usage percentage")


class AdminStatsResponse(BaseModel):
    """Real database metrics and analytics for Admin Dashboard."""
    total_jobs: int = Field(default=0, description="All-time total jobs")
    queued_jobs: int = Field(default=0, description="Currently queued jobs")
    processing_jobs: int = Field(default=0, description="Currently processing jobs")
    completed_jobs: int = Field(default=0, description="Successfully completed jobs")
    failed_jobs: int = Field(default=0, description="Failed jobs")
    cancelled_jobs: int = Field(default=0, description="Cancelled jobs")
    total_pages_processed: int = Field(default=0, description="Cumulative pages processed")
    average_processing_time_ms: float = Field(default=0.0, description="Average duration in milliseconds")
    retry_count_total: int = Field(default=0, description="Total retry attempts across all jobs")
    worker_health: WorkerHealthInfo
    recent_errors: List[Dict[str, Any]] = Field(default_factory=list, description="Recent job errors")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AdminJobListResponse(BaseModel):
    """Admin view of all jobs with advanced filtering and diagnostic info."""
    total: int
    page: int
    page_size: int
    total_pages: int
    items: List[JobStatusResponse]
