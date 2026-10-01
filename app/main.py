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
    from app.services.deepseek_client import DeepSeekClient
    await DeepSeekClient.close_client()


def create_application() -> FastAPI:
    """Factory function to build and configure the FastAPI application."""
    settings = get_settings()

    tags_metadata = [
        {
            "name": "OCR & Extraction",
            "description": (
                "High-performance, stateless document OCR and 3-Layer structured data extraction. "
                "Supports PDFs up to **200 pages / 100MB**, scanned images, password-protected PDFs, "
                "parallel batch extraction (up to 50 files / Zip archives), and asynchronous callback webhooks."
            ),
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
            "An ultra-fast, stateless REST API to extract, clean, structure, and export documents:\n\n"
            "- **Supported Documents**: Bank Statements, Invoices, Receipts, Tax Docs, General PDFs & Scanned Images\n"
            "- **Capacity**: Up to **200 pages** per PDF and up to **100MB** payload size\n"
            "- **3-Layer Output**: Layer 1 (Raw OCR), Layer 2 (Prompt-Injection-Safe Cleaned Text), Layer 3 (Pydantic-Validated Structured JSON)\n"
            "- **Financial Integrity**: Mathematical balance audits and tax validation preserving raw source figures\n"
            "- **Accounting Direct Exports**: Direct `.ofx`, `.qbo` (QuickBooks), `.qif`, `.xlsx`, `.pdf`, and `.csv` generation\n"
            "- **Batch & Webhooks**: Parallel multi-file/Zip processing and signed async webhooks (`callback_url`)\n"
            "- **Stateless**: No MongoDB, Redis, or Celery required. In-memory TTL caching with guaranteed temporary file cleanup."
        ),
        openapi_tags=tags_metadata,
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
