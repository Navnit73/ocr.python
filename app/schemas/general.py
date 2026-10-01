"""
General Document Extraction Schema.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class GeneralExtraction(BaseModel):
    """Fallback extraction model for general / unstructured documents."""
    title: Optional[str] = Field(default=None, description="Inferred document title or header")
    summary: Optional[str] = Field(default=None, description="Brief summary of document content")
    key_value_pairs: Dict[str, Any] = Field(default_factory=dict, description="Identified key-value entities")
    tables: List[List[List[str]]] = Field(default_factory=list, description="Extracted table grids if present")
