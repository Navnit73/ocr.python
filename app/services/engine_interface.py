"""
Document Extraction Engine Interface and Data Models.
Provides a unified contract for PyMuPDF, PaddleOCR, and IBM Docling extraction engines.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from app.schemas.ocr import ExtractionWarning, OCRLine, PageExtraction


@dataclass
class ExtractedTable:
    """Represents a structured table extracted from a document."""
    page_number: int
    table_index: int
    num_rows: int
    num_cols: int
    headers: List[str] = field(default_factory=list)
    grid: List[List[str]] = field(default_factory=list)
    markdown: str = ""
    bbox: Optional[List[float]] = None
    confidence: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        """Converts table data to dictionary format."""
        return {
            "page_number": self.page_number,
            "table_index": self.table_index,
            "num_rows": self.num_rows,
            "num_cols": self.num_cols,
            "headers": self.headers,
            "grid": self.grid,
            "markdown": self.markdown,
            "bbox": self.bbox,
            "confidence": self.confidence,
        }


@dataclass
class ExtractedHeading:
    """Represents a heading / section header."""
    text: str
    level: int = 1
    page_number: int = 1

    def to_dict(self) -> Dict[str, Any]:
        """Converts heading data to dictionary format."""
        return {
            "text": self.text,
            "level": self.level,
            "page_number": self.page_number,
        }


@dataclass
class EngineResult:
    """Normalized document extraction output produced by any engine."""
    engine_name: str
    pages: List[PageExtraction] = field(default_factory=list)
    raw_text: str = ""
    markdown: Optional[str] = None
    tables: List[ExtractedTable] = field(default_factory=list)
    headings: List[ExtractedHeading] = field(default_factory=list)
    structured_json: Optional[Dict[str, Any]] = None
    is_scanned: bool = False
    confidence: float = 1.0
    warnings: List[ExtractionWarning] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class BaseExtractionEngine(Protocol):
    """Protocol for OCR / document extraction engines."""

    @property
    def engine_name(self) -> str:
        """Returns the identifier name of the engine."""
        ...

    async def extract_document(
        self,
        file_bytes: bytes,
        filename: str,
        doc_category: str = "pdf",
        language: str = "en",
        password: Optional[str] = None,
        enable_ocr: bool = True,
        extract_tables: bool = True,
        **kwargs: Any,
    ) -> EngineResult:
        """
        Asynchronously processes a document and returns a standardized EngineResult.
        """
        ...
