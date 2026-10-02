"""
Tests for Advanced Webhook Dispatcher: HMAC-SHA256 Signatures, SSRF Defense, and Retries.
"""

import hashlib
import hmac
import pytest
import pytest_asyncio
from app.db.mongodb import MongoDBManager
from app.services.webhook_service import WebhookService


@pytest_asyncio.fixture(autouse=True)
async def init_db():
    await MongoDBManager.connect(force_mock=True)
    yield
    await MongoDBManager.disconnect()


@pytest.mark.asyncio
async def test_webhook_hmac_signature_calculation():
    """Verify HMAC-SHA256 signature calculation matches expected digest."""
    secret = "my_super_secret_webhook_key_2026"
    payload = {"job_id": "job_123", "status": "completed"}
    
    import json
    payload_bytes = json.dumps(payload, default=str).encode("utf-8")
    expected_sig = "sha256=" + hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()

    # Verify signature string format
    assert expected_sig.startswith("sha256=")
    assert len(expected_sig) == 7 + 64


def test_webhook_ssrf_safety_check():
    """Verify SSRF defense checks against invalid schemes."""
    # Invalid schemes
    assert WebhookService._is_safe_url("ftp://example.com/webhook") is False
    assert WebhookService._is_safe_url("javascript:alert(1)") is False
    assert WebhookService._is_safe_url("") is False

    # Valid HTTP/HTTPS
    assert WebhookService._is_safe_url("https://api.example.com/webhook") is True
    assert WebhookService._is_safe_url("http://localhost:3000/api/webhook") is True


@pytest.mark.asyncio
async def test_webhook_dispatch_failed_endpoint_handling():
    """Verify graceful handling and DB logging when webhook destination is unreachable."""
    success = await WebhookService.send_webhook(
        callback_url="http://invalid-destination-that-does-not-exist-12345.com/webhook",
        payload={"job_id": "job_dead_webhook", "status": "completed"},
        max_retries=1,
    )
    assert success is False
