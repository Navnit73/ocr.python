"""
Schema package initialization.
"""

from app.schemas.ocr import (
    DocumentTypeEnum,
    LanguageEnum,
    ExtractionStatus,
    OCRBoundingBox,
    OCRLine,
    PageExtraction,
    ProcessingMetadata,
    ExtractionWarning,
    ExtractionResponse,
)
from app.schemas.bank_statement import BankTransaction, BankStatementExtraction
from app.schemas.receipt import ReceiptLineItem, ReceiptExtraction
from app.schemas.invoice import InvoiceLineItem, PartyDetails, InvoiceExtraction
from app.schemas.general import GeneralExtraction
from app.schemas.job import (
    JobStatusEnum,
    JobStageEnum,
    JobCreateResponse,
    JobStatusResponse,
    JobListResponse,
    JobEventPayload,
)
from app.schemas.document import (
    DocumentListItem,
    DocumentListResponse,
    DocumentDetailResponse,
)
from app.schemas.webhook import (
    WebhookEventEnum,
    WebhookPayload,
    WebhookDeliveryRecord,
)
from app.schemas.admin import (
    AdminStatsResponse,
    AdminJobListResponse,
    WorkerHealthInfo,
)

__all__ = [
    "DocumentTypeEnum",
    "LanguageEnum",
    "ExtractionStatus",
    "OCRBoundingBox",
    "OCRLine",
    "PageExtraction",
    "ProcessingMetadata",
    "ExtractionWarning",
    "ExtractionResponse",
    "BankTransaction",
    "BankStatementExtraction",
    "ReceiptLineItem",
    "ReceiptExtraction",
    "InvoiceLineItem",
    "PartyDetails",
    "InvoiceExtraction",
    "GeneralExtraction",
    "JobStatusEnum",
    "JobStageEnum",
    "JobCreateResponse",
    "JobStatusResponse",
    "JobListResponse",
    "JobEventPayload",
    "DocumentListItem",
    "DocumentListResponse",
    "DocumentDetailResponse",
    "WebhookEventEnum",
    "WebhookPayload",
    "WebhookDeliveryRecord",
    "AdminStatsResponse",
    "AdminJobListResponse",
    "WorkerHealthInfo",
]
