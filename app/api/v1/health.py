"""
System Diagnostics, Health, Readiness, and Liveness Endpoints.
"""

import time
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from app.core.config import Settings, get_settings
from app.db.mongo import mongo_manager
from app.db.redis import redis_manager
from app.schemas.health import HealthCheckResponse, ServiceHealth, SimplePingResponse

router = APIRouter(prefix="/health", tags=["Health & Diagnostics"])


@router.get(
    "/ping",
    response_model=SimplePingResponse,
    summary="Simple Ping",
    description="Lightweight ping check for load balancers.",
)
async def ping() -> SimplePingResponse:
    return SimplePingResponse(
        status="ok",
        timestamp=datetime.now(timezone.utc),
    )


@router.get(
    "/liveness",
    response_model=SimplePingResponse,
    summary="Liveness Probe",
    description="Confirms that the FastAPI process is running.",
)
async def liveness() -> SimplePingResponse:
    return SimplePingResponse(
        status="alive",
        timestamp=datetime.now(timezone.utc),
    )


@router.get(
    "/readiness",
    summary="Readiness Probe",
    description="Verifies that all critical downstream dependencies (MongoDB, Redis) are ready.",
)
async def readiness() -> JSONResponse:
    mongo_ok = await mongo_manager.ping()
    redis_ok = await redis_manager.ping()

    is_ready = mongo_ok and redis_ok
    status_code = status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ready" if is_ready else "not_ready",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": {
                "mongodb": "connected" if mongo_ok else "disconnected",
                "redis": "connected" if redis_ok else "disconnected",
            },
        },
    )


@router.get(
    "",
    response_model=HealthCheckResponse,
    summary="Comprehensive Health Check",
    description="Performs detailed ping checks with latency tracking for MongoDB, Redis, and Storage.",
)
async def health_check(
    settings: Settings = Depends(get_settings),
) -> HealthCheckResponse:
    services = {}

    # Check MongoDB
    mongo_start = time.perf_counter()
    mongo_ok = await mongo_manager.ping()
    mongo_latency = (time.perf_counter() - mongo_start) * 1000
    services["mongodb"] = ServiceHealth(
        status="connected" if mongo_ok else "disconnected",
        latency_ms=round(mongo_latency, 2) if mongo_ok else None,
        details=f"DB: {settings.mongo.db_name}",
    )

    # Check Redis
    redis_start = time.perf_counter()
    redis_ok = await redis_manager.ping()
    redis_latency = (time.perf_counter() - redis_start) * 1000
    services["redis"] = ServiceHealth(
        status="connected" if redis_ok else "disconnected",
        latency_ms=round(redis_latency, 2) if redis_ok else None,
        details=f"URL: {settings.redis.url}",
    )

    # Check Storage Configuration
    cloudinary_configured = bool(
        settings.cloudinary.cloud_name and settings.cloudinary.api_key
    )
    services["storage"] = ServiceHealth(
        status="configured",
        latency_ms=None,
        details=(
            f"Backend: {settings.storage_backend.value} | "
            f"Cloudinary: {'Ready' if cloudinary_configured else 'Not Configured (Local fallback)'}"
        ),
    )

    overall_status = "healthy" if (mongo_ok and redis_ok) else "degraded"
    if not mongo_ok and not redis_ok:
        overall_status = "unhealthy"

    return HealthCheckResponse(
        status=overall_status,
        app_name=settings.app_name,
        version=settings.project_version,
        environment=settings.app_env.value,
        timestamp=datetime.now(timezone.utc),
        services=services,
    )
