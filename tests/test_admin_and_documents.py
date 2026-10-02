"""
Tests for Admin Dashboard endpoints and Stored Documents management.
"""

import io
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
import pymupdf

from app.main import app
from app.db.mongodb import MongoDBManager
from app.services.event_bus import JobEventBus
from app.workers.worker_manager import WorkerManager

USER_KEY = "ocr_dev_key_secret_2026"
ADMIN_KEY = "ocr_test_key_master"
USER_HEADERS = {"X-API-Key": USER_KEY}
ADMIN_HEADERS = {"X-API-Key": ADMIN_KEY}


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    JobEventBus.reset()
    await MongoDBManager.connect(force_mock=True)
    await WorkerManager.start()
    yield
    await WorkerManager.stop()
    await MongoDBManager.disconnect()
    JobEventBus.reset()


def create_pdf(text: str = "Test document") -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 50), text)
    data = doc.write()
    doc.close()
    return data


@pytest.mark.asyncio
async def test_admin_stats_endpoint():
    """Verify GET /api/v1/admin/stats returns real database metrics."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # User key should get 403 Forbidden for admin endpoint
        user_res = await client.get("/api/v1/admin/stats", headers=USER_HEADERS)
        assert user_res.status_code == 403

        # Admin key should succeed
        admin_res = await client.get("/api/v1/admin/stats", headers=ADMIN_HEADERS)
        assert admin_res.status_code == 200
        stats = admin_res.json()
        assert "total_jobs" in stats
        assert "worker_health" in stats
        assert "uptime_seconds" in stats["worker_health"]


@pytest.mark.asyncio
async def test_documents_crud_and_idor_protection():
    """Verify Documents listing, retrieval by ID, and IDOR protection."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Upload a document
        pdf_bytes = create_pdf("Receipt from Coffee Shop Total: $4.50")
        files = {"file": ("receipt.pdf", io.BytesIO(pdf_bytes), "application/pdf")}
        upload_res = await client.post(
            "/api/v1/ocr/extract",
            files=files,
            data={"document_type": "receipt", "clean_with_ai": "false", "request_id": "test_doc_crud_1"},
            headers=USER_HEADERS,
        )
        assert upload_res.status_code == 200
        doc_id = upload_res.json()["id"]

        # 1. List Documents
        list_res = await client.get("/api/v1/documents", headers=USER_HEADERS)
        assert list_res.status_code == 200
        items = list_res.json()["items"]
        assert any(d["id"] == doc_id for d in items)

        # 2. Get Document by ID
        get_res = await client.get(f"/api/v1/documents/{doc_id}", headers=USER_HEADERS)
        assert get_res.status_code == 200
        assert get_res.json()["id"] == doc_id

        # 3. IDOR test: Unauthorized caller with different key
        other_headers = {"X-API-Key": "my_production_secret_key"}
        idor_res = await client.get(f"/api/v1/documents/{doc_id}", headers=other_headers)
        assert idor_res.status_code == 404

        # 4. Delete Document
        del_res = await client.delete(f"/api/v1/documents/{doc_id}", headers=USER_HEADERS)
        assert del_res.status_code == 200
        assert del_res.json()["status"] == "deleted"
