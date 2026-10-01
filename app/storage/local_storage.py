"""
Local Filesystem Storage Service Implementation (for local dev and testing).
"""

import os
import uuid
from typing import Any, Dict, Optional

from app.core.config import Settings, get_settings
from app.core.exceptions import StorageException
from app.storage.base import StorageServiceInterface, StorageUploadResult


class LocalStorageService(StorageServiceInterface):
    """Local filesystem implementation of StorageServiceInterface."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.base_dir = os.path.abspath(self.settings.local_storage_dir)
        os.makedirs(self.base_dir, exist_ok=True)

    def _get_target_dir(self, folder: Optional[str] = None) -> str:
        target = os.path.join(self.base_dir, folder) if folder else self.base_dir
        os.makedirs(target, exist_ok=True)
        return target

    async def upload_file(
        self,
        file_bytes: bytes,
        filename: str,
        content_type: str,
        folder: Optional[str] = None,
    ) -> StorageUploadResult:
        """Saves file bytes to local disk."""
        target_dir = self._get_target_dir(folder)
        ext = filename.split(".")[-1].lower() if "." in filename else "bin"
        unique_id = f"{uuid.uuid4().hex}_{filename}"
        file_path = os.path.join(target_dir, unique_id)

        try:
            with open(file_path, "wb") as f:
                f.write(file_bytes)

            file_url = f"/storage/{folder or 'root'}/{unique_id}"
            return StorageUploadResult(
                public_id=f"{folder}/{unique_id}" if folder else unique_id,
                url=file_url,
                secure_url=file_url,
                format=ext,
                bytes_size=len(file_bytes),
                resource_type="raw" if ext == "pdf" else "image",
                metadata={"local_path": file_path},
            )
        except Exception as exc:
            raise StorageException(f"Failed to write file locally: {str(exc)}")

    async def upload_from_path(
        self,
        file_path: str,
        filename: Optional[str] = None,
        folder: Optional[str] = None,
    ) -> StorageUploadResult:
        """Copies an existing file into the storage directory."""
        if not os.path.exists(file_path):
            raise StorageException(f"Source file not found at: {file_path}")

        name = filename or os.path.basename(file_path)
        with open(file_path, "rb") as f:
            data = f.read()

        ext = name.split(".")[-1].lower() if "." in name else "bin"
        content_type = "application/pdf" if ext == "pdf" else f"image/{ext}"
        return await self.upload_file(data, name, content_type, folder)

    async def delete_file(self, public_id: str, resource_type: str = "image") -> bool:
        """Deletes a file from the local storage folder."""
        file_path = os.path.join(self.base_dir, public_id)
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                return True
            except OSError:
                return False
        return False

    async def get_file_url(
        self,
        public_id: str,
        signed: bool = False,
        resource_type: str = "image",
    ) -> str:
        """Returns relative URL for local asset."""
        return f"/storage/{public_id}"

    async def download_file(self, public_id_or_url: str) -> bytes:
        """Reads file bytes from disk."""
        clean_id = public_id_or_url.replace("/storage/", "")
        file_path = os.path.join(self.base_dir, clean_id)
        if not os.path.exists(file_path):
            raise StorageException(f"File not found on local storage: {clean_id}")
        with open(file_path, "rb") as f:
            return f.read()
