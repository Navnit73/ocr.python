"""
File Validation and Temporary File Management Service.
"""

import os
import re
import uuid
import tempfile
from contextlib import contextmanager
from typing import Generator, Optional, Tuple
from fastapi import HTTPException, UploadFile, status

from app.core.config import get_settings


# Magic byte signatures for supported file formats
FILE_SIGNATURES = {
    "pdf": [b"%PDF-"],
    "jpeg": [b"\xff\xd8\xff"],
    "png": [b"\x89PNG\r\n\x1a\n"],
    "webp": [b"RIFF"],  # also checks WEBP at byte offset 8
    "tiff": [b"II*\x00", b"MM\x00*"],
}


class FileValidator:
    """Validates uploaded files for size, extension, MIME type, and signatures."""

    @staticmethod
    def generate_or_sanitize_request_id(client_id: Optional[str] = None) -> str:
        """
        Returns client-provided ID if valid, or generates a clean unique request ID.
        """
        if client_id and client_id.strip():
            # Sanitize client ID to prevent header/path injection
            sanitized = re.sub(r"[^a-zA-Z0-9_\-\.:]", "", client_id.strip())
            if sanitized:
                return sanitized
        return f"ocr_{uuid.uuid4().hex[:12]}"

    @classmethod
    async def validate_upload(cls, file: UploadFile) -> Tuple[str, bytes]:
        """
        Validates the uploaded file against security rules.
        Returns the detected file format category ('pdf' or 'image') and first chunk/full bytes.
        """
        settings = get_settings()

        if not file.filename:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file must have a valid filename."
            )

        # Check extension
        _, ext = os.path.splitext(file.filename.lower())
        if ext not in settings.allowed_extensions:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported file extension '{ext}'. Allowed extensions: {', '.join(settings.allowed_extensions)}"
            )

        # Read content and validate size
        content = await file.read()
        max_bytes = settings.max_upload_size_mb * 1024 * 1024
        if len(content) > max_bytes:
            raise HTTPException(
                status_code=getattr(status, "HTTP_413_CONTENT_TOO_LARGE", 413),
                detail=f"File size ({len(content) / (1024*1024):.2f}MB) exceeds maximum limit of {settings.max_upload_size_mb}MB."
            )

        if len(content) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file is empty (0 bytes)."
            )

        # Reset cursor for downstream readers if needed
        await file.seek(0)

        # Validate file signature (magic bytes)
        doc_category = cls._verify_signature(content, ext)
        return doc_category, content

    @staticmethod
    def _verify_signature(content: bytes, ext: str) -> str:
        """
        Verifies magic bytes against known signatures.
        """
        header = content[:16]

        # PDF check
        if header.startswith(b"%PDF-"):
            return "pdf"

        # JPEG check
        if header.startswith(b"\xff\xd8\xff"):
            return "image"

        # PNG check
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image"

        # WEBP check
        if header.startswith(b"RIFF") and len(content) >= 12 and content[8:12] == b"WEBP":
            return "image"

        # TIFF check
        if header.startswith(b"II*\x00") or header.startswith(b"MM\x00*"):
            return "image"

        # If magic byte check failed but extension is supported, verify with Pillow for images
        if ext in [".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif"]:
            from PIL import Image
            import io
            try:
                with Image.open(io.BytesIO(content)) as img:
                    img.verify()
                return "image"
            except Exception:
                pass

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File content does not match its declared format (invalid magic signature)."
        )


@contextmanager
def managed_temp_file(content: bytes, suffix: str = ".tmp") -> Generator[str, None, None]:
    """
    Context manager that safely writes bytes to a temp file and strictly deletes it on exit.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp.write(content)
        tmp.flush()
        tmp.close()
        yield tmp.name
    finally:
        if os.path.exists(tmp.name):
            try:
                os.remove(tmp.name)
            except OSError:
                pass
