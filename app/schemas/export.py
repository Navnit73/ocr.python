"""
Schemas for Document Export (Excel, CSV, PDF).
"""

from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class ExportFormatEnum(str, Enum):
    XLSX = "xlsx"
    EXCEL = "excel"
    CSV = "csv"
    PDF = "pdf"
    OFX = "ofx"
    QBO = "qbo"
    QIF = "qif"


class DirectExportRequest(BaseModel):
    """Direct export request payload containing extraction data."""
    id: str = Field(default="export_doc", description="Document / Request ID")
    document_type: str = Field(default="bank_statement", description="Document category (bank_statement, receipt, invoice, general)")
    extraction: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Structured extraction JSON")
    raw_text: Optional[str] = Field(default="", description="Raw extracted text")
