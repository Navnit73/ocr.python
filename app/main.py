"""
FastAPI Application Entry Point.
"""

from contextlib import asynccontextmanager
import logging
from typing import AsyncGenerator
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import router as api_v1_router
from app.core.config import get_settings

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan events for startup and shutdown."""
    settings = get_settings()
    logger.info(f"🚀 Starting {settings.app_name} v{settings.version} in [{settings.environment}] mode...")
    yield
    logger.info(f"🛑 Shutting down {settings.app_name}...")


def create_application() -> FastAPI:
    """Factory function to build and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description="Stateless AI-Powered OCR & Structured Document Extraction API.",
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

    # Exception Handlers
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        req_id = request.headers.get("X-Request-ID", "unknown")
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "id": req_id,
                "status": "error",
                "error": exc.detail,
                "status_code": exc.status_code,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        req_id = request.headers.get("X-Request-ID", "unknown")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "id": req_id,
                "status": "error",
                "error": "Request validation failed",
                "details": exc.errors(),
            },
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        req_id = request.headers.get("X-Request-ID", "unknown")
        logger.error(f"Unhandled error processing request {req_id}: {str(exc)}", exc_info=False)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "id": req_id,
                "status": "error",
                "error": "An internal server error occurred during document processing.",
            },
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

    # Health Check Endpoint
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
