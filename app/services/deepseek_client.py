"""
DeepSeek AI Async Client with Persistent Connection Pooling, Retries, and Safe JSON Parsing.
"""

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional
import httpx

from app.core.config import get_settings

logger = logging.getLogger("deepseek_client")


def safe_json_loads(text: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Safely parses JSON from LLM responses, stripping markdown code blocks
    and extracting nested JSON objects/arrays if present.
    """
    if not text or not text.strip():
        return None

    cleaned = text.strip()

    # Strip markdown code blocks: ```json ... ``` or ``` ... ```
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

    # Direct JSON parse attempt
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Find the outermost '{ ... }' or '[ ... ]'
    match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", cleaned)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    return None


class DeepSeekClient:
    """Async HTTP Client for DeepSeek AI API with connection pooling and retries."""

    _shared_client: Optional[httpx.AsyncClient] = None
    _loop: Optional[asyncio.AbstractEventLoop] = None
    _lock = asyncio.Lock()

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[int] = None,
    ):
        settings = get_settings()
        self.api_key = api_key if api_key is not None else settings.deepseek_api_key
        self.base_url = (base_url if base_url is not None else settings.deepseek_base_url).rstrip("/")
        self.model = model or settings.deepseek_model
        self.timeout = timeout or settings.ai_timeout

    def is_configured(self) -> bool:
        """Returns True if API key is present and configured."""
        return bool(self.api_key and self.api_key.strip() and not self.api_key.startswith("your_"))

    @classmethod
    async def get_http_client(cls, timeout_sec: int = 45) -> httpx.AsyncClient:
        """
        Returns or creates a shared persistent httpx.AsyncClient bound to the current running event loop.
        """
        current_loop = asyncio.get_running_loop()
        if (
            cls._shared_client is None
            or cls._shared_client.is_closed
            or cls._loop != current_loop
        ):
            async with cls._lock:
                if (
                    cls._shared_client is None
                    or cls._shared_client.is_closed
                    or cls._loop != current_loop
                ):
                    limits = httpx.Limits(max_keepalive_connections=20, max_connections=50)
                    cls._shared_client = httpx.AsyncClient(
                        limits=limits,
                        timeout=httpx.Timeout(timeout_sec, connect=10.0),
                    )
                    cls._loop = current_loop
        return cls._shared_client

    @classmethod
    async def close_client(cls):
        """Closes the shared HTTP client during application shutdown."""
        if cls._shared_client and not cls._shared_client.is_closed:
            await cls._shared_client.aclose()
            cls._shared_client = None

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
        client = await self.get_http_client(self.timeout)

        for attempt in range(max_retries + 1):
            try:
                response = await client.post(url, headers=headers, json=payload)
                
                if response.status_code == 200:
                    data = response.json()
                    content = data["choices"][0]["message"]["content"]
                    return content
                elif response.status_code in [429, 500, 502, 503, 504]:
                    logger.warning(
                        f"DeepSeek request failed with status {response.status_code} (attempt {attempt + 1}/{max_retries + 1})"
                    )
                    if attempt < max_retries:
                        await asyncio.sleep(0.5 * (2 ** attempt))
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
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue

        logger.error(f"DeepSeek API failed after {max_retries + 1} attempts. Last error: {last_exception}")
        return None
