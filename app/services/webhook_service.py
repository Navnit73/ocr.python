"""
Asynchronous Webhook & Callback Notification Service.
Dispatches signed event payloads to client webhooks upon background task progress and completion.
Includes HMAC-SHA256 signatures, SSRF prevention, exponential backoff retries, and audit logging.
"""

import asyncio
from datetime import datetime, timezone
import hashlib
import hmac
import ipaddress
import json
import logging
import socket
from urllib.parse import urlparse
import uuid
from typing import Any, Dict, Optional
import httpx

from app.core.config import get_settings
from app.db.repositories.webhook_repo import WebhookRepository

logger = logging.getLogger("webhook")


class WebhookService:
    """Dispatches secure HTTP POST webhooks with HMAC signatures and retry logic."""

    @classmethod
    def _is_safe_url(cls, url: str) -> bool:
        """
        Validates URL to protect against Server-Side Request Forgery (SSRF).
        In production, blocks private, loopback, and link-local IP addresses.
        In development/test, permits localhost and test destinations.
        """
        try:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                return False

            hostname = parsed.hostname
            if not hostname:
                return False

            settings = get_settings()
            # If in development or test, allow local webhook destinations
            if settings.environment in ("development", "test", "testing"):
                return True

            # Resolve IP to check for private / reserved ranges
            try:
                ip_addr = socket.gethostbyname(hostname)
                ip = ipaddress.ip_address(ip_addr)
                if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
                    logger.warning(f"Blocked potential SSRF webhook URL: {url} (resolved to {ip_addr})")
                    return False
            except Exception:
                # If DNS resolution fails here, reject in production
                return False

            return True
        except Exception as e:
            logger.warning(f"URL safety check failed for {url}: {e}")
            return False

    @classmethod
    async def dispatch_event(
        cls,
        event_name: str,
        job_id: str,
        document_id: Optional[str],
        callback_url: Optional[str],
        callback_secret: Optional[str],
        status: str,
        progress: Optional[int] = None,
        current_stage: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        result_url: Optional[str] = None,
    ) -> bool:
        """
        Constructs and dispatches a standard WebhookPayload to the configured callback_url.
        """
        if not callback_url:
            return False

        if not cls._is_safe_url(callback_url):
            logger.warning(f"Skipping webhook delivery to unsafe destination: {callback_url}")
            return False

        event_id = f"evt_{uuid.uuid4().hex[:16]}"
        now = datetime.now(timezone.utc)

        payload = {
            "event": event_name,
            "event_id": event_id,
            "job_id": job_id,
            "document_id": document_id,
            "status": status,
            "timestamp": now.isoformat(),
            "metadata": metadata or {},
        }
        if progress is not None:
            payload["progress"] = progress
        if current_stage:
            payload["current_stage"] = current_stage
        if result_url:
            payload["result_url"] = result_url
        if error:
            payload["error"] = error

        return await cls.send_webhook(
            callback_url=callback_url,
            payload=payload,
            secret=callback_secret,
            event_id=event_id,
            job_id=job_id,
            event_name=event_name,
        )

    @classmethod
    async def send_webhook(
        cls,
        callback_url: str,
        payload: Dict[str, Any],
        secret: Optional[str] = None,
        max_retries: int = 3,
        event_id: Optional[str] = None,
        job_id: Optional[str] = None,
        event_name: Optional[str] = None,
    ) -> bool:
        """
        Dispatches payload JSON to callback_url with signature headers and exponential backoff retry.
        """
        settings = get_settings()
        payload_bytes = json.dumps(payload, default=str).encode("utf-8")
        req_id = job_id or str(payload.get("job_id") or payload.get("id") or "webhook_event")
        evt_id = event_id or str(payload.get("event_id") or f"evt_{uuid.uuid4().hex[:12]}")
        evt_name = event_name or str(payload.get("event") or "ocr.job.event")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "AI-OCR-Webhook-Worker/2.0",
            "X-Request-ID": req_id,
            "X-Event-ID": evt_id,
            "X-Event-Type": evt_name,
        }

        # Calculate HMAC signature if secret provided
        if secret:
            sig = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
            headers["X-Webhook-Signature"] = f"sha256={sig}"

        timeout = httpx.Timeout(settings.webhook_timeout, connect=5.0)
        last_status: Optional[int] = None
        last_error: Optional[str] = None

        for attempt in range(1, max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(callback_url, content=payload_bytes, headers=headers)
                    last_status = response.status_code
                    if response.is_success:
                        logger.info(
                            f"✅ Webhook '{evt_name}' delivered successfully to {callback_url} "
                            f"for job {req_id} on attempt {attempt} (Status {response.status_code})"
                        )
                        # Record successful delivery
                        await WebhookRepository.record_delivery(
                            job_id=req_id,
                            event=evt_name,
                            url=callback_url,
                            status_code=last_status,
                            success=True,
                            attempts=attempt,
                            delivery_id=evt_id,
                        )
                        return True
                    else:
                        last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                        logger.warning(
                            f"Webhook attempt {attempt} for job {req_id} to {callback_url} returned {response.status_code}"
                        )
            except Exception as e:
                last_error = str(e)
                logger.warning(f"Webhook delivery attempt {attempt} for job {req_id} failed: {last_error}")

            if attempt < max_retries:
                # Exponential backoff: 1s, 2s, 4s...
                backoff_delay = 2 ** (attempt - 1)
                await asyncio.sleep(backoff_delay)

        # Record failed delivery
        await WebhookRepository.record_delivery(
            job_id=req_id,
            event=evt_name,
            url=callback_url,
            status_code=last_status,
            success=False,
            attempts=max_retries,
            error=last_error,
            delivery_id=evt_id,
        )
        logger.error(f"❌ Failed to deliver webhook to {callback_url} after {max_retries} attempts for job {req_id}")
        return False
