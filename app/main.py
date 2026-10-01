"""
FastAPI Application Entrypoint and Lifespan Management.
"""

import os
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_v1_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import logger, setup_logging
from app.db.mongo import mongo_manager
from app.db.redis import redis_manager


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manages application startup and graceful shutdown."""
    settings = get_settings()

    # 1. Initialize logging
    setup_logging(log_level=settings.log_level, log_format=settings.log_format)
    logger.info(f"Starting {settings.app_name} v{settings.project_version} [{settings.app_env.value}]...")

    # 2. Ensure runtime directories exist
    os.makedirs(settings.local_storage_dir, exist_ok=True)
    os.makedirs(settings.temp_file_dir, exist_ok=True)

    # 3. Connect to Database and Cache
    await mongo_manager.connect(settings)
    await redis_manager.connect(settings)

    logger.info("Application startup sequence completed successfully.")
    yield

    # 4. Shutdown sequence
    logger.info("Initiating graceful shutdown sequence...")
    await mongo_manager.disconnect()
    await redis_manager.disconnect()
    logger.info("Application shutdown completed.")


def create_application() -> FastAPI:
    """Factory function to build and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.project_version,
        description=(
            "Production-Ready AI PDF & Image OCR Platform SaaS API. "
            "Supports high-accuracy document intelligence, OCR engines, table extraction, "
            "and AI-assisted verification with strict audit trails."
        ),
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        openapi_url=f"{settings.api_v1_str}/openapi.json",
        lifespan=lifespan,
    )

    # Configure CORS Middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Request Processing Time & Tracing Middleware
    @app.middleware("http")
    async def add_process_time_header(request: Request, call_next):
        start_time = time.perf_counter()
        response = await call_next(request)
        process_time = (time.perf_counter() - start_time) * 1000
        response.headers["X-Process-Time-Ms"] = f"{process_time:.2f}"
        return response

    # Register Exception Handlers
    register_exception_handlers(app)

    # Root Welcome Endpoint
    @app.get("/", tags=["Root"], summary="Root API Information")
    async def root_endpoint() -> JSONResponse:
        return JSONResponse(
            content={
                "name": settings.app_name,
                "version": settings.project_version,
                "environment": settings.app_env.value,
                "status": "online",
                "documentation": "/docs" if not settings.is_production else "disabled in production",
                "api_v1": settings.api_v1_str,
            }
        )

    # Mount API v1 Routes
    app.include_router(api_v1_router, prefix=settings.api_v1_str)

    return app


# Main Application Instance
app = create_application()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=settings.server_reload,
        workers=settings.server_workers,
    )
