"""
Tests for Financial Document Processing with IBM Docling Integration.
"""

import pytest
import pymupdf
from app.db.mongodb import MongoDBManager
from app.schemas.ocr import DocumentTypeEnum, ExtractionEngineEnum
from app.services.pipeline import ExtractionPipeline


def generate_bank_statement_pdf() -> bytes:
    """Generates a synthetic PDF bank statement with transactions table."""
    doc = pymupdf.open()
    page = doc.new_page()

    page.insert_text((50, 40), "NATIONAL RESERVE BANK - STATEMENT OF ACCOUNT", fontsize=14)
    page.insert_text((50, 65), "Account Holder: Acme Enterprises Inc", fontsize=10)
    page.insert_text((50, 80), "Account Number: 9876-5432-1098 | Currency: USD", fontsize=10)
    page.insert_text((50, 95), "Statement Period: 2026-01-01 to 2026-01-31", fontsize=10)
    page.insert_text((50, 110), "Opening Balance: $5000.00 | Closing Balance: $5750.00", fontsize=10)

    # Table
    page.draw_rect(pymupdf.Rect(50, 130, 520, 240), width=1)
    page.draw_line(pymupdf.Point(50, 155), pymupdf.Point(520, 155), width=1)
    page.insert_text((55, 148), "Date", fontsize=9)
    page.insert_text((140, 148), "Description", fontsize=9)
    page.insert_text((330, 148), "Debit", fontsize=9)
    page.insert_text((400, 148), "Credit", fontsize=9)
    page.insert_text((460, 148), "Balance", fontsize=9)

    page.insert_text((55, 175), "2026-01-05", fontsize=9)
    page.insert_text((140, 175), "Client Payment Ref 001", fontsize=9)
    page.insert_text((400, 175), "1000.00", fontsize=9)
    page.insert_text((460, 175), "6000.00", fontsize=9)

    page.insert_text((55, 205), "2026-01-15", fontsize=9)
    page.insert_text((140, 205), "Server Hosting Fees", fontsize=9)
    page.insert_text((330, 205), "250.00", fontsize=9)
    page.insert_text((460, 205), "5750.00", fontsize=9)

    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def generate_invoice_pdf() -> bytes:
    """Generates a synthetic PDF invoice with items table."""
    doc = pymupdf.open()
    page = doc.new_page()

    page.insert_text((50, 40), "TAX INVOICE #INV-2026-443", fontsize=14)
    page.insert_text((50, 65), "Supplier: Apex Software Solutions", fontsize=10)
    page.insert_text((50, 80), "Customer: Global Logistics Ltd", fontsize=10)
    page.insert_text((50, 95), "Invoice Date: 2026-02-15 | Due Date: 2026-03-15", fontsize=10)

    page.draw_rect(pymupdf.Rect(50, 120, 500, 200), width=1)
    page.draw_line(pymupdf.Point(50, 145), pymupdf.Point(500, 145), width=1)

    page.insert_text((55, 138), "Description", fontsize=9)
    page.insert_text((280, 138), "Qty", fontsize=9)
    page.insert_text((360, 138), "Unit Price", fontsize=9)
    page.insert_text((440, 138), "Amount", fontsize=9)

    page.insert_text((55, 165), "Enterprise API Subscription", fontsize=9)
    page.insert_text((280, 165), "1", fontsize=9)
    page.insert_text((360, 165), "1200.00", fontsize=9)
    page.insert_text((440, 165), "1200.00", fontsize=9)

    page.insert_text((50, 220), "Subtotal: $1200.00 | Tax: $120.00 | Total: $1320.00", fontsize=10)

    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


@pytest.mark.asyncio
async def test_docling_bank_statement_pipeline():
    """Verify end-to-end bank statement processing with Docling."""
    await MongoDBManager.connect(force_mock=True)
    try:
        pdf_bytes = generate_bank_statement_pdf()
        pipeline = ExtractionPipeline()

        response = await pipeline.process_document(
            file=None,
            file_bytes=pdf_bytes,
            filename="bank_statement.pdf",
            document_type=DocumentTypeEnum.BANK_STATEMENT.value,
            extraction_engine=ExtractionEngineEnum.DOCLING.value,
            clean_with_ai=False,
        )

        assert response.status.value in ("success", "partial_success")
        assert response.metadata.extraction_engine == "docling"
        assert response.extraction is not None
        assert response.raw_text is not None
        assert "NATIONAL RESERVE BANK" in response.raw_text
        assert response.tables is not None or len(response.pages) >= 1
    finally:
        await MongoDBManager.disconnect()


@pytest.mark.asyncio
async def test_docling_invoice_pipeline():
    """Verify end-to-end invoice processing with Docling."""
    await MongoDBManager.connect(force_mock=True)
    try:
        pdf_bytes = generate_invoice_pdf()
        pipeline = ExtractionPipeline()

        response = await pipeline.process_document(
            file=None,
            file_bytes=pdf_bytes,
            filename="invoice.pdf",
            document_type=DocumentTypeEnum.INVOICE.value,
            extraction_engine=ExtractionEngineEnum.DOCLING.value,
            clean_with_ai=False,
        )

        assert response.status.value in ("success", "partial_success")
        assert response.metadata.extraction_engine == "docling"
        assert "INV-2026-443" in response.raw_text
        assert response.extraction is not None
    finally:
        await MongoDBManager.disconnect()
