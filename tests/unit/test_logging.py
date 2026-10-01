"""
Unit tests for Logging Formatters.
"""

import json
import logging
from app.core.logging import JSONFormatter, ConsoleFormatter, setup_logging


def test_json_formatter():
    """Tests JSON structured logging output."""
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=42,
        msg="Test logging message",
        args=(),
        exc_info=None,
    )
    formatted = formatter.format(record)
    parsed = json.loads(formatted)

    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "test_logger"
    assert parsed["message"] == "Test logging message"
    assert parsed["lineNo"] == 42
    assert "timestamp" in parsed


def test_console_formatter():
    """Tests Console formatter text generation."""
    formatter = ConsoleFormatter()
    record = logging.LogRecord(
        name="test_logger",
        level=logging.WARNING,
        pathname="test.py",
        lineno=10,
        msg="Warning alert message",
        args=(),
        exc_info=None,
    )
    formatted = formatter.format(record)
    assert "WARNING" in formatted
    assert "Warning alert message" in formatted


def test_setup_logging():
    """Tests root logger configuration."""
    app_logger = setup_logging(log_level="DEBUG", log_format="console")
    assert app_logger.level == logging.DEBUG
