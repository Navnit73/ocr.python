"""
Tests for File Validation and Temporary File Management.
"""

import io
import os
import pytest
from fastapi import HTTPException, UploadFile
from app.services.file_validator import FileValidator, managed_temp_file


@pytest.mark.asyncio
async def test_generate_or_sanitize_request_id():
    # Generated ID
    generated = FileValidator.generate_or_sanitize_request_id(None)
    assert generated.startswith("ocr_")

    # Custom frontend ID passed
    custom = FileValidator.generate_or_sanitize_request_id("client_req_12345")
    assert custom == "client_req_12345"

    # Sanitization of malicious characters
    sanitized = FileValidator.generate_or_sanitize_request_id("req/../123$<script>")
    assert sanitized == "req..123script"


@pytest.mark.asyncio
async def test_validate_empty_file():
    upload = UploadFile(filename="empty.pdf", file=io.BytesIO(b""))
    with pytest.raises(HTTPException) as exc_info:
        await FileValidator.validate_upload(upload)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_validate_unsupported_extension():
    upload = UploadFile(filename="document.exe", file=io.BytesIO(b"not an executable"))
    with pytest.raises(HTTPException) as exc_info:
        await FileValidator.validate_upload(upload)
    assert exc_info.value.status_code == 400
    assert "Unsupported file extension" in exc_info.value.detail


@pytest.mark.asyncio
async def test_validate_file_size_exceeded():
    # 101MB file exceeding 100MB limit
    large_content = b"%PDF-" + b"0" * (101 * 1024 * 1024)
    upload = UploadFile(filename="large.pdf", file=io.BytesIO(large_content))
    with pytest.raises(HTTPException) as exc_info:
        await FileValidator.validate_upload(upload)
    assert exc_info.value.status_code == 413


@pytest.mark.asyncio
async def test_validate_valid_pdf_signature():
    pdf_bytes = b"%PDF-1.4\n%test pdf content"
    upload = UploadFile(filename="sample.pdf", file=io.BytesIO(pdf_bytes))
    category, content = await FileValidator.validate_upload(upload)
    assert category == "pdf"
    assert content == pdf_bytes


@pytest.mark.asyncio
async def test_validate_valid_png_signature():
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    upload = UploadFile(filename="image.png", file=io.BytesIO(png_bytes))
    category, content = await FileValidator.validate_upload(upload)
    assert category == "image"
    assert content == png_bytes


def test_managed_temp_file_cleanup():
    temp_path = None
    with managed_temp_file(b"test temporary content", suffix=".tmp") as path:
        temp_path = path
        assert os.path.exists(temp_path)
        with open(temp_path, "rb") as f:
            assert f.read() == b"test temporary content"
    # Ensure deleted after context exit
    assert not os.path.exists(temp_path)
