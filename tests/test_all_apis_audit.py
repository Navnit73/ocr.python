"""
Comprehensive End-to-End API Audit Test Suite.
Audits every single API endpoint across all routers for correctness, security, status codes, and data integrity.
"""

import io
import json
import zipfile
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
import pymupdf

from app.core.config import get_settings
from app.db.mongodb import MongoDBManager
from app.db.repositories.document_repo import DocumentRepository
from app.db.repositories.job_repo import JobRepository
from app.workers.worker_manager import WorkerManager
from app.main import app

AUTH_HEADER = {"X-API-Key": "ocr_dev_key_secret_2026"}
ADMIN_HEADER = {"X-API-Key": "ocr_test_key_master"}
OTHER_AUTH_HEADER = {"X-API-Key": "ocr_test_key_master"}


@pytest_asyncio.fixture(autouse=True)
async def init_db():
    await MongoDBManager.connect(force_mock=True)
    await WorkerManager.start()
    yield
    await WorkerManager.stop()
    await MongoDBManager.disconnect()


def generate_test_pdf(text: str = "Invoice #INV-2026-001\nTotal: $1,500.00\nDate: 2026-01-15") -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 72), text)
    pdf_bytes = doc.write()
    doc.close()
    return pdf_bytes


# ==========================================
# 1. HEALTH CHECKS
# ==========================================
@pytest.mark.asyncio
async def test_audit_health_endpoints():
    """Audit GET /health, GET /api/v1/health, and GET /api/v1/ping."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Root health
        res = await client.get("/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "healthy"

        # V1 health
        res_v1 = await client.get("/api/v1/health")
        assert res_v1.status_code == 200
        assert res_v1.json()["status"] == "healthy"

        # Ping
        res_ping = await client.get("/api/v1/ping")
        assert res_ping.status_code == 200
        assert res_ping.json()["ping"] == "pong"


# ==========================================
# 2. JOBS & ASYNC PROCESSING ENDPOINTS
# ==========================================
@pytest.mark.asyncio
async def test_audit_jobs_lifecycle_and_endpoints():
    """Audit POST /jobs/upload, GET /jobs/{id}, GET /jobs, POST /jobs/{id}/cancel, POST /jobs/{id}/retry."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pdf_bytes = generate_test_pdf("Bank Statement Chase\nAccount: XXXX-1234\nOpening Balance: $5000.00\nClosing Balance: $6000.00")

        # 1. POST /api/v1/jobs/upload
        files = {"file": ("test_statement.pdf", pdf_bytes, "application/pdf")}
        data = {
            "document_type": "bank_statement",
            "language": "en",
            "clean_with_ai": "false",
        }
        res = await client.post("/api/v1/jobs/upload", files=files, data=data, headers=AUTH_HEADER)
        assert res.status_code == 202
        body = res.json()
        assert "job_id" in body
        assert "document_id" in body
        assert body["status"] == "queued"
        assert "/api/v1/jobs/" in body["status_url"]

        job_id = body["job_id"]
        doc_id = body["document_id"]

        # 2. GET /api/v1/jobs/{job_id}
        res_job = await client.get(f"/api/v1/jobs/{job_id}", headers=AUTH_HEADER)
        assert res_job.status_code == 200
        job_body = res_job.json()
        assert job_body["job_id"] == job_id
        assert job_body["document_id"] == doc_id
        assert job_body["status"] in ("queued", "processing", "completed")

        # 3. GET /api/v1/jobs (List)
        res_list = await client.get("/api/v1/jobs?page=1&page_size=10", headers=AUTH_HEADER)
        assert res_list.status_code == 200
        list_body = res_list.json()
        assert list_body["total"] >= 1
        assert any(j["job_id"] == job_id for j in list_body["items"])

        # 4. POST /api/v1/jobs/{job_id}/cancel
        res_cancel = await client.post(f"/api/v1/jobs/{job_id}/cancel", headers=AUTH_HEADER)
        assert res_cancel.status_code in (200, 400)
        if res_cancel.status_code == 200:
            assert res_cancel.json()["status"] == "cancelled"

        # 5. POST /api/v1/jobs/{job_id}/retry
        res_retry = await client.post(f"/api/v1/jobs/{job_id}/retry", headers=AUTH_HEADER)
        assert res_retry.status_code in (200, 400)
        if res_retry.status_code == 200:
            assert res_retry.json()["status"] == "queued"


# ==========================================
# 3. OCR EXTRACTION (SYNC & ASYNC)
# ==========================================
@pytest.mark.asyncio
async def test_audit_ocr_extract_endpoints():
    """Audit POST /ocr/extract (sync) and POST /ocr/extract-async."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pdf_bytes = generate_test_pdf("Invoice #INV-8899\nDate: 2026-02-01\nTotal: $2,500.00")

        # 1. POST /api/v1/ocr/extract (Synchronous)
        files = {"file": ("invoice.pdf", pdf_bytes, "application/pdf")}
        data = {"document_type": "invoice", "clean_with_ai": "false"}
        res_sync = await client.post("/api/v1/ocr/extract", files=files, data=data, headers=AUTH_HEADER)
        assert res_sync.status_code == 200
        sync_body = res_sync.json()
        assert sync_body["status"] == "success"
        assert sync_body["document_type"] == "invoice"
        assert sync_body["metadata"]["pages"] == 1
        assert "INV-8899" in sync_body["raw_text"]

        # 2. POST /api/v1/ocr/extract-async
        files_async = {"file": ("invoice_async.pdf", pdf_bytes, "application/pdf")}
        res_async = await client.post("/api/v1/ocr/extract-async", files=files_async, data=data, headers=AUTH_HEADER)
        assert res_async.status_code == 202
        async_body = res_async.json()
        assert async_body["status"] == "queued"
        assert "job_id" in async_body


# ==========================================
# 4. BATCH EXTRACTION (FILES & ZIP)
# ==========================================
@pytest.mark.asyncio
async def test_audit_ocr_batch_endpoints():
    """Audit POST /ocr/batch with multiple files and zip archive."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pdf1 = generate_test_pdf("Receipt 1 Total: $45.00")
        pdf2 = generate_test_pdf("Receipt 2 Total: $55.00")

        # 1. Multi-file upload
        files = [
            ("files", ("receipt1.pdf", pdf1, "application/pdf")),
            ("files", ("receipt2.pdf", pdf2, "application/pdf")),
        ]
        data = {"document_type": "receipt", "clean_with_ai": "false"}
        res_batch = await client.post("/api/v1/ocr/batch", files=files, data=data, headers=AUTH_HEADER)
        assert res_batch.status_code == 200
        batch_body = res_batch.json()
        assert batch_body["total_files"] == 2
        assert batch_body["successful_count"] == 2

        # 2. Zip archive upload
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w") as zf:
            zf.writestr("doc_a.pdf", pdf1)
            zf.writestr("doc_b.pdf", pdf2)
        zip_bytes = zip_buf.getvalue()

        zip_files = [("files", ("archive.zip", zip_bytes, "application/zip"))]
        res_zip = await client.post("/api/v1/ocr/batch", files=zip_files, data=data, headers=AUTH_HEADER)
        assert res_zip.status_code == 200
        zip_body = res_zip.json()
        assert zip_body["total_files"] == 2
        assert zip_body["successful_count"] == 2


# ==========================================
# 5. DOCUMENTS STORAGE & RETRIEVAL
# ==========================================
@pytest.mark.asyncio
async def test_audit_documents_endpoints():
    """Audit GET /documents, GET /documents/{id}, DELETE /documents/{id}."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # First extract a document to persist it
        pdf_bytes = generate_test_pdf("General Report 2026\nQuarterly Earnings Overview")
        files = {"file": ("report.pdf", pdf_bytes, "application/pdf")}
        data = {"document_type": "general", "clean_with_ai": "false"}
        res_extract = await client.post("/api/v1/ocr/extract", files=files, data=data, headers=AUTH_HEADER)
        assert res_extract.status_code == 200
        doc_id = res_extract.json()["id"]

        # 1. GET /api/v1/documents
        res_list = await client.get("/api/v1/documents?search=report", headers=AUTH_HEADER)
        assert res_list.status_code == 200
        docs_body = res_list.json()
        assert docs_body["total"] >= 1
        assert any(d["id"] == doc_id for d in docs_body["items"])

        # 2. GET /api/v1/documents/{document_id}
        res_get = await client.get(f"/api/v1/documents/{doc_id}", headers=AUTH_HEADER)
        assert res_get.status_code == 200
        assert res_get.json()["id"] == doc_id
        assert "Quarterly Earnings" in res_get.json()["raw_text"]

        # 3. DELETE /api/v1/documents/{document_id}
        res_del = await client.delete(f"/api/v1/documents/{doc_id}", headers=AUTH_HEADER)
        assert res_del.status_code == 200
        assert res_del.json()["status"] == "deleted"

        # 4. Verify 404 after deletion
        res_after = await client.get(f"/api/v1/documents/{doc_id}", headers=AUTH_HEADER)
        assert res_after.status_code == 404


# ==========================================
# 6. EXPORTS & DOWNLOADS
# ==========================================
@pytest.mark.asyncio
async def test_audit_export_endpoints():
    """Audit GET /export/download/{id} across all formats and POST /export/generate & /export/consolidate."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Extract a statement first
        pdf_bytes = generate_test_pdf(
            "Chase Bank Statement\nAccount No: XXXX-9988\nOpening: $10000.00\nClosing: $12000.00\n2026-01-10 Deposit $2000.00"
        )
        files = {"file": ("statement_export.pdf", pdf_bytes, "application/pdf")}
        res_ext = await client.post("/api/v1/ocr/extract", files=files, data={"document_type": "bank_statement", "clean_with_ai": "false"}, headers=AUTH_HEADER)
        assert res_ext.status_code == 200
        doc_id = res_ext.json()["id"]

        # 1. Test all 6 download formats: xlsx, pdf, csv, ofx, qbo, qif
        for fmt in ["xlsx", "pdf", "csv", "ofx", "qbo", "qif"]:
            res_down = await client.get(f"/api/v1/export/download/{doc_id}?format={fmt}", headers=AUTH_HEADER)
            assert res_down.status_code == 200, f"Export format {fmt} failed"
            assert len(res_down.content) > 0
            assert "Content-Disposition" in res_down.headers

        # 2. POST /api/v1/export/generate (Direct Export)
        gen_payload = {
            "id": "doc_direct_test",
            "document_type": "bank_statement",
            "extraction": {
                "bank_name": "Direct Bank",
                "account_holder": "Direct User",
                "opening_balance": 1000.0,
                "closing_balance": 2000.0,
                "transactions": [
                    {"date": "2026-01-01", "description": "Salary", "debit": None, "credit": 1000.0, "balance": 2000.0}
                ]
            },
            "raw_text": "Direct Bank Salary 1000",
        }
        res_gen = await client.post("/api/v1/export/generate?format=xlsx", json=gen_payload, headers=AUTH_HEADER)
        assert res_gen.status_code == 200
        assert len(res_gen.content) > 0

        # 3. POST /api/v1/export/consolidate (Excel & JSON)
        cons_payload = {
            "request_ids": [doc_id],
            "title": "2026 Annual Audit",
        }
        # As Excel
        res_cons_xl = await client.post("/api/v1/export/consolidate?as_excel=true", json=cons_payload, headers=AUTH_HEADER)
        assert res_cons_xl.status_code == 200
        assert len(res_cons_xl.content) > 0

        # As JSON
        res_cons_json = await client.post("/api/v1/export/consolidate?as_excel=false", json=cons_payload, headers=AUTH_HEADER)
        assert res_cons_json.status_code == 200
        cons_data = res_cons_json.json()
        assert cons_data["statement_count"] >= 1


# ==========================================
# 7. ADMIN DASHBOARD & MONITORING
# ==========================================
@pytest.mark.asyncio
async def test_audit_admin_endpoints():
    """Audit GET /admin/stats and GET /admin/jobs."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. GET /api/v1/admin/stats
        res_stats = await client.get("/api/v1/admin/stats", headers=ADMIN_HEADER)
        assert res_stats.status_code == 200
        stats = res_stats.json()
        assert "total_jobs" in stats
        assert "worker_health" in stats
        assert stats["worker_health"]["mongodb_connected"] is True

        # 2. GET /api/v1/admin/jobs
        res_jobs = await client.get("/api/v1/admin/jobs?page=1&page_size=10", headers=ADMIN_HEADER)
        assert res_jobs.status_code == 200
        admin_jobs = res_jobs.json()
        assert "total" in admin_jobs
        assert "items" in admin_jobs


# ==========================================
# 8. SECURITY, AUTHENTICATION & IDOR AUDIT
# ==========================================
@pytest.mark.asyncio
async def test_audit_security_and_error_handling():
    """Audit 401 Unauthorized, 403 Forbidden (IDOR), 404 Not Found, 400 Bad Request."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Missing API Key -> 401
        res_unauth = await client.get("/api/v1/documents")
        assert res_unauth.status_code == 401

        # 2. Invalid API Key -> 401
        res_invalid = await client.get("/api/v1/documents", headers={"X-API-Key": "invalid_fake_key"})
        assert res_invalid.status_code == 401

        # 3. Create document with User A
        pdf = generate_test_pdf("Secret Doc User A")
        files = {"file": ("secret.pdf", pdf, "application/pdf")}
        res_a = await client.post("/api/v1/ocr/extract", files=files, data={"clean_with_ai": "false"}, headers=AUTH_HEADER)
        assert res_a.status_code == 200
        doc_id_a = res_a.json()["id"]

        # 4. User B attempts to access User A's document -> 404 (IDOR Defense)
        res_b = await client.get(f"/api/v1/documents/{doc_id_a}", headers=OTHER_AUTH_HEADER)
        assert res_b.status_code == 404

        # 5. User B attempts to download User A's document export -> 403/404 (IDOR Defense)
        res_b_down = await client.get(f"/api/v1/export/download/{doc_id_a}?format=xlsx", headers=OTHER_AUTH_HEADER)
        assert res_b_down.status_code in (403, 404)

        # 6. Invalid / Corrupted File -> 400
        bad_file = {"file": ("corrupted.pdf", b"NOT_A_PDF_CORRUPTED_BYTES", "application/pdf")}
        res_bad = await client.post("/api/v1/ocr/extract", files=bad_file, data={"clean_with_ai": "false"}, headers=AUTH_HEADER)
        assert res_bad.status_code == 400
