"""
Storage module package initialization.
"""

from app.storage.base import StorageServiceInterface, StorageUploadResult
from app.storage.cloudinary_storage import CloudinaryStorageService
from app.storage.local_storage import LocalStorageService

__all__ = [
    "StorageServiceInterface",
    "StorageUploadResult",
    "CloudinaryStorageService",
    "LocalStorageService",
]
