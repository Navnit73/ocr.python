"""
Storage Service Interface and Data Models.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class StorageUploadResult(BaseModel):
    """Normalized result returned after uploading a file to storage."""
    public_id: str = Field(description="Unique public identifier / key for the stored asset")
    url: str = Field(description="Public or access URL for the asset")
    secure_url: str = Field(description="HTTPS secure access URL for the asset")
    format: str = Field(description="File extension or format (e.g. pdf, png)")
    bytes_size: int = Field(description="Size of the file in bytes")
    resource_type: str = Field(default="image", description="Resource type (image, raw, auto, etc.)")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional provider metadata")


class StorageServiceInterface(ABC):
    """Abstract Interface for file storage backends."""

    @abstractmethod
    async def upload_file(
        self,
        file_bytes: bytes,
        filename: str,
        content_type: str,
        folder: Optional[str] = None,
    ) -> StorageUploadResult:
        """Uploads binary file data to the storage backend."""
        pass

    @abstractmethod
    async def upload_from_path(
        self,
        file_path: str,
        filename: Optional[str] = None,
        folder: Optional[str] = None,
    ) -> StorageUploadResult:
        """Uploads a local file from disk to the storage backend."""
        pass

    @abstractmethod
    async def delete_file(self, public_id: str, resource_type: str = "image") -> bool:
        """Deletes a file from the storage backend."""
        pass

    @abstractmethod
    async def get_file_url(
        self,
        public_id: str,
        signed: bool = False,
        resource_type: str = "image",
    ) -> str:
        """Generates a secure access URL for a stored asset."""
        pass

    @abstractmethod
    async def download_file(self, public_id_or_url: str) -> bytes:
        """Downloads the file content as bytes."""
        pass
