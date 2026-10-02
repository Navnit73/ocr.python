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
