"""
Application Configuration with Pydantic Settings.
"""

from functools import lru_cache
from typing import List, Optional, Union
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

    # Security & API Authentication
    api_keys: Union[str, List[str]] = Field(
        default=["ocr_dev_key_secret_2026", "ocr_test_key_master"],
        alias="API_KEYS",
    )
    require_api_key: bool = Field(default=True, alias="REQUIRE_API_KEY")
    enable_docs: bool = Field(default=True, alias="ENABLE_DOCS")

    # Rate Limiting Configuration
    rate_limit_enabled: bool = Field(default=True, alias="RATE_LIMIT_ENABLED")
    rate_limit_requests_per_minute: int = Field(default=60, alias="RATE_LIMIT_REQUESTS_PER_MINUTE")
    rate_limit_burst: int = Field(default=15, alias="RATE_LIMIT_BURST")

    # MongoDB Configuration
    mongodb_enabled: bool = Field(default=True, alias="MONGODB_ENABLED")
    mongodb_uri: str = Field(default="mongodb://localhost:27017", alias="MONGODB_URI")
    mongodb_db_name: str = Field(default="ocr_advance", alias="MONGODB_DB_NAME")

    # Redis & Celery Configuration
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    celery_broker_url: Optional[str] = Field(default=None, alias="CELERY_BROKER_URL")
    celery_result_backend: Optional[str] = Field(default=None, alias="CELERY_RESULT_BACKEND")
    use_celery: bool = Field(default=False, alias="USE_CELERY")

    # Worker & Concurrency Configuration
    worker_concurrency: int = Field(default=4, alias="WORKER_CONCURRENCY")
    max_job_retries: int = Field(default=3, alias="MAX_JOB_RETRIES")
    job_stale_timeout_seconds: int = Field(default=600, alias="JOB_STALE_TIMEOUT_SECONDS")

    # Storage Configuration
    storage_dir: str = Field(default="./storage/uploads", alias="STORAGE_DIR")
    storage_retention_days: int = Field(default=7, alias="STORAGE_RETENTION_DAYS")

    # Admin Dashboard Configuration
    admin_api_keys: Union[str, List[str]] = Field(
        default=["ocr_test_key_master", "ocr_admin_secret_2026"],
        alias="ADMIN_API_KEYS",
    )

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

    @field_validator("api_keys", "admin_api_keys", mode="before")
    @classmethod
    def parse_api_keys(cls, value: Union[str, List[str]]) -> List[str]:
        if isinstance(value, str):
            value = value.strip()
            if value.startswith("[") and value.endswith("]"):
                import json
                try:
                    return json.loads(value)
                except Exception:
                    pass
            return [k.strip() for k in value.split(",") if k.strip()]
        return value

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
