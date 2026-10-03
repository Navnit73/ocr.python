"""
Secure Storage Service for Uploaded Documents and Extraction Artifacts.
Provides path traversal protection, SHA-256 deduplication, and retention management.
"""

import hashlib
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple
import aiofiles

from app.core.config import get_settings

logger = logging.getLogger("storage")


class StorageService:
    """Manages file persistence for background OCR jobs."""

    @classmethod
    def _get_storage_root(cls) -> Path:
        settings = get_settings()
        root = Path(settings.storage_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    @classmethod
    def _sanitize_filename(cls, filename: str) -> str:
        """Sanitizes filename removing directory traversal characters."""
        base = os.path.basename(filename)
        # Keep only safe alphanumeric, dash, dot, underscore
        safe = "".join(c for c in base if c.isalnum() or c in ("-", "_", "."))
        return safe or "document.bin"

    @classmethod
    async def save_file(
        cls,
        file_bytes: bytes,
        filename: str,
        document_id: str,
    ) -> Tuple[str, str, int]:
        """
        Saves file bytes to disk in a dedicated document subfolder.
        Returns: (file_reference, sha256_checksum, file_size_bytes)
        """
        root = cls._get_storage_root()
        safe_name = cls._sanitize_filename(filename)
        
        # Subdirectory per document ID to isolate files
        doc_dir = (root / document_id).resolve()
        # Security: verify doc_dir is within root
        if not str(doc_dir).startswith(str(root)):
            raise ValueError("Path traversal attempt detected in document_id.")
        
        doc_dir.mkdir(parents=True, exist_ok=True)
        target_path = doc_dir / safe_name
        
        # Write bytes asynchronously
        async with aiofiles.open(target_path, "wb") as f:
            await f.write(file_bytes)
            
        file_size = len(file_bytes)
        checksum = hashlib.sha256(file_bytes).hexdigest()
        file_reference = str(target_path)
        
        logger.info(f"Saved file {safe_name} ({file_size} bytes) for doc {document_id} at {file_reference}")
        return file_reference, checksum, file_size

    @classmethod
    async def read_file(cls, file_reference: str) -> bytes:
        """Reads file bytes from storage reference."""
        path = Path(file_reference).resolve()
        root = cls._get_storage_root()
        
        if not str(path).startswith(str(root)):
            raise ValueError("Security violation: Attempted to access file outside storage root.")
            
        if not path.exists():
            raise FileNotFoundError(f"Stored file not found at {file_reference}")
            
        async with aiofiles.open(path, "rb") as f:
            return await f.read()

    @classmethod
    async def delete_file(cls, file_reference: str) -> bool:
        """Deletes file and parent document directory if empty."""
        try:
            path = Path(file_reference).resolve()
            root = cls._get_storage_root()
            
            if not str(path).startswith(str(root)):
                return False
                
            if path.exists():
                path.unlink()
                # If directory is now empty, remove it
                parent = path.parent
                if parent != root and parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
                return True
        except Exception as e:
            logger.warning(f"Error deleting file {file_reference}: {e}")
    @classmethod
    async def delete_document_directory(cls, document_id: str) -> bool:
        """Removes the entire storage folder and files for a given document_id."""
        try:
            root = cls._get_storage_root()
            doc_dir = (root / document_id).resolve()
            if not str(doc_dir).startswith(str(root)):
                return False
            if doc_dir.exists() and doc_dir.is_dir():
                import shutil
                shutil.rmtree(doc_dir, ignore_errors=True)
                logger.info(f"Purged storage directory for document {document_id}")
                return True
        except Exception as e:
            logger.warning(f"Error removing storage directory for {document_id}: {e}")
        return False

    @classmethod
    def cleanup_old_files(cls, max_age_days: Optional[int] = None) -> int:
        """Removes files older than the retention period."""
        settings = get_settings()
        days = max_age_days if max_age_days is not None else settings.storage_retention_days
        cutoff = time.time() - (days * 86400)
        deleted_count = 0
        root = cls._get_storage_root()
        
        try:
            for item in root.glob("**/*"):
                if item.is_file():
                    if item.stat().st_mtime < cutoff:
                        item.unlink()
                        deleted_count += 1
            # Clean empty directories
            for item in root.glob("*"):
                if item.is_dir() and not any(item.iterdir()):
                    item.rmdir()
        except Exception as e:
            logger.warning(f"Error during storage retention cleanup: {e}")
            
        return deleted_count
