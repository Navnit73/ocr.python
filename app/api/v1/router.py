"""
API v1 Router.
"""

from datetime import datetime, timezone
from fastapi import APIRouter

router = APIRouter(tags=["API v1"])


@router.get("/health", summary="API v1 Health Check")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "api_version": "v1",
    }


@router.get("/ping", summary="Ping Check")
async def ping():
    """Simple ping check for uptime monitors."""
    return {"ping": "pong"}
