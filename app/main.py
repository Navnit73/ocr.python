"""
FastAPI Application Entry Point with Rich OpenAPI / Swagger Documentation.
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
from app.db.mongodb import MongoDBManager
from app.workers.worker_manager import WorkerManager

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

    # Initialize MongoDB connection & indexes
    await MongoDBManager.connect()

    # Start Asynchronous Background Worker Manager & Job Recovery
    await WorkerManager.start()

    yield

    logger.info(f"🛑 Shutting down {settings.app_name}...")
    await WorkerManager.stop()
    await MongoDBManager.disconnect()

    from app.services.deepseek_client import DeepSeekClient
    await DeepSeekClient.close_client()


def create_application() -> FastAPI:
    """Factory function to build and configure the FastAPI application."""
    settings = get_settings()

    tags_metadata = [
        {
            "name": "OCR & Extraction",
            "description": (
                "High-performance document OCR and 3-Layer structured data extraction. "
                "Supports PDFs up to **200 pages / 100MB**, scanned images, password-protected PDFs, "
                "parallel batch extraction (up to 50 files / Zip archives), and asynchronous callback webhooks."
            ),
        },
        {
            "name": "Asynchronous Jobs & Background Workers",
            "description": (
                "Asynchronous document processing lifecycle endpoints:\n"
                "- `POST /api/v1/jobs/upload`: Non-blocking upload returning `HTTP 202 Accepted` immediately\n"
                "- `GET /api/v1/jobs/{job_id}`: Real-time progress and processing stage tracking\n"
                "- `GET /api/v1/jobs/{job_id}/events`: Server-Sent Events (SSE) live push stream\n"
                "- `POST /api/v1/jobs/{job_id}/cancel` & `/retry`: Interactive job control."
            ),
        },
        {
            "name": "Stored Documents & Extractions",
            "description": "Query, search, filter, and delete persistent document extractions in MongoDB.",
        },
        {
            "name": "Admin Dashboard & System Monitoring",
            "description": "Real database metrics, worker CPU/memory health, and global system event stream.",
        },
        {
            "name": "Export & Accounting Downloads",
            "description": (
                "Instant multi-format exports for financial accounting and reporting:\n"
                "- **Executive Excel (.xlsx)**: Multi-sheet workbook with KPI Dashboard & Transaction Ledger\n"
                "- **Fintech-Styled PDF (.pdf)**: Executive report with KPI cards, category progress bars, and audit summary\n"
                "- **Accounting Direct (.ofx / .qbo / .qif)**: 1-click import into QuickBooks, Xero, Zoho Books, Tally\n"
                "- **Annual Consolidation**: Merges multi-month statements into a 12-month Annual Cashflow & P&L report."
            ),
        },
        {
            "name": "Health",
            "description": "System liveness, readiness, and connectivity health check endpoints.",
        },
    ]

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description=(
            "### 🚀 Enterprise AI-Powered Document OCR & Financial Intelligence API\n\n"
            "An ultra-fast, production-grade REST API to extract, clean, structure, and export documents:\n\n"
            "- **Asynchronous Processing**: Upload large **100–200 page documents**, get immediate `202 Accepted`, and track progress via SSE or Webhooks\n"
            "- **Background Workers**: Dedicated background worker pool with Celery & Redis support, crash recovery, and concurrency management\n"
            "- **Real-Time Notifications**: Server-Sent Events (SSE) `/api/v1/jobs/{job_id}/events` & Signed HMAC-SHA256 Webhooks\n"
            "- **Persistent Storage**: MongoDB persistence for extractions, jobs, and webhook logs with in-memory caching fallback\n"
            "- **Admin Dashboard**: Live system analytics, page metrics, duration tracking, and worker health monitoring\n"
            "- **Authentication**: Use `X-API-Key` header or `Authorization: Bearer <key>`\n"
            "- **Rate Limiting**: Protected with in-memory sliding window limiter\n"
            "- **Accounting Direct Exports**: Direct `.ofx`, `.qbo` (QuickBooks), `.qif`, `.xlsx`, `.pdf`, and `.csv` generation."
        ),
        openapi_tags=tags_metadata,
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url="/redoc" if settings.enable_docs else None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
        lifespan=lifespan,
    )

    # Security Headers Middleware
    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

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
            headers=exc.headers,
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
