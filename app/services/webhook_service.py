"""
Asynchronous Webhook & Callback Notification Service.
Dispatches signed event payloads to client webhooks upon background task completion.
"""

import hashlib
import hmac
import json
import logging
from typing import Any, Dict, Optional
import httpx

from app.core.config import get_settings

logger = logging.getLogger("webhook")


class WebhookService:
    """Dispatches HTTP POST webhooks with HMAC signatures and retry logic."""

    @classmethod
    async def send_webhook(
        cls,
        callback_url: str,
        payload: Dict[str, Any],
        secret: Optional[str] = None,
        max_retries: int = 2,
    ) -> bool:
        """
        Dispatches payload JSON to callback_url with signature headers.
        """
        settings = get_settings()
        payload_bytes = json.dumps(payload, default=str).encode("utf-8")
        req_id = str(payload.get("id") or payload.get("batch_id") or "webhook_event")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "AI-OCR-Webhook-Worker/1.0",
            "X-Request-ID": req_id,
        }

        # Calculate HMAC signature if secret provided
        if secret:
            sig = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
            headers["X-Webhook-Signature"] = f"sha256={sig}"

        timeout = httpx.Timeout(settings.webhook_timeout, connect=5.0)

        for attempt in range(1, max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(callback_url, content=payload_bytes, headers=headers)
                    if response.is_success:
                        logger.info(f"Webhook delivered successfully to {callback_url} for ID {req_id} (Status {response.status_code})")
                        return True
                    else:
                        logger.warning(f"Webhook attempt {attempt} to {callback_url} returned status {response.status_code}")
            except Exception as e:
                logger.warning(f"Webhook delivery attempt {attempt} failed: {str(e)}")

        logger.error(f"Failed to deliver webhook to {callback_url} after {max_retries} attempts for ID {req_id}")
        return False
