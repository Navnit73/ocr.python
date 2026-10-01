"""
Unit tests for Configuration and Environment Parsing.
"""

from app.core.config import Settings
from app.core.constants import Environment, StorageBackendType, OCREngineType


def test_default_settings():
    """Validates default settings fallback and parsing."""
    settings = Settings()
    assert settings.app_name == "AI Document OCR Platform"
    assert settings.app_env in [Environment.DEVELOPMENT, Environment.TEST]
    assert settings.api_v1_str == "/api/v1"
    assert settings.server_port == 8000
    assert isinstance(settings.allowed_origins, list)
    assert len(settings.allowed_origins) > 0


def test_cors_origins_parsing():
    """Validates comma-separated string parsing for CORS origins."""
    settings = Settings(ALLOWED_ORIGINS="http://example.com, https://app.example.com")
    assert "http://example.com" in settings.allowed_origins
    assert "https://app.example.com" in settings.allowed_origins


def test_allowed_extensions_parsing():
    """Validates comma-separated extensions parsing."""
    settings = Settings(ALLOWED_EXTENSIONS=".pdf, .png, jpg")
    assert "pdf" in settings.allowed_extensions
    assert "png" in settings.allowed_extensions
    assert "jpg" in settings.allowed_extensions


def test_environment_helper_properties():
    """Validates helper properties on Settings."""
    dev_settings = Settings(APP_ENV="development")
    assert dev_settings.is_development is True
    assert dev_settings.is_production is False

    prod_settings = Settings(APP_ENV="production")
    assert prod_settings.is_production is True
    assert prod_settings.is_development is False
