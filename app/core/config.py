"""
Application Configuration using Pydantic Settings.
"""

from functools import lru_cache
from typing import Any, List, Union
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.constants import Environment, StorageBackendType, OCREngineType, LLMProviderType


class MongoSettings(BaseSettings):
    """MongoDB Database Configuration."""
    uri: str = Field(default="mongodb://localhost:27017", alias="MONGODB_URI")
    db_name: str = Field(default="ocr_advance_db", alias="MONGODB_DB_NAME")
    min_pool_size: int = Field(default=10, alias="MONGODB_MIN_POOL_SIZE")
    max_pool_size: int = Field(default=50, alias="MONGODB_MAX_POOL_SIZE")
    timeout_ms: int = Field(default=5000, alias="MONGODB_TIMEOUT_MS")


class RedisSettings(BaseSettings):
    """Redis & Celery Queue Configuration."""
    url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    celery_broker_url: str = Field(default="redis://localhost:6379/1", alias="CELERY_BROKER_URL")
    celery_result_backend: str = Field(default="redis://localhost:6379/2", alias="CELERY_RESULT_BACKEND")
    celery_task_default_queue: str = Field(default="ocr_tasks", alias="CELERY_TASK_DEFAULT_QUEUE")
    celery_task_time_limit: int = Field(default=600, alias="CELERY_TASK_TIME_LIMIT")


class CloudinarySettings(BaseSettings):
    """Cloudinary Cloud Storage Configuration."""
    cloud_name: str = Field(default="", alias="CLOUDINARY_CLOUD_NAME")
    api_key: str = Field(default="", alias="CLOUDINARY_API_KEY")
    api_secret: str = Field(default="", alias="CLOUDINARY_API_SECRET")
    secure: bool = Field(default=True, alias="CLOUDINARY_SECURE")
    folder: str = Field(default="ocr_platform", alias="CLOUDINARY_FOLDER")


class SecuritySettings(BaseSettings):
    """Security and JWT Settings."""
    jwt_secret_key: str = Field(
        default="default-insecure-secret-key-change-in-production-min32chars",
        alias="JWT_SECRET_KEY"
    )
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    access_token_expire_minutes: int = Field(default=60, alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    refresh_token_expire_days: int = Field(default=7, alias="REFRESH_TOKEN_EXPIRE_DAYS")


class OCRSettings(BaseSettings):
    """OCR and Layout Intelligence Configuration."""
    default_engine: OCREngineType = Field(default=OCREngineType.PADDLEOCR, alias="DEFAULT_OCR_ENGINE")
    paddleocr_lang: str = Field(default="en", alias="PADDLEOCR_LANG")
    tesseract_cmd_path: str = Field(default="/usr/bin/tesseract", alias="TESSERACT_CMD_PATH")
    docling_enabled: bool = Field(default=True, alias="DOCLING_ENABLED")


class LLMSettings(BaseSettings):
    """LLM / AI Document Correction Configuration."""
    provider: LLMProviderType = Field(default=LLMProviderType.OPENAI, alias="LLM_PROVIDER")
    api_key: str = Field(default="", alias="LLM_API_KEY")
    model_name: str = Field(default="gpt-4o-mini", alias="LLM_MODEL_NAME")
    temperature: float = Field(default=0.1, alias="LLM_TEMPERATURE")
    max_tokens: int = Field(default=4096, alias="LLM_MAX_TOKENS")


class Settings(BaseSettings):
    """Main Application Settings combining all modules."""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # App Info
    app_name: str = Field(default="AI Document OCR Platform", alias="APP_NAME")
    app_env: Environment = Field(default=Environment.DEVELOPMENT, alias="APP_ENV")
    debug: bool = Field(default=True, alias="DEBUG")
    api_v1_str: str = Field(default="/api/v1", alias="API_V1_STR")
    project_version: str = Field(default="0.1.0", alias="PROJECT_VERSION")

    # Server
    server_host: str = Field(default="0.0.0.0", alias="SERVER_HOST")
    server_port: int = Field(default=8000, alias="SERVER_PORT")
    server_workers: int = Field(default=1, alias="SERVER_WORKERS")
    server_reload: bool = Field(default=True, alias="SERVER_RELOAD")

    # CORS
    allowed_origins: Union[str, List[str]] = Field(
        default=["http://localhost:3000", "http://localhost:8000"],
        alias="ALLOWED_ORIGINS"
    )

    # Storage Backend
    storage_backend: StorageBackendType = Field(
        default=StorageBackendType.LOCAL,
        alias="STORAGE_BACKEND"
    )
    local_storage_dir: str = Field(default="./storage_data", alias="LOCAL_STORAGE_DIR")
    temp_file_dir: str = Field(default="./temp_uploads", alias="TEMP_FILE_DIR")
    max_file_size_bytes: int = Field(default=26214400, alias="MAX_FILE_SIZE_BYTES")
    allowed_extensions: Union[str, List[str]] = Field(
        default="pdf,png,jpg,jpeg,tiff,bmp,webp",
        alias="ALLOWED_EXTENSIONS"
    )

    # Logging & Monitoring
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_format: str = Field(default="console", alias="LOG_FORMAT")
    sentry_dsn: str = Field(default="", alias="SENTRY_DSN")

    # Nested Sub-Settings
    mongo: MongoSettings = Field(default_factory=MongoSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    cloudinary: CloudinarySettings = Field(default_factory=CloudinarySettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    ocr: OCRSettings = Field(default_factory=OCRSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)

    def __init__(self, **values: Any) -> None:
        # Allow flat kwargs to populate sub-settings
        if any(k.upper().startswith("CLOUDINARY_") for k in values):
            cloud_kwargs = {
                k.lower().replace("cloudinary_", ""): v
                for k, v in list(values.items())
                if k.upper().startswith("CLOUDINARY_")
            }
            if "cloudinary" not in values:
                values["cloudinary"] = CloudinarySettings(**cloud_kwargs)

        if any(k.upper().startswith("MONGODB_") for k in values):
            mongo_kwargs = {
                k.lower().replace("mongodb_", ""): v
                for k, v in list(values.items())
                if k.upper().startswith("MONGODB_")
            }
            if "mongo" not in values:
                values["mongo"] = MongoSettings(**mongo_kwargs)

        if any(k.upper().startswith("REDIS_") for k in values):
            redis_kwargs = {
                k.lower().replace("redis_", ""): v
                for k, v in list(values.items())
                if k.upper().startswith("REDIS_")
            }
            if "redis" not in values:
                values["redis"] = RedisSettings(**redis_kwargs)

        super().__init__(**values)

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: Union[str, List[str]]) -> List[str]:
        if isinstance(value, str):
            # Handle comma separated or json array
            value = value.strip()
            if value.startswith("[") and value.endswith("]"):
                import json
                try:
                    return json.loads(value)
                except Exception:
                    pass
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("allowed_extensions", mode="before")
    @classmethod
    def parse_extensions(cls, value: Union[str, List[str]]) -> List[str]:
        if isinstance(value, str):
            return [ext.strip().lower().lstrip(".") for ext in value.split(",") if ext.strip()]
        return [ext.lower().lstrip(".") for ext in value]

    @property
    def is_production(self) -> bool:
        return self.app_env == Environment.PRODUCTION

    @property
    def is_development(self) -> bool:
        return self.app_env == Environment.DEVELOPMENT

    @property
    def is_testing(self) -> bool:
        return self.app_env == Environment.TEST


@lru_cache
def get_settings() -> Settings:
    """Returns cached settings instance."""
    return Settings()
