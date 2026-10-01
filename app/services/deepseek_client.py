"""
DeepSeek AI Async Client with Retries, Timeouts, and JSON mode.
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional
import httpx

from app.core.config import get_settings

logger = logging.getLogger("deepseek_client")


class DeepSeekClient:
    """Async HTTP Client for DeepSeek AI API (OpenAI-compatible format)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[int] = None,
    ):
        settings = get_settings()
        self.api_key = api_key or settings.deepseek_api_key
        self.base_url = (base_url or settings.deepseek_base_url).rstrip("/")
        self.model = model or settings.deepseek_model
        self.timeout = timeout or settings.ai_timeout

    def is_configured(self) -> bool:
        """Returns True if API key is present and configured."""
        return bool(self.api_key and self.api_key.strip() and not self.api_key.startswith("your_"))

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.0,
        json_mode: bool = False,
        max_retries: int = 2,
    ) -> Optional[str]:
        """
        Sends an asynchronous chat completion request to DeepSeek with bounded exponential retries.
        """
        if not self.is_configured():
            logger.warning("DeepSeek API key is not configured. Skipping AI processing.")
            return None

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }

        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        last_exception = None

        for attempt in range(max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(url, headers=headers, json=payload)
                    
                    if response.status_code == 200:
                        data = response.json()
                        content = data["choices"][0]["message"]["content"]
                        return content
                    elif response.status_code in [429, 500, 502, 503, 504]:
                        logger.warning(
                            f"DeepSeek request failed with status {response.status_code} (attempt {attempt + 1}/{max_retries + 1}): {response.text}"
                        )
                        if attempt < max_retries:
                            await asyncio.sleep(1.0 * (2 ** attempt))
                            continue
                    else:
                        logger.error(f"DeepSeek API error {response.status_code}: {response.text}")
                        return None
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                last_exception = e
                logger.warning(
                    f"DeepSeek network/timeout error (attempt {attempt + 1}/{max_retries + 1}): {e}"
                )
                if attempt < max_retries:
                    await asyncio.sleep(1.0 * (2 ** attempt))
                    continue

        logger.error(f"DeepSeek API failed after {max_retries + 1} attempts. Last error: {last_exception}")
        return None
