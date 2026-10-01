"""
Core OCR and Extraction Schemas.
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class DocumentTypeEnum(str, Enum):
    AUTO = "auto"
    BANK_STATEMENT = "bank_statement"
    RECEIPT = "receipt"
    INVOICE = "invoice"
    GENERAL = "general"


class LanguageEnum(str, Enum):
    AUTO = "auto"
    EN = "en"
    HI = "hi"
    ES = "es"
    FR = "fr"
    DE = "de"
    CH = "ch"


class ExtractionStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    ERROR = "error"
    FAILED = "failed"


class OCRBoundingBox(BaseModel):
    """Bounding box coordinates [x1, y1, x2, y2] or polygon points."""
    points: List[List[float]] = Field(default_factory=list)


class OCRLine(BaseModel):
    """Line-level OCR extraction result."""
    text: str
    confidence: float = Field(ge=0.0, le=1.0)
    bbox: Optional[List[List[float]]] = None


class PageExtraction(BaseModel):
    """Page-level OCR extraction result."""
    page_number: int = Field(ge=1)
    text: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    lines: List[OCRLine] = Field(default_factory=list)
    is_scanned: bool = False


class ProcessingMetadata(BaseModel):
    """Processing metrics and execution details."""
    pages: int = Field(default=1, ge=1)
    ocr_used: bool = False
    ocr_engine: str = "pymupdf"
    ai_cleaned: bool = False
    ai_model: Optional[str] = None
    processing_time_ms: int = Field(default=0, ge=0)
    stage_timings_ms: Dict[str, int] = Field(default_factory=dict)


class ExtractionWarning(BaseModel):
    """Warning or validation notice."""
    code: str
    message: str
    severity: str = "warning"  # info, warning, error


class ExtractionResponse(BaseModel):
    """Unified 3-Layer Extraction Response."""
    id: str = Field(description="Unique request ID supplied by client or generated UUID")
    status: ExtractionStatus = ExtractionStatus.SUCCESS
    document_type: str = DocumentTypeEnum.GENERAL.value
    extraction: Optional[Dict[str, Any]] = None
    raw_text: str = Field(description="Layer 1: Unmodified raw OCR / digital PDF text")
    cleaned_text: Optional[str] = Field(default=None, description="Layer 2: AI cleaned/normalized text")
    pages: List[PageExtraction] = Field(default_factory=list)
    metadata: ProcessingMetadata
    warnings: List[ExtractionWarning] = Field(default_factory=list)
