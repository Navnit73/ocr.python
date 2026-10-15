"""
Bank Reconciliation Schemas.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ReconciliationStatusEnum(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ReconciliationCategoryEnum(str, Enum):
    MATCHED = "matched"
    UNMATCHED_BANK = "unmatched_bank"
    UNMATCHED_LEDGER = "unmatched_ledger"
    POSSIBLE_DUPLICATES = "possible_duplicates"
    AMOUNT_MISMATCHES = "amount_mismatches"


class ReconciliationExportFormatEnum(str, Enum):
    XLSX = "xlsx"
    CSV = "csv"


class ReconciliationUploadResponse(BaseModel):
    """Returned immediately (HTTP 202) after both documents are accepted."""
    success: bool = True
    reconciliation_id: str
    status: ReconciliationStatusEnum = ReconciliationStatusEnum.QUEUED
    message: str
    status_url: str
    access_token: Optional[str] = Field(
        default=None,
        description=(
            "Guest uploads only: secret token returned once. Send it as the 'X-Reconciliation-Token' header "
            "to read the status, transactions, or export. It is never shown again."
        ),
    )
    access_expires_at: Optional[datetime] = Field(default=None, description="Guest uploads only: when guest access expires")


class ReconciliationStatusResponse(BaseModel):
    """Same structure for queued, processing, completed, and failed reconciliations."""
    success: bool = True
    reconciliation_id: str
    status: ReconciliationStatusEnum
    progress: int = Field(ge=0, le=100)
    message: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class ReconciliationTransactionsResponse(BaseModel):
    """Paginated slice of the saved reconciliation report."""
    success: bool = True
    reconciliation_id: str
    status: Optional[ReconciliationCategoryEnum] = None
    total: int
    page: int
    page_size: int
    transactions: List[Dict[str, Any]] = Field(default_factory=list)
