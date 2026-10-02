"""
Admin Dashboard API Endpoints for Real Background Processing Metrics, Worker Health, and Diagnostics.
Protected by Admin API Key authentication.
"""

from typing import Optional
from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse

from app.core.rate_limiter import check_rate_limit
from app.core.security import AuthenticatedClient, verify_admin_key
from app.db.repositories.job_repo import JobRepository
from app.schemas.admin import AdminJobListResponse, AdminStatsResponse, WorkerHealthInfo
from app.schemas.job import JobStatusEnum, JobStatusResponse
from app.services.event_bus import JobEventBus
from app.workers.worker_manager import WorkerManager

router = APIRouter(prefix="/admin", tags=["Admin Dashboard & System Monitoring"])


@router.get(
    "/stats",
    response_model=AdminStatsResponse,
    summary="Get real-time system metrics, worker health, and job statistics",
    description=(
        "Returns actual aggregated metrics calculated directly from persistent MongoDB records.\n\n"
        "Includes total queued, processing, completed, and failed counts, cumulative pages processed, "
        "average duration, retry totals, worker CPU/memory usage, and recent processing error logs."
    ),
    dependencies=[Depends(check_rate_limit)],
)
async def get_admin_stats(
    admin_client: AuthenticatedClient = Depends(verify_admin_key),
) -> AdminStatsResponse:
    """
    Computes real database statistics and worker health metrics for authorized administrators.
    """
    stats_data = await JobRepository.get_stats()
    worker_health_data = WorkerManager.get_health_stats()

    return AdminStatsResponse(
        total_jobs=stats_data["total_jobs"],
        queued_jobs=stats_data["queued_jobs"],
        processing_jobs=stats_data["processing_jobs"],
        completed_jobs=stats_data["completed_jobs"],
        failed_jobs=stats_data["failed_jobs"],
        cancelled_jobs=stats_data["cancelled_jobs"],
        total_pages_processed=stats_data["total_pages_processed"],
        average_processing_time_ms=stats_data["average_processing_time_ms"],
        retry_count_total=stats_data["retry_count_total"],
        worker_health=WorkerHealthInfo(**worker_health_data),
        recent_errors=stats_data["recent_errors"],
    )


@router.get(
    "/jobs",
    response_model=AdminJobListResponse,
    summary="List and inspect all system background jobs (Admin View)",
    description="Allows administrators to inspect all jobs across all users with status filtering and pagination.",
    dependencies=[Depends(check_rate_limit)],
)
async def admin_list_jobs(
    status_filter: Optional[str] = Query(
        default=None,
        alias="status",
        description="Filter by status: queued, processing, completed, failed, cancelled, or all",
    ),
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page"),
    admin_client: AuthenticatedClient = Depends(verify_admin_key),
) -> AdminJobListResponse:
    """
    Admin listing of all jobs without user-isolation constraints.
    """
    items, total = await JobRepository.list_jobs(
        owner_hash=None,  # No owner filter for admin
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

    return AdminJobListResponse(
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        items=formatted_items,
    )


@router.get(
    "/events",
    summary="Global Server-Sent Events (SSE) stream for real-time Admin monitoring",
    description="Streams all system background job events in real time.",
)
async def admin_stream_events(
    admin_client: AuthenticatedClient = Depends(verify_admin_key),
):
    """
    Streams all global events to admin subscribers.
    """
    return StreamingResponse(
        JobEventBus.subscribe_global(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
