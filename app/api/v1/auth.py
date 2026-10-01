"""
Authentication and User Profile Router (Stubs prepared for Phase 1).
"""

from fastapi import APIRouter
from app.schemas.common import APIResponse

router = APIRouter(prefix="/auth", tags=["Authentication & Users"])


@router.get("/status", response_model=APIResponse[dict])
async def auth_module_status() -> APIResponse[dict]:
    """Confirms auth module readiness for Phase 1."""
    return APIResponse(
        success=True,
        message="Authentication module initialized and ready for Phase 1.",
        data={"module": "auth", "phase": "foundation"},
    )
