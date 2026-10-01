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
]
