"""
Unit tests for Storage Services.
"""

import os
import pytest
from app.core.config import Settings
from app.storage.local_storage import LocalStorageService
from app.storage.cloudinary_storage import CloudinaryStorageService


@pytest.mark.asyncio
async def test_local_storage_upload_download_delete(test_settings: Settings):
    """Tests file lifecycle using LocalStorageService."""
    storage = LocalStorageService(settings=test_settings)
    sample_data = b"Sample OCR document binary content for testing"
    filename = "invoice_test.pdf"

    # 1. Upload
    result = await storage.upload_file(
        file_bytes=sample_data,
        filename=filename,
        content_type="application/pdf",
        folder="test_docs",
    )
    assert result.public_id is not None
    assert result.bytes_size == len(sample_data)
    assert result.format == "pdf"

    # 2. Get URL
    url = await storage.get_file_url(result.public_id)
    assert "/storage/" in url

    # 3. Download
    downloaded = await storage.download_file(result.public_id)
    assert downloaded == sample_data

    # 4. Delete
    deleted = await storage.delete_file(result.public_id)
    assert deleted is True

    # 5. Verify deleted
    with pytest.raises(Exception):
        await storage.download_file(result.public_id)


def test_cloudinary_storage_initialization():
    """Tests CloudinaryStorageService initialization with settings."""
    settings = Settings(
        CLOUDINARY_CLOUD_NAME="demo_cloud",
        CLOUDINARY_API_KEY="1234567890",
        CLOUDINARY_API_SECRET="secret_token",
    )
    storage = CloudinaryStorageService(settings=settings)
    assert storage.settings.cloudinary.cloud_name == "demo_cloud"
