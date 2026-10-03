"""
Services package initialization.
"""

from app.services.file_validator import FileValidator, managed_temp_file
from app.services.image_service import ImageService
from app.services.pdf_service import PDFService
from app.services.ocr_service import OCRService
from app.services.engine_interface import (
    BaseExtractionEngine,
    EngineResult,
    ExtractedHeading,
    ExtractedTable,
)
from app.services.pymupdf_engine import PyMuPDFEngine
from app.services.paddleocr_engine import PaddleOCREngine
from app.services.docling_engine import DoclingEngine
from app.services.document_normalizer import DocumentNormalizer
from app.services.extraction_router import ExtractionRouter
from app.services.deepseek_client import DeepSeekClient
from app.services.ai_cleaner import AICleaner
from app.services.classifier import DocumentClassifier
from app.services.extractor import StructuredExtractor
from app.services.analytics_service import AnalyticsService
from app.services.export_service import ExportService
from app.services.result_cache import ResultCache
from app.services.pipeline import ExtractionPipeline

__all__ = [
    "FileValidator",
    "managed_temp_file",
    "ImageService",
    "PDFService",
    "OCRService",
    "BaseExtractionEngine",
    "EngineResult",
    "ExtractedHeading",
    "ExtractedTable",
    "PyMuPDFEngine",
    "PaddleOCREngine",
    "DoclingEngine",
    "DocumentNormalizer",
    "ExtractionRouter",
    "DeepSeekClient",
    "AICleaner",
    "DocumentClassifier",
    "StructuredExtractor",
    "AnalyticsService",
    "ExportService",
    "ResultCache",
    "ExtractionPipeline",
]
