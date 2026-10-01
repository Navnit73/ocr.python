"""
OCR Processing and Background Job Status Router.
"""

from fastapi import APIRouter
from app.schemas.common import APIResponse

router = APIRouter(prefix="/jobs", tags=["OCR Jobs & Background Tasks"])


@router.get("/status", response_model=APIResponse[dict])
async def jobs_module_status() -> APIResponse[dict]:
    """Confirms jobs module readiness for Celery background tasks."""
    return APIResponse(
        success=True,
        message="Jobs & Asynchronous OCR module initialized.",
        data={"module": "jobs", "phase": "foundation"},
    )
