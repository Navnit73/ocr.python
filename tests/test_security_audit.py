"""
Security, IDOR, Rate Limiting & Penetration Tests for AI OCR Advance API.
"""

import io
import pytest
from httpx import ASGITransport, AsyncClient
import pymupdf

from app.core.rate_limiter import get_rate_limiter
from app.core.security import hash_key
from app.main import app
from app.services.export_service import ExportService, sanitize_for_formula_injection
from app.services.result_cache import ResultCache


@pytest.fixture(autouse=True)
def reset_limiter():
    get_rate_limiter().reset()
    yield
    get_rate_limiter().reset()


def create_sample_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Test Bank Statement Account 123456 Balance 1000")
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


@pytest.mark.asyncio
async def test_auth_missing_api_key_rejected():
    """Attack vector 2: Check if API works without auth key -> must return 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {"file": ("statement.pdf", create_sample_pdf(), "application/pdf")}
        res = await client.post("/api/v1/ocr/extract", files=files)
        assert res.status_code == 401
        assert "Missing API Key" in res.json()["error"]
        assert "WWW-Authenticate" in res.headers


@pytest.mark.asyncio
async def test_auth_invalid_api_key_rejected():
    """Attack vector 2: Malformed or forged API key -> must return 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {"file": ("statement.pdf", create_sample_pdf(), "application/pdf")}
        headers = {"X-API-Key": "attacker_forged_key_9999"}
        res = await client.post("/api/v1/ocr/extract", files=files, headers=headers)
        assert res.status_code == 401
        assert "Invalid API Key" in res.json()["error"]


@pytest.mark.asyncio
async def test_auth_bearer_token_accepted():
    """Valid Bearer token authentication passes."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {"file": ("statement.pdf", create_sample_pdf(), "application/pdf")}
        headers = {"Authorization": "Bearer ocr_dev_key_secret_2026"}
        data = {"clean_with_ai": "false"}
        res = await client.post("/api/v1/ocr/extract", files=files, data=data, headers=headers)
        assert res.status_code == 200
        assert res.json()["status"] == "success"


@pytest.mark.asyncio
async def test_idor_cross_tenant_data_access_blocked():
    """Attack vector 1: User B tries to download User A's document ID -> must return 403."""
    key_user_a = "ocr_dev_key_secret_2026"
    key_user_b = "ocr_test_key_master"
    doc_id = "user_a_private_payroll_statement"

    # User A extracts and caches document
    ResultCache.set(
        key=doc_id,
        value={
            "id": doc_id,
            "document_type": "bank_statement",
            "extraction": {
                "bank_name": "Private Swiss Bank",
                "account_holder": "User A Confidential",
                "closing_balance": 9999999.0,
            },
        },
        owner_hash=hash_key(key_user_a),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Attacker (User B) attempts to download User A's document by ID
        res_b = await client.get(
            f"/api/v1/export/download/{doc_id}?format=xlsx",
            headers={"X-API-Key": key_user_b},
        )
        assert res_b.status_code == 403
        assert "belongs to another account" in res_b.json()["error"]

        # 2. Legitimate owner (User A) accesses their document
        res_a = await client.get(
            f"/api/v1/export/download/{doc_id}?format=xlsx",
            headers={"X-API-Key": key_user_a},
        )
        assert res_a.status_code == 200
        assert res_a.content[:2] == b"PK"


@pytest.mark.asyncio
async def test_idor_consolidation_blocked_for_foreign_statement():
    """Attack vector 1: User B attempts to consolidate statements owned by User A -> must return 403."""
    key_user_a = "ocr_dev_key_secret_2026"
    key_user_b = "ocr_test_key_master"
    doc_id = "user_a_q1_statement"

    ResultCache.set(
        key=doc_id,
        value={
            "id": doc_id,
            "document_type": "bank_statement",
            "extraction": {"bank_name": "Bank A", "transactions": []},
        },
        owner_hash=hash_key(key_user_a),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {"title": "Annual consolidation", "request_ids": [doc_id]}
        res = await client.post(
            "/api/v1/export/consolidate",
            json=payload,
            headers={"X-API-Key": key_user_b},
        )
        assert res.status_code == 403
        assert "belongs to another account" in res.json()["error"]


@pytest.mark.asyncio
async def test_rate_limiter_exceeded():
    """Attack vector 4: Flooding endpoints triggers 429 Too Many Requests."""
    limiter = get_rate_limiter()
    limiter.reset()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"X-API-Key": "ocr_dev_key_secret_2026"}

        # Simulate hitting rate limit threshold (60 requests/min configured)
        for _ in range(60):
            limiter.check_rate_limit(f"key:{hash_key('ocr_dev_key_secret_2026')}")

        # Next request must be rejected with 429
        payload = {
            "id": "direct_req_rate_test",
            "document_type": "receipt",
            "extraction": {"merchant": "Coffee Shop", "total": 4.50},
        }
        res = await client.post("/api/v1/export/generate?format=csv", json=payload, headers=headers)
        assert res.status_code == 429
        assert "Rate limit exceeded" in res.json()["error"]
        assert "Retry-After" in res.headers
        assert "X-RateLimit-Limit" in res.headers

    # Reset limiter for subsequent test runs
    limiter.reset()


def test_formula_injection_defense():
    """Attack vector 5: CSV/Excel Formula Injection sanitized with prepended single quote."""
    # Malicious inputs trying to trigger OS command execution or DDE in Excel
    malicious_inputs = [
        "=cmd|' /C calc'!A0",
        "@SUM(1+1)*cmd|' /C calc'!A0",
        "-2+3+cmd|' /C calc'!A0",
        "+cmd|' /C calc'!A0",
    ]

    for attack_str in malicious_inputs:
        sanitized = sanitize_for_formula_injection(attack_str)
        assert sanitized.startswith("'"), f"Failed to sanitize formula injection: {attack_str}"

    # Legitimate numbers should NOT be prepended with quote
    assert sanitize_for_formula_injection(100.50) == 100.50
    assert sanitize_for_formula_injection(-50.0) == -50.0
    assert sanitize_for_formula_injection("+50.0") == "+50.0"
    assert sanitize_for_formula_injection("Normal Description") == "Normal Description"


@pytest.mark.asyncio
async def test_security_headers_present():
    """Attack vector 6: Verify security headers prevent MIME-sniffing, clickjacking, etc."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/health")
        assert res.status_code == 200
        assert res.headers.get("X-Content-Type-Options") == "nosniff"
        assert res.headers.get("X-Frame-Options") == "DENY"
        assert res.headers.get("X-XSS-Protection") == "1; mode=block"
        assert "Referrer-Policy" in res.headers
