"""
Application Constants and Enumerations.
"""

from enum import Enum


class Environment(str, Enum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    TEST = "test"


class UserRole(str, Enum):
    USER = "user"
    ADMIN = "admin"
    SUPERADMIN = "superadmin"


class DocumentStatus(str, Enum):
    PENDING = "pending"
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class JobStatus(str, Enum):
    QUEUED = "queued"
    STARTED = "started"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILURE = "failure"
    RETRY = "retry"
    REVOKED = "revoked"


class OCREngineType(str, Enum):
    PADDLEOCR = "paddleocr"
    TESSERACT = "tesseract"
    HYBRID = "hybrid"


class StorageBackendType(str, Enum):
    CLOUDINARY = "cloudinary"
    LOCAL = "local"


class LLMProviderType(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    CUSTOM = "custom"


# MIME types & Extensions
ALLOWED_MIME_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/tiff",
    "image/bmp",
    "image/webp",
}

ALLOWED_EXTENSIONS = {
    "pdf",
    "png",
    "jpg",
    "jpeg",
    "tiff",
    "bmp",
    "webp",
}

MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB default
