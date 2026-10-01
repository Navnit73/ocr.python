"""
Tests for AI Cleaner Service and DeepSeek Client with Mocked Responses.
"""

import json
import pytest
from unittest.mock import AsyncMock, patch

from app.services.ai_cleaner import AICleaner
from app.services.deepseek_client import DeepSeekClient


def test_whitespace_normalization():
    raw = "Line 1   with   spaces\n\n\n\nLine 2\n\n   \nLine 3"
    cleaned = AICleaner.normalize_whitespace(raw)
    assert cleaned == "Line 1 with spaces\n\nLine 2\n\nLine 3"


@pytest.mark.asyncio
async def test_ai_cleaner_when_not_configured():
    # When api key is empty, should cleanly return normalized text
    client = DeepSeekClient(api_key="")
    cleaner = AICleaner(client)
    raw = "Account Numbr:  123456"
    cleaned, warnings, review = await cleaner.clean_ocr_text(raw)
    assert cleaned == "Account Numbr: 123456"
    assert review is False


@pytest.mark.asyncio
async def test_ai_cleaner_mocked_success():
    client = DeepSeekClient(api_key="mock_key_test")
    cleaner = AICleaner(client)

    mock_response = json.dumps({
        "cleaned_text": "Account Number: 123456\nBalance: $500.00",
        "corrections": [
            {"original": "Acc0unt Numbr", "corrected": "Account Number", "reason": "OCR character fix"}
        ],
        "warnings": ["Low confidence on header"],
        "review_required": False
    })

    with patch.object(client, "chat_completion", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = mock_response

        cleaned, warnings, review = await cleaner.clean_ocr_text("Acc0unt Numbr: 123456\nBalance: $500.00")
        assert cleaned == "Account Number: 123456\nBalance: $500.00"
        assert len(warnings) == 1
        assert warnings[0].message == "Low confidence on header"
        assert review is False


@pytest.mark.asyncio
async def test_ai_cleaner_timeout_fallback():
    client = DeepSeekClient(api_key="mock_key_test")
    cleaner = AICleaner(client)

    with patch.object(client, "chat_completion", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = None  # Simulating timeout / network error

        cleaned, warnings, review = await cleaner.clean_ocr_text("Raw text content")
        assert cleaned == "Raw text content"
        assert len(warnings) == 1
        assert warnings[0].code == "AI_CLEANING_SKIPPED"


def test_safe_json_loads_with_markdown():
    from app.services.deepseek_client import safe_json_loads

    # Direct JSON
    assert safe_json_loads('{"key": "value"}') == {"key": "value"}

    # Markdown fenced JSON with ```json
    markdown_json = "```json\n{\n  \"cleaned_text\": \"Hello World\",\n  \"warnings\": []\n}\n```"
    parsed = safe_json_loads(markdown_json)
    assert parsed is not None
    assert parsed["cleaned_text"] == "Hello World"

    # Fenced JSON with leading and trailing text
    preamble_json = "Here is your JSON output:\n```\n{\"total\": 150.0}\n```\nHope that helps!"
    parsed_preamble = safe_json_loads(preamble_json)
    assert parsed_preamble is not None
    assert parsed_preamble["total"] == 150.0

    # Invalid string
    assert safe_json_loads("not a json object") is None

