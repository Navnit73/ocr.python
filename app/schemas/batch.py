"""
Schemas for Batch Processing and Multi-Statement Consolidation.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from app.schemas.ocr import ExtractionResponse, ExtractionStatus, ExtractionWarning


class BatchItemResult(BaseModel):
    """Result for an individual file in a batch extraction."""
    id: str = Field(description="Unique processing request ID for this file")
    filename: str = Field(description="Original filename")
    status: ExtractionStatus = Field(description="Extraction outcome (success, partial_success, failed)")
    document_type: str = Field(description="Detected document type")
    extraction: Optional[Dict[str, Any]] = Field(default=None, description="Structured extraction JSON")
    raw_text: Optional[str] = Field(default="", description="Extracted raw text snippet")
    warnings: List[ExtractionWarning] = Field(default_factory=list, description="Extraction or audit warnings")
    error: Optional[str] = Field(default=None, description="Error message if failed")
    processing_time_ms: int = Field(default=0, description="Processing duration in milliseconds")


class BatchExtractionResponse(BaseModel):
    """Response returned by the batch extraction endpoint."""
    batch_id: str = Field(description="Unique identifier for the batch request")
    total_files: int = Field(description="Total number of documents received")
    successful_count: int = Field(description="Number of successfully extracted documents")
    failed_count: int = Field(description="Number of failed extractions")
    total_processing_time_ms: int = Field(description="Total batch duration in milliseconds")
    consolidated_inflow: float = Field(default=0.0, description="Sum of all inflows across all batch files")
    consolidated_outflow: float = Field(default=0.0, description="Sum of all outflows across all batch files")
    net_consolidated_savings: float = Field(default=0.0, description="Net savings (Inflows - Outflows) across the batch")
    items: List[BatchItemResult] = Field(default_factory=list, description="Itemized extraction results per document")


class MonthlyRollup(BaseModel):
    """Aggregated financial metrics for a specific calendar month."""
    month: str = Field(description="Month in YYYY-MM format")
    inflow: float = Field(default=0.0, description="Total credits/deposits for the month")
    outflow: float = Field(default=0.0, description="Total debits/expenses for the month")
    net_savings: float = Field(default=0.0, description="Net cashflow for the month")
    transaction_count: int = Field(default=0, description="Number of transactions in the month")


class ConsolidationRequest(BaseModel):
    """Payload to request multi-statement consolidation."""
    request_ids: Optional[List[str]] = Field(default=None, description="List of cached request IDs to consolidate")
    extractions: Optional[List[Dict[str, Any]]] = Field(default=None, description="Raw extraction objects to consolidate")
    title: Optional[str] = Field(default="Annual Financial Consolidation", description="Report title")


class ConsolidationResponse(BaseModel):
    """Consolidated financial intelligence rollup across multiple statements."""
    consolidation_id: str = Field(description="Consolidation request identifier")
    statement_count: int = Field(description="Number of statements merged")
    total_transactions: int = Field(description="Total deduplicated transactions")
    currency: str = Field(default="INR", description="Primary currency")
    consolidated_inflow: float = Field(description="Total combined inflow")
    consolidated_outflow: float = Field(description="Total combined outflow")
    net_savings: float = Field(description="Total combined net savings")
    overall_savings_rate_pct: float = Field(description="Overall savings percentage")
    monthly_trends: List[MonthlyRollup] = Field(default_factory=list, description="Month-by-month financial breakdown")
    ai_insights: List[str] = Field(default_factory=list, description="Macro AI financial insights across all statements")
