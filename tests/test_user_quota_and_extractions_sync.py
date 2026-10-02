"""
Tests for User Email, Quota Sync (Page Credit Reduction), and Shared MongoDB Collections (extractions & users).
"""

import asyncio
from datetime import datetime, timezone
import hashlib
import hmac
import io
import json
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
import pymupdf

from app.main import app
from app.db.mongodb import MongoDBManager
from app.db.repositories.extraction_repo import ExtractionRepository
from app.db.repositories.user_repo import UserRepository
from app.db.repositories.job_repo import JobRepository
from app.services.event_bus import JobEventBus
from app.services.webhook_service import WebhookService
from app.workers.worker_manager import WorkerManager

API_KEY = "ocr_dev_key_secret_2026"
HEADERS = {"X-API-Key": API_KEY}


def create_sample_pdf(num_pages: int = 2, text: str = "Invoice INV-9901 Total $500.00") -> bytes:
    """Helper to generate a test PDF with arbitrary page count."""
    doc = pymupdf.open()
    for i in range(num_pages):
        page = doc.new_page()
        page.insert_text((50, 50), f"Page {i + 1}\n{text}")
    pdf_bytes = doc.write()
    doc.close()
    return pdf_bytes


@pytest_asyncio.fixture(autouse=True)
async def setup_test_env():
    """Starts in-memory MongoDB mock and WorkerManager."""
    JobEventBus.reset()
    await MongoDBManager.connect(force_mock=True)
    await WorkerManager.start()
    yield
    await WorkerManager.stop()
    await MongoDBManager.disconnect()
    JobEventBus.reset()


@pytest.mark.asyncio
async def test_async_upload_syncs_extractions_and_increments_user_quota():
    """
    POST /api/v1/jobs/upload with user_email:
    1. Accepts user_email in form data.
    2. Stores user_email in jobs collection.
    3. Worker processes document and saves record in extractions collection with full schema.
    4. Worker increments pages_processed in users collection by page count.
    """
    user_email = "Finance.Team@Finlyzer.net"
    normalized_email = "finance.team@finlyzer.net"
    pdf_bytes = create_sample_pdf(num_pages=3, text="Bank Statement Closing Balance $12,500.00")
    files = {"file": ("statement.pdf", io.BytesIO(pdf_bytes), "application/pdf")}
    data = {
        "document_type": "bank_statement",
        "clean_with_ai": "false",
        "user_email": user_email,
        "request_id": "doc_fin_stmt_001",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Upload
        res = await client.post("/api/v1/jobs/upload", files=files, data=data, headers=HEADERS)
        assert res.status_code == 202
        body = res.json()
        job_id = body["job_id"]
        doc_id = body["document_id"]
        assert doc_id == "doc_fin_stmt_001"

        # 2. Check jobs collection has user_email
        job_record = await JobRepository.get_job(job_id)
        assert job_record is not None
        assert job_record["user_email"] == normalized_email
        assert job_record["metadata"]["user_email"] == normalized_email

        # 3. Wait for background worker processing
        status_res = {}
        for _ in range(30):
            await asyncio.sleep(0.2)
            check_res = await client.get(f"/api/v1/jobs/{job_id}", headers=HEADERS)
            assert check_res.status_code == 200
            status_res = check_res.json()
            if status_res.get("status") == "completed":
                break

        assert status_res["status"] == "completed"
        assert status_res["user_email"] == normalized_email
        assert status_res["total_pages"] == 3

        # 4. Check shared extractions collection in MongoDB
        db = MongoDBManager.get_db()
        extraction_doc = await db["extractions"].find_one({"id": doc_id})
        assert extraction_doc is not None
        assert extraction_doc["id"] == doc_id
        assert extraction_doc["job_id"] == job_id
        assert extraction_doc["user_email"] == normalized_email
        assert extraction_doc["document_type"] == "bank_statement"
        assert extraction_doc["filename"] == "statement.pdf"
        assert extraction_doc["pages"] == 3
        assert extraction_doc["status"] == "success"
        assert "Bank Statement" in extraction_doc["raw_text"]
        assert extraction_doc["metadata"]["user_email"] == normalized_email
        assert extraction_doc["metadata"]["pages"] == 3
        assert "updated_at" in extraction_doc

        # 5. Check shared users collection in MongoDB for page credit increment
        user_doc = await db["users"].find_one({"email": normalized_email})
        assert user_doc is not None
        assert user_doc["email"] == normalized_email
        assert user_doc["pages_processed"] == 3
        assert "updated_at" in user_doc


@pytest.mark.asyncio
async def test_sync_extract_persists_to_extractions_and_users_quota():
    """
    POST /api/v1/ocr/extract (Synchronous):
    1. Accepts user_email in form data.
    2. Returns extraction response with metadata.user_email.
    3. Persists to extractions collection.
    4. Increments pages_processed on users collection.
    """
    user_email = "analyst@company.org"
    pdf_bytes = create_sample_pdf(num_pages=2, text="Invoice Number INV-2026-X Total $1,200.00")
    files = {"file": ("invoice_x.pdf", io.BytesIO(pdf_bytes), "application/pdf")}
    data = {
        "document_type": "invoice",
        "clean_with_ai": "false",
        "user_email": user_email,
        "request_id": "doc_sync_inv_002",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/api/v1/ocr/extract", files=files, data=data, headers=HEADERS)
        assert res.status_code == 200
        body = res.json()
        assert body["id"] == "doc_sync_inv_002"
        assert body["metadata"]["user_email"] == user_email
        assert body["user_email"] == user_email
        assert body["metadata"]["pages"] == 2

        # Check extractions collection
        db = MongoDBManager.get_db()
        ext_doc = await db["extractions"].find_one({"id": "doc_sync_inv_002"})
        assert ext_doc is not None
        assert ext_doc["user_email"] == user_email
        assert ext_doc["pages"] == 2
        assert ext_doc["status"] == "success"

        # Check users collection
        user_doc = await db["users"].find_one({"email": user_email})
        assert user_doc is not None
        assert user_doc["pages_processed"] == 2


@pytest.mark.asyncio
async def test_batch_extract_syncs_all_items_and_increments_total_quota():
    """
    POST /api/v1/ocr/batch with user_email:
    Processes multiple documents, persists each in extractions collection,
    and increments users.pages_processed by total pages across all documents.
    """
    user_email = "batch_user@finlyzer.net"
    pdf_bytes_1 = create_sample_pdf(num_pages=1, text="Receipt Grocery $45.00")
    pdf_bytes_2 = create_sample_pdf(num_pages=2, text="Receipt Fuel $60.00")

    files = [
        ("files", ("receipt1.pdf", io.BytesIO(pdf_bytes_1), "application/pdf")),
        ("files", ("receipt2.pdf", io.BytesIO(pdf_bytes_2), "application/pdf")),
    ]
    data = {
        "document_type": "receipt",
        "clean_with_ai": "false",
        "user_email": user_email,
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/api/v1/ocr/batch", files=files, data=data, headers=HEADERS)
        assert res.status_code == 200
        body = res.json()
        assert body["total_files"] == 2
        assert body["successful_count"] == 2

        # Verify all batch items stored in extractions collection
        db = MongoDBManager.get_db()
        all_exts = await db["extractions"].find({"user_email": user_email}).to_list(length=10)
        assert len(all_exts) == 2

        # Total pages: 1 + 2 = 3 pages
        user_doc = await db["users"].find_one({"email": user_email})
        assert user_doc is not None
        assert user_doc["pages_processed"] == 3


@pytest.mark.asyncio
async def test_guest_user_does_not_increment_users_collection():
    """
    When user_email is 'guest' or not supplied:
    - Extractions collection records user_email: 'guest'
    - Users collection is NOT modified.
    """
    pdf_bytes = create_sample_pdf(num_pages=1, text="General document sample")
    files = {"file": ("guest_doc.pdf", io.BytesIO(pdf_bytes), "application/pdf")}
    data = {
        "clean_with_ai": "false",
        "user_email": "guest",
        "request_id": "doc_guest_999",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/api/v1/ocr/extract", files=files, data=data, headers=HEADERS)
        assert res.status_code == 200

        db = MongoDBManager.get_db()
        ext_doc = await db["extractions"].find_one({"id": "doc_guest_999"})
        assert ext_doc is not None
        assert ext_doc["user_email"] == "guest"

        # Users collection should not have an entry for 'guest'
        guest_user = await db["users"].find_one({"email": "guest"})
        assert guest_user is None


@pytest.mark.asyncio
async def test_webhook_payload_contains_user_email_and_result(monkeypatch):
    """
    Tests that webhook dispatch on ocr.job.completed sends user_email in:
    1. payload.user_email
    2. payload.metadata.user_email
    3. payload.result.metadata.user_email
    And includes valid HMAC-SHA256 signature header.
    """
    captured_payloads = []
    captured_headers = []

    async def mock_send_webhook(callback_url, payload, secret=None, **kwargs):
        captured_payloads.append(payload)
        payload_bytes = json.dumps(payload, default=str).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if secret:
            sig = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
            headers["X-Webhook-Signature"] = f"sha256={sig}"
        captured_headers.append(headers)
        return True

    monkeypatch.setattr(WebhookService, "send_webhook", mock_send_webhook)

    user_email = "webhook_tester@finlyzer.net"
    callback_url = "http://test-webhook-receiver.local/webhook"
    secret = "secret_webhook_key_2026"

    pdf_bytes = create_sample_pdf(num_pages=2, text="Invoice #9944 Total: $775.00")
    files = {"file": ("inv.pdf", io.BytesIO(pdf_bytes), "application/pdf")}
    data = {
        "document_type": "invoice",
        "clean_with_ai": "false",
        "user_email": user_email,
        "callback_url": callback_url,
        "callback_secret": secret,
        "request_id": "doc_hook_test_001",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/api/v1/jobs/upload", files=files, data=data, headers=HEADERS)
        assert res.status_code == 202
        job_id = res.json()["job_id"]

        # Wait for completion
        for _ in range(30):
            await asyncio.sleep(0.2)
            s = await client.get(f"/api/v1/jobs/{job_id}", headers=HEADERS)
            if s.json().get("status") == "completed":
                break

    # Inspect captured webhook payloads
    completed_payloads = [p for p in captured_payloads if p.get("event") == "ocr.job.completed"]
    assert len(completed_payloads) >= 1
    completed_p = completed_payloads[0]

    assert completed_p["job_id"] == job_id
    assert completed_p["document_id"] == "doc_hook_test_001"
    assert completed_p["status"] == "completed"
    assert completed_p["metadata"]["user_email"] == user_email
    assert completed_p["result"] is not None
    assert completed_p["result"]["metadata"]["user_email"] == user_email
    assert completed_p["result"]["metadata"]["pages"] == 2

    # Check HMAC signature was attached
    assert any("X-Webhook-Signature" in h for h in captured_headers)


@pytest.mark.asyncio
async def test_failed_job_updates_extractions_and_sends_failed_webhook(monkeypatch):
    """
    Tests that a failed job updates extractions collection with status 'error'
    and dispatches ocr.job.failed webhook with metadata.user_email.
    """
    captured_payloads = []

    async def mock_send_webhook(callback_url, payload, **kwargs):
        captured_payloads.append(payload)
        return True

    monkeypatch.setattr(WebhookService, "send_webhook", mock_send_webhook)

    # Force failure in pipeline by invalid file reference
    user_email = "error_tester@finlyzer.net"
    callback_url = "http://test-webhook-receiver.local/webhook"

    job_id = "job_forced_failure"
    doc_id = "doc_forced_failure"

    await JobRepository.create_job({
        "job_id": job_id,
        "document_id": doc_id,
        "user_email": user_email,
        "file_reference": "/invalid/nonexistent/path/file.pdf",
        "callback_url": callback_url,
        "retry_count": 3,  # Exhaust retries so it fails immediately
        "metadata": {"filename": "corrupt.pdf", "user_email": user_email},
    })

    # Process job directly
    await WorkerManager.process_job(job_id)

    # Check extractions collection
    db = MongoDBManager.get_db()
    ext_doc = await db["extractions"].find_one({"id": doc_id})
    assert ext_doc is not None
    assert ext_doc["status"] == "error"
    assert ext_doc["user_email"] == user_email
    assert ext_doc["error"] is not None

    # Check failed webhook payload
    failed_payloads = [p for p in captured_payloads if p.get("event") == "ocr.job.failed"]
    assert len(failed_payloads) >= 1
    failed_p = failed_payloads[0]
    assert failed_p["status"] == "failed"
    assert failed_p["metadata"]["user_email"] == user_email
    assert failed_p["error"] is not None
