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


try:
    import json_repair
except ImportError:
    json_repair = None


def safe_json_loads(text: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Safely parses JSON from LLM responses.
    Handles <think> tags, markdown code blocks, trailing commas, single quotes,
    unescaped control characters, comments, and truncated JSON streams.
    """
    if not text or not str(text).strip():
        return None

    cleaned = str(text).strip()

    # 1. Strip reasoning / thinking tags (<think>...</think>)
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", cleaned, flags=re.IGNORECASE).strip()

    # 2. Strip markdown code fences (```json ... ``` or ``` ... ```)
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    # 3. Direct JSON parse attempt with non-strict control character handling
    try:
        parsed = json.loads(cleaned, strict=False)
        if isinstance(parsed, dict):
            return parsed
        elif isinstance(parsed, list):
            return {"items": parsed}
    except Exception:
        pass

    # 4. Use json_repair library if available
    if json_repair is not None:
        try:
            repaired = json_repair.loads(cleaned)
            if isinstance(repaired, dict):
                return repaired
            elif isinstance(repaired, list):
                return {"items": repaired}
        except Exception:
            pass

    # 5. Native Regex & Stack-based JSON Repair Fallback
    try:
        # Extract candidate JSON boundary
        first_brace = cleaned.find("{")
        first_bracket = cleaned.find("[")
        start_idx = -1
        if first_brace != -1 and first_bracket != -1:
            start_idx = min(first_brace, first_bracket)
        elif first_brace != -1:
            start_idx = first_brace
        elif first_bracket != -1:
            start_idx = first_bracket

        candidate = cleaned[start_idx:] if start_idx != -1 else cleaned

        # Clean single-line and multi-line comments
        candidate = re.sub(r"//.*?\n", "\n", candidate)
        candidate = re.sub(r"/\*[\s\S]*?\*/", "", candidate)

        # Remove trailing commas before closing braces/brackets
        candidate = re.sub(r",\s*([\]}])", r"\1", candidate)

        try:
            parsed = json.loads(candidate, strict=False)
            if isinstance(parsed, dict):
                return parsed
            elif isinstance(parsed, list):
                return {"items": parsed}
        except Exception:
            pass

        # Handle Truncated JSON Streams (auto-close open strings and brackets)
        in_str = False
        escape = False
        stack = []
        for c in candidate:
            if escape:
                escape = False
                continue
            if c == "\\":
                escape = True
                continue
            if c == '"':
                in_str = not in_str
                continue
            if not in_str:
                if c in ("{", "["):
                    stack.append("}" if c == "{" else "]")
                elif c in ("}", "]"):
                    if stack and stack[-1] == c:
                        stack.pop()

        repaired_str = candidate
        if in_str:
            repaired_str += '"'
        repaired_str = re.sub(r",\s*$", "", repaired_str)
        while stack:
            repaired_str += stack.pop()

        try:
            parsed = json.loads(repaired_str, strict=False)
            if isinstance(parsed, dict):
                return parsed
            elif isinstance(parsed, list):
                return {"items": parsed}
        except Exception:
            pass

        # Slice back to last valid closed sub-object if still truncated
        last_brace = candidate.rfind("}")
        if last_brace != -1:
            sub = candidate[:last_brace + 1]
            sub = re.sub(r",\s*$", "", sub)
            sub_stack = []
            sub_in_str = False
            sub_esc = False
            for c in sub:
                if sub_esc:
                    sub_esc = False
                    continue
                if c == "\\":
                    sub_esc = True
                    continue
                if c == '"':
                    sub_in_str = not sub_in_str
                    continue
                if not sub_in_str:
                    if c in ("{", "["):
                        sub_stack.append("}" if c == "{" else "]")
                    elif c in ("}", "]"):
                        if sub_stack and sub_stack[-1] == c:
                            sub_stack.pop()
            while sub_stack:
                sub += sub_stack.pop()
            try:
                parsed = json.loads(sub, strict=False)
                if isinstance(parsed, dict):
                    return parsed
                elif isinstance(parsed, list):
                    return {"items": parsed}
            except Exception:
                pass

    except Exception as repair_exc:
        logger.warning(f"Error during native JSON repair: {repair_exc}")

    return None


class DeepSeekClient:
    """Async HTTP Client for DeepSeek AI API with connection pooling and retries."""

    _shared_client: Optional[httpx.AsyncClient] = None
    _loop: Optional[asyncio.AbstractEventLoop] = None
    _lock: Optional[asyncio.Lock] = None

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
    def _get_lock(cls) -> asyncio.Lock:
        current_loop = None
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        if cls._lock is None or (current_loop and getattr(cls._lock, "_loop", None) not in (None, current_loop)):
            cls._lock = asyncio.Lock()
        return cls._lock

    @classmethod
    async def get_http_client(cls, timeout_sec: int = 60) -> httpx.AsyncClient:
        """
        Returns or creates a shared persistent httpx.AsyncClient bound to the current running event loop.
        """
        current_loop = asyncio.get_running_loop()
        if (
            cls._shared_client is None
            or cls._shared_client.is_closed
            or cls._loop != current_loop
        ):
            lock = cls._get_lock()
            async with lock:
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
            "max_tokens": 8192,
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
