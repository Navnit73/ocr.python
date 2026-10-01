"""
API v1 Endpoints package initialization.
"""

from app.api.v1.endpoints.ocr import router as ocr_router

__all__ = ["ocr_router"]
