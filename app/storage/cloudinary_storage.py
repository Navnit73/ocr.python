"""
Cloudinary Storage Service Implementation.
"""

import asyncio
import os
from typing import Any, Dict, Optional
import cloudinary
import cloudinary.uploader
import cloudinary.utils
import httpx

from app.core.config import Settings, get_settings
from app.core.exceptions import StorageException
from app.core.logging import logger
from app.storage.base import StorageServiceInterface, StorageUploadResult


class CloudinaryStorageService(StorageServiceInterface):
    """Cloudinary cloud asset storage backend implementation."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self._initialize_client()

    def _initialize_client(self) -> None:
        """Configures the Cloudinary global SDK."""
        if not self.settings.cloudinary.cloud_name or not self.settings.cloudinary.api_key:
            logger.warning("Cloudinary credentials not fully configured.")
            return

        cloudinary.config(
            cloud_name=self.settings.cloudinary.cloud_name,
            api_key=self.settings.cloudinary.api_key,
            api_secret=self.settings.cloudinary.api_secret,
            secure=self.settings.cloudinary.secure,
        )
        logger.info(f"Cloudinary storage initialized for cloud: '{self.settings.cloudinary.cloud_name}'")

    def _resolve_resource_type(self, content_type: str, filename: str) -> str:
        """Determines Cloudinary resource type (image vs raw vs auto)."""
        ext = filename.split(".")[-1].lower() if "." in filename else ""
        if content_type == "application/pdf" or ext == "pdf":
            # In Cloudinary, PDFs can be uploaded as 'image' (for multi-page preview & conversion) or 'raw'
            return "image"
        if content_type.startswith("image/"):
            return "image"
        return "raw"

    async def upload_file(
        self,
        file_bytes: bytes,
        filename: str,
        content_type: str,
        folder: Optional[str] = None,
    ) -> StorageUploadResult:
        """Uploads file bytes to Cloudinary in a separate thread."""
        target_folder = folder or self.settings.cloudinary.folder
        resource_type = self._resolve_resource_type(content_type, filename)

        def _sync_upload() -> Dict[str, Any]:
            return cloudinary.uploader.upload(
                file_bytes,
                folder=target_folder,
                resource_type=resource_type,
                use_filename=True,
                unique_filename=True,
            )

        try:
            result = await asyncio.to_thread(_sync_upload)
            return StorageUploadResult(
                public_id=result["public_id"],
                url=result.get("url", ""),
                secure_url=result.get("secure_url", ""),
                format=result.get("format", filename.split(".")[-1]),
                bytes_size=result.get("bytes", len(file_bytes)),
                resource_type=result.get("resource_type", resource_type),
                metadata={
                    "width": result.get("width"),
                    "height": result.get("height"),
                    "pages": result.get("pages"),
                    "created_at": result.get("created_at"),
                },
            )
        except Exception as exc:
            logger.error(f"Cloudinary upload failed for {filename}: {exc}")
            raise StorageException(f"Failed to upload {filename} to Cloudinary: {str(exc)}")

    async def upload_from_path(
        self,
        file_path: str,
        filename: Optional[str] = None,
        folder: Optional[str] = None,
    ) -> StorageUploadResult:
        """Uploads a file directly from a local path."""
        if not os.path.exists(file_path):
            raise StorageException(f"Source file not found at path: {file_path}")

        name = filename or os.path.basename(file_path)
        ext = name.split(".")[-1].lower()
        content_type = "application/pdf" if ext == "pdf" else f"image/{ext}"

        target_folder = folder or self.settings.cloudinary.folder
        resource_type = self._resolve_resource_type(content_type, name)

        def _sync_upload() -> Dict[str, Any]:
            return cloudinary.uploader.upload(
                file_path,
                folder=target_folder,
                resource_type=resource_type,
                use_filename=True,
                unique_filename=True,
            )

        try:
            result = await asyncio.to_thread(_sync_upload)
            return StorageUploadResult(
                public_id=result["public_id"],
                url=result.get("url", ""),
                secure_url=result.get("secure_url", ""),
                format=result.get("format", ext),
                bytes_size=result.get("bytes", os.path.getsize(file_path)),
                resource_type=result.get("resource_type", resource_type),
                metadata=result,
            )
        except Exception as exc:
            logger.error(f"Cloudinary upload_from_path failed for {file_path}: {exc}")
            raise StorageException(f"Failed to upload file to Cloudinary: {str(exc)}")

    async def delete_file(self, public_id: str, resource_type: str = "image") -> bool:
        """Deletes an asset from Cloudinary."""
        def _sync_delete() -> Dict[str, Any]:
            return cloudinary.uploader.destroy(public_id, resource_type=resource_type)

        try:
            result = await asyncio.to_thread(_sync_delete)
            return result.get("result") == "ok"
        except Exception as exc:
            logger.error(f"Cloudinary delete failed for public_id {public_id}: {exc}")
            return False

    async def get_file_url(
        self,
        public_id: str,
        signed: bool = False,
        resource_type: str = "image",
    ) -> str:
        """Generates a secure or signed URL for an asset."""
        url, _ = cloudinary.utils.cloudinary_url(
            public_id,
            resource_type=resource_type,
            secure=True,
            sign_url=signed,
        )
        return url

    async def download_file(self, public_id_or_url: str) -> bytes:
        """Downloads the file from Cloudinary via HTTP."""
        url = public_id_or_url
        if not url.startswith("http://") and not url.startswith("https://"):
            url = await self.get_file_url(public_id_or_url)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            if response.status_code != 200:
                raise StorageException(
                    f"Failed to download asset from {url}, status code: {response.status_code}"
                )
            return response.content
