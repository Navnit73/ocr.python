"""
Tests for Large Multi-Page PDF Document Processing and Progress Callbacks.
"""

import asyncio
import pytest
import pytest_asyncio
import pymupdf
from app.db.mongodb import MongoDBManager
from app.services.pipeline import ExtractionPipeline


@pytest_asyncio.fixture(autouse=True)
async def init_db():
    await MongoDBManager.connect(force_mock=True)
    yield
    await MongoDBManager.disconnect()


def generate_multipage_pdf(num_pages: int) -> bytes:
    """Generates a synthetic multi-page PDF document."""
    doc = pymupdf.open()
    for i in range(num_pages):
        page = doc.new_page()
        page.insert_text(
            (50, 72),
            f"STATEMENT OF ACCOUNT - PAGE {i + 1}\n"
            f"Date: 2026-01-{min(28, i + 1):02d}\n"
            f"Description: Transaction on page {i + 1}\n"
            f"Amount: ${(i + 1) * 10.50:.2f}\n"
        )
    pdf_bytes = doc.write()
    doc.close()
    return pdf_bytes


@pytest.mark.asyncio
async def test_multipage_pdf_progress_tracking():
    """Verify that progress callbacks are triggered and increment correctly for 10-page document."""
    pdf_bytes = generate_multipage_pdf(10)
    pipeline = ExtractionPipeline()

    recorded_progress = []

    async def _progress_callback(processed_pages: int, total_pages: int, stage: str, progress_pct: int, message: str):
        recorded_progress.append({
            "processed": processed_pages,
            "total": total_pages,
            "stage": stage,
            "pct": progress_pct,
        })

    response = await pipeline.process_document(
        file=None,
        file_bytes=pdf_bytes,
        filename="10_page_statement.pdf",
        clean_with_ai=False,
        progress_callback=_progress_callback,
    )

    assert response.status.value == "success"
    assert response.metadata.pages == 10
    assert len(response.pages) == 10

    # Ensure progress callback was invoked multiple times with increasing progress
    assert len(recorded_progress) >= 5
    # Final progress callback should reach 100%
    assert recorded_progress[-1]["pct"] == 100
    assert recorded_progress[-1]["stage"] == "completed"


@pytest.mark.asyncio
async def test_50_page_pdf_capacity():
    """Verify processing a 50-page PDF executes within memory and returns all pages."""
    pdf_bytes = generate_multipage_pdf(50)
    pipeline = ExtractionPipeline()

    response = await pipeline.process_document(
        file=None,
        file_bytes=pdf_bytes,
        filename="50_page_statement.pdf",
        clean_with_ai=False,
    )

    assert response.metadata.pages == 50
    assert len(response.pages) == 50
    assert "PAGE 50" in response.raw_text


@pytest.mark.asyncio
async def test_100_page_pdf_chunking_10_parts():
    """Verify that a 100-page PDF is broken into 10 parts of 10 pages and processed sequentially."""
    pdf_bytes = generate_multipage_pdf(100)
    pipeline = ExtractionPipeline()

    part_messages = []

    async def _progress_callback(processed_pages: int, total_pages: int, stage: str, progress_pct: int, message: str):
        if "Processing Part" in message:
            part_messages.append(message)

    response = await pipeline.process_document(
        file=None,
        file_bytes=pdf_bytes,
        filename="100_page_statement.pdf",
        clean_with_ai=False,
        progress_callback=_progress_callback,
    )

    assert response.status.value == "success"
    assert response.metadata.pages == 100
    assert len(response.pages) == 100
    assert "PAGE 1" in response.raw_text
    assert "PAGE 50" in response.raw_text
    assert "PAGE 100" in response.raw_text

    # Verify that exactly 10 parts of 10 pages were processed sequentially
    assert len(part_messages) == 10
    assert "Part 1 of 10 (Pages 1-10)" in part_messages[0]
    assert "Part 5 of 10 (Pages 41-50)" in part_messages[4]
    assert "Part 10 of 10 (Pages 91-100)" in part_messages[9]


@pytest.mark.asyncio
async def test_200_page_pdf_chunking_20_parts():
    """Verify that a 200-page PDF is broken into 20 parts of 10 pages and processed sequentially."""
    pdf_bytes = generate_multipage_pdf(200)
    pipeline = ExtractionPipeline()

    part_messages = []
    completed_messages = []

    async def _progress_callback(processed_pages: int, total_pages: int, stage: str, progress_pct: int, message: str):
        if "Processing Part" in message:
            part_messages.append(message)
        elif "Completed Part" in message:
            completed_messages.append(message)

    response = await pipeline.process_document(
        file=None,
        file_bytes=pdf_bytes,
        filename="200_page_statement.pdf",
        clean_with_ai=False,
        progress_callback=_progress_callback,
    )

    assert response.status.value == "success"
    assert response.metadata.pages == 200
    assert len(response.pages) == 200
    assert "PAGE 1" in response.raw_text
    assert "PAGE 100" in response.raw_text
    assert "PAGE 200" in response.raw_text

    # Verify that exactly 20 parts of 10 pages each were processed sequentially
    assert len(part_messages) == 20
    assert "Part 1 of 20 (Pages 1-10)" in part_messages[0]
    assert "Part 10 of 20 (Pages 91-100)" in part_messages[9]
    assert "Part 20 of 20 (Pages 191-200)" in part_messages[19]

    assert len(completed_messages) == 20
    assert "Completed Part 20 of 20" in completed_messages[-1]


def test_multi_chunk_bank_statement_structured_merge():
    """Verify merging 20 chunk extractions consolidates all 200 transactions and preserves balances."""
    from app.services.extractor import StructuredExtractor

    extractor = StructuredExtractor()
    chunk_extractions = []

    for i in range(1, 21):
        # 20 parts: part 1 has opening balance 10000.0, part 20 has closing balance 15000.0
        part_data = {
            "bank_name": "Chase Bank",
            "account_holder": "John Doe",
            "account_number_masked": "XXXX-XXXX-1234",
            "currency": "USD",
            "statement_period": "2026-01-01 to 2026-01-31",
            "opening_balance": 10000.0 if i == 1 else None,
            "closing_balance": 15000.0 if i == 20 else None,
            "transactions": [
                {
                    "date": "2026-01-15",
                    "description": f"Transaction Part {i} Item {j}",
                    "reference": f"REF-{i}-{j}",
                    "debit": 50.0 if j % 2 == 0 else 0.0,
                    "credit": 100.0 if j % 2 != 0 else 0.0,
                    "balance": 10000.0 + (i * 100),
                }
                for j in range(1, 11)
            ],
        }
        chunk_extractions.append(part_data)

    merged, warnings = extractor.merge_extractions(chunk_extractions, "bank_statement")

    assert merged is not None
    assert merged["bank_name"] == "Chase Bank"
    assert merged["account_holder"] == "John Doe"
    assert merged["account_number_masked"] == "XXXX-XXXX-1234"
    assert merged["opening_balance"] == 10000.0
    assert merged["closing_balance"] == 15000.0
    assert len(merged["transactions"]) == 200  # 20 chunks * 10 transactions = 200
    assert merged["transactions"][0]["description"] == "Transaction Part 1 Item 1"
    assert merged["transactions"][-1]["description"] == "Transaction Part 20 Item 10"


def test_multi_chunk_invoice_and_receipt_structured_merge():
    """Verify merging invoice and receipt line items across multiple chunks."""
    from app.services.extractor import StructuredExtractor

    extractor = StructuredExtractor()

    # Invoice merge
    invoice_chunks = [
        {
            "invoice_number": "INV-2026-999",
            "invoice_date": "2026-01-10",
            "supplier": {"name": "Acme Corp", "email": "billing@acme.com"},
            "customer": {"name": "Client Inc", "address": "123 Main St"},
            "currency": "USD",
            "line_items": [
                {"description": "Server hosting", "quantity": 1, "unit_price": 500.0, "amount": 500.0},
            ],
        },
        {
            "invoice_number": None,
            "supplier": {"tax_id": "TAX-123456"},
            "subtotal": 1500.0,
            "tax": 150.0,
            "total": 1650.0,
            "line_items": [
                {"description": "Consulting services", "quantity": 10, "unit_price": 100.0, "amount": 1000.0},
            ],
        }
    ]

    merged_inv, _ = extractor.merge_extractions(invoice_chunks, "invoice")
    assert merged_inv["invoice_number"] == "INV-2026-999"
    assert merged_inv["supplier"]["name"] == "Acme Corp"
    assert merged_inv["supplier"]["tax_id"] == "TAX-123456"
    assert len(merged_inv["line_items"]) == 2
    assert merged_inv["total"] == 1650.0
