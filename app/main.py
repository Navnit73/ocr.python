"""
FastAPI Application Entry Point.
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import router as api_v1_router
from app.core.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan events for startup and shutdown."""
    settings = get_settings()
    print(f"🚀 Starting {settings.app_name} v{settings.version} in [{settings.environment}] mode...")
    yield
    print(f"🛑 Shutting down {settings.app_name}...")


def create_application() -> FastAPI:
    """Factory function to build and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description="Clean, modular FastAPI project setup running with Uvicorn / Gunicorn.",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # CORS Middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Root Endpoint
    @app.get("/", tags=["Root"], summary="Root Endpoint")
    async def root() -> JSONResponse:
        return JSONResponse(
            content={
                "name": settings.app_name,
                "version": settings.version,
                "environment": settings.environment,
                "status": "online",
                "docs": "/docs",
                "api_v1": settings.api_v1_prefix,
            }
        )

    # Top-Level Health Check Endpoint
    @app.get("/health", tags=["Health"], summary="System Health Check")
    async def health() -> JSONResponse:
        return JSONResponse(
            content={
                "status": "healthy",
                "environment": settings.environment,
                "version": settings.version,
            }
        )

    # Include API Routers
    app.include_router(api_v1_router, prefix=settings.api_v1_prefix)

    return app


# Application instance
app = create_application()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        workers=settings.workers if not settings.reload else 1,
    )
