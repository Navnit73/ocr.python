"""
API v1 Router with OCR, Jobs, Documents, Export, and Admin Endpoints.
"""

from datetime import datetime, timezone
from fastapi import APIRouter

from app.api.v1.endpoints.ocr import router as ocr_router
from app.api.v1.endpoints.jobs import router as jobs_router
from app.api.v1.endpoints.documents import router as documents_router
from app.api.v1.endpoints.export import router as export_router
from app.api.v1.endpoints.admin import router as admin_router

router = APIRouter()


# Health & Diagnostic Endpoints
@router.get("/health", tags=["Health"], summary="API v1 Health Check")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "api_version": "v1",
    }


@router.get("/ping", tags=["Health"], summary="Ping Check")
async def ping():
    """Simple ping check for uptime monitors."""
    return {"ping": "pong"}


# Include Functional Endpoints
router.include_router(ocr_router)
router.include_router(jobs_router)
router.include_router(documents_router)
router.include_router(export_router)
router.include_router(admin_router)
