"""
Application Configuration with Pydantic Settings.
"""

from functools import lru_cache
from typing import List, Union
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Core Application Settings."""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # App Info
    app_name: str = Field(default="AI OCR Advance API", alias="APP_NAME")
    environment: str = Field(default="development", alias="ENVIRONMENT")
    debug: bool = Field(default=True, alias="DEBUG")
    version: str = Field(default="1.0.0", alias="VERSION")
    api_v1_prefix: str = Field(default="/api/v1", alias="API_V1_PREFIX")

    # Server / Gunicorn / Uvicorn settings
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")
    workers: int = Field(default=2, alias="WORKERS")
    reload: bool = Field(default=True, alias="RELOAD")

    # CORS
    allowed_origins: Union[str, List[str]] = Field(
        default=["*"],
        alias="ALLOWED_ORIGINS",
    )

    # Document & OCR Configuration
    max_upload_size_mb: int = Field(default=100, alias="MAX_UPLOAD_SIZE_MB")
    max_pdf_pages: int = Field(default=200, alias="MAX_PDF_PAGES")
    max_batch_files: int = Field(default=50, alias="MAX_BATCH_FILES")
    ocr_language: str = Field(default="en", alias="OCR_LANGUAGE")
    ocr_timeout: int = Field(default=120, alias="OCR_TIMEOUT")
    ai_timeout: int = Field(default=60, alias="AI_TIMEOUT")
    webhook_timeout: int = Field(default=15, alias="WEBHOOK_TIMEOUT")

    # DeepSeek Configuration
    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")
    deepseek_model: str = Field(default="deepseek-chat", alias="DEEPSEEK_MODEL")
    deepseek_base_url: str = Field(default="https://api.deepseek.com/v1", alias="DEEPSEEK_BASE_URL")

    # Allowed Extensions & MIME Types
    allowed_extensions: List[str] = [
        ".pdf",
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".tiff",
        ".tif",
    ]

    allowed_mime_types: List[str] = [
        "application/pdf",
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/webp",
        "image/tiff",
    ]

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: Union[str, List[str]]) -> List[str]:
        if isinstance(value, str):
            value = value.strip()
            if value.startswith("[") and value.endswith("]"):
                import json
                try:
                    return json.loads(value)
                except Exception:
                    pass
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    """Returns cached settings instance."""
    return Settings()
