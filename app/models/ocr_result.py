"""
OCR Result and Document Intelligence Data Models.
Maintains pristine original OCR text and a tamper-proof audit trail for all AI corrections.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class BoundingBox(BaseModel):
    """Normalized polygon or bounding box coordinates."""
    x_min: float
    y_min: float
    x_max: float
    y_max: float
    polygon: Optional[List[List[float]]] = None


class OCRTextLine(BaseModel):
    """Individual line or word recognized during OCR."""
    text: str
    confidence: float = Field(ge=0.0, le=1.0)
    bbox: Optional[BoundingBox] = None
    page_number: int = 1


class AIModificationRecord(BaseModel):
    """Audit trail record for an AI-suggested or applied text correction."""
    page_number: int
    original_text: str
    corrected_text: str
    category: str = Field(description="e.g., 'spelling', 'formatting', 'syntax'")
    confidence: float
    reason: str
    applied: bool = True
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OCRPageResult(BaseModel):
    """Full extraction result for a single page."""
    page_number: int
    raw_ocr_text: str = Field(description="Raw untouched text straight from OCR engine")
    cleaned_ocr_text: Optional[str] = Field(default=None, description="Pre-processed / normalized text")
    ai_corrected_text: Optional[str] = Field(default=None, description="LLM corrected text with audit trail")
    confidence_score: float = Field(ge=0.0, le=1.0)
    lines: List[OCRTextLine] = Field(default_factory=list)
    tables: List[Dict[str, Any]] = Field(default_factory=list)
    ai_audit_trail: List[AIModificationRecord] = Field(default_factory=list)


class OCRResultModel(BaseModel):
    """Complete document OCR result entity stored in MongoDB."""
    id: Optional[str] = Field(default=None, description="MongoDB Document ID")
    document_id: str
    user_id: str
    job_id: str
    engine_used: str
    
    # Combined texts
    raw_full_text: str = Field(description="Unaltered raw output from the OCR engine")
    final_full_text: str = Field(description="Final output incorporating verified corrections")
    
    pages: List[OCRPageResult] = Field(default_factory=list)
    total_pages: int = 1
    average_confidence: float = Field(ge=0.0, le=1.0)
    
    # Document classification / layout metadata
    document_type: Optional[str] = None
    extracted_entities: Dict[str, Any] = Field(default_factory=dict)
    
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
