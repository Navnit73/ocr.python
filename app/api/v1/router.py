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
    from app.core.config import get_settings
    from app.db.mongodb import MongoDBManager
    from app.workers.worker_manager import WorkerManager

    settings = get_settings()
    db_connected = MongoDBManager.is_connected()
    worker_health = WorkerManager.get_health_stats()

    return {
        "status": "healthy",
        "app_name": settings.app_name,
        "version": settings.version,
        "environment": settings.environment,
        "database": "connected" if db_connected else "disconnected",
        "worker_status": "active" if worker_health.get("is_alive") else "idle",
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
