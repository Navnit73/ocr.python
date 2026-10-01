"""
Document Management and Cloudinary Storage Router (Stubs prepared for Phase 2).
"""

from fastapi import APIRouter
from app.schemas.common import APIResponse

router = APIRouter(prefix="/documents", tags=["Documents & Storage"])


@router.get("/status", response_model=APIResponse[dict])
async def documents_module_status() -> APIResponse[dict]:
    """Confirms documents module readiness for Phase 2."""
    return APIResponse(
        success=True,
        message="Documents & Storage module initialized.",
        data={"module": "documents", "phase": "foundation"},
    )
