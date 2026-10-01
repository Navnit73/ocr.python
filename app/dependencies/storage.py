"""
FastAPI Storage Service Dependency.
"""

from functools import lru_cache
from fastapi import Depends
from app.core.config import Settings, get_settings
from app.core.constants import StorageBackendType
from app.storage.base import StorageServiceInterface
from app.storage.cloudinary_storage import CloudinaryStorageService
from app.storage.local_storage import LocalStorageService


@lru_cache
def _get_storage_instance(backend: StorageBackendType, settings: Settings) -> StorageServiceInterface:
    """Internal factory to instantiate and cache storage service singleton."""
    if backend == StorageBackendType.CLOUDINARY and settings.cloudinary.cloud_name:
        return CloudinaryStorageService(settings=settings)
    return LocalStorageService(settings=settings)


def get_storage_service(
    settings: Settings = Depends(get_settings),
) -> StorageServiceInterface:
    """Dependency provider returning the configured StorageServiceInterface implementation."""
    return _get_storage_instance(settings.storage_backend, settings)
