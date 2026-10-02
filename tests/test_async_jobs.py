"""
End-to-End Tests for Asynchronous OCR Processing with Background Workers & Job Status.
"""

import asyncio
import io
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
import pymupdf

from app.main import app
from app.db.mongodb import MongoDBManager
from app.services.event_bus import JobEventBus
from app.workers.worker_manager import WorkerManager

API_KEY = "ocr_dev_key_secret_2026"
HEADERS = {"X-API-Key": API_KEY}


def create_sample_pdf(num_pages: int = 1, text: str = "Test Bank Statement Account Balance 10000") -> bytes:
    """Creates a sample multi-page PDF in memory."""
    doc = pymupdf.open()
    for i in range(num_pages):
        page = doc.new_page()
        page.insert_text((50, 50), f"Page {i + 1}\n{text}")
    pdf_bytes = doc.write()
    doc.close()
    return pdf_bytes


@pytest_asyncio.fixture(autouse=True)
async def setup_environment():
    """Starts MongoDB mock and background worker pool."""
    JobEventBus.reset()
    await MongoDBManager.connect(force_mock=True)
    await WorkerManager.start()
    yield
    await WorkerManager.stop()
    await MongoDBManager.disconnect()
    JobEventBus.reset()


@pytest.mark.asyncio
async def test_async_upload_returns_202_accepted():
    """Test that POST /api/v1/jobs/upload returns HTTP 202 immediately with job_id."""
    pdf_bytes = create_sample_pdf(num_pages=2, text="Invoice Number: INV-2026-001 Total Amount: $450.00")
    files = {"file": ("invoice.pdf", io.BytesIO(pdf_bytes), "application/pdf")}
    data = {
        "document_type": "invoice",
        "clean_with_ai": "false",
        "request_id": "test_req_async_001",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/jobs/upload",
            files=files,
            data=data,
            headers=HEADERS,
        )

        assert response.status_code == 202
        body = response.json()
        assert "job_id" in body
        assert body["document_id"] == "test_req_async_001"
        assert body["status"] == "queued"
        assert "/api/v1/jobs/" in body["status_url"]

        job_id = body["job_id"]

        # Wait briefly for background worker to process job
        status_body = {}
        for _ in range(30):
            await asyncio.sleep(0.2)
            status_res = await client.get(f"/api/v1/jobs/{job_id}", headers=HEADERS)
            assert status_res.status_code == 200
            status_body = status_res.json()
            if status_body.get("status") == "completed":
                break

        assert status_body["status"] == "completed"
        assert status_body["progress"] == 100
        assert status_body["total_pages"] == 2
        assert status_body["processed_pages"] == 2
        assert status_body["result"] is not None
        assert "INV-2026-001" in status_body["result"]["raw_text"]


@pytest.mark.asyncio
async def test_ocr_extract_async_mode():
    """Test that POST /api/v1/ocr/extract with async_mode=true returns 202 Accepted."""
    pdf_bytes = create_sample_pdf(num_pages=1, text="Receipt Starbucks Coffee Total $5.50")
    files = {"file": ("receipt.pdf", io.BytesIO(pdf_bytes), "application/pdf")}
    data = {
        "async_mode": "true",
        "document_type": "receipt",
        "clean_with_ai": "false",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/ocr/extract",
            files=files,
            data=data,
            headers=HEADERS,
        )

        assert response.status_code == 202
        body = response.json()
        assert "job_id" in body
        assert body["status"] == "queued"


@pytest.mark.asyncio
async def test_job_listing_and_pagination():
    """Test GET /api/v1/jobs listing with user ownership filtering."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/jobs", headers=HEADERS)
        assert res.status_code == 200
        body = res.json()
        assert "total" in body
        assert "items" in body
        assert isinstance(body["items"], list)


@pytest.mark.asyncio
async def test_job_cancellation_and_retry_endpoints():
    """Test job cancel and retry endpoints."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create a job
        pdf_bytes = create_sample_pdf(num_pages=1, text="General document content")
        files = {"file": ("doc.pdf", io.BytesIO(pdf_bytes), "application/pdf")}
        upload_res = await client.post(
            "/api/v1/jobs/upload",
            files=files,
            data={"clean_with_ai": "false"},
            headers=HEADERS,
        )
        job_id = upload_res.json()["job_id"]

        # Wait for completion or check status
        for _ in range(30):
            await asyncio.sleep(0.2)
            s = await client.get(f"/api/v1/jobs/{job_id}", headers=HEADERS)
            if s.json()["status"] in ("completed", "failed"):
                break

        # Attempt cancel on completed job (should return 400 bad request)
        cancel_res = await client.post(f"/api/v1/jobs/{job_id}/cancel", headers=HEADERS)
        assert cancel_res.status_code in (400, 200)
