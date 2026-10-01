"""
Tests for Batch Processing, Zip Extraction, and Async Webhook Callbacks.
"""

import io
import zipfile
import pytest
from httpx import ASGITransport, AsyncClient
import pymupdf

from app.main import app
from app.services.webhook_service import WebhookService


def create_mock_pdf_bytes(text: str = "INVOICE #INV-999 Total: 500 USD") -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 50), text, fontsize=12)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


AUTH_HEADERS = {"X-API-Key": "ocr_dev_key_secret_2026"}


@pytest.mark.asyncio
async def test_batch_extract_multiple_files():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        pdf_1 = create_mock_pdf_bytes("RECEIPT Merchant: Starbucks Total: 15 USD")
        pdf_2 = create_mock_pdf_bytes("INVOICE Supplier: AWS Total: 200 USD")

        files = [
            ("files", ("receipt.pdf", pdf_1, "application/pdf")),
            ("files", ("invoice.pdf", pdf_2, "application/pdf")),
        ]
        data = {"document_type": "auto", "clean_with_ai": "false"}

        res = await client.post("/api/v1/ocr/batch", files=files, data=data, headers=AUTH_HEADERS)
        assert res.status_code == 200
        json_data = res.json()
        assert json_data["total_files"] == 2
        assert json_data["successful_count"] == 2
        assert len(json_data["items"]) == 2


@pytest.mark.asyncio
async def test_batch_extract_zip_archive():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create an in-memory zip archive with 2 PDFs
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w") as z:
            z.writestr("doc1.pdf", create_mock_pdf_bytes("Statement Page 1 Total: 1000 INR"))
            z.writestr("doc2.pdf", create_mock_pdf_bytes("Statement Page 2 Total: 2000 INR"))
        zip_buf.seek(0)

        files = [("files", ("archive.zip", zip_buf.getvalue(), "application/zip"))]
        data = {"document_type": "auto", "clean_with_ai": "false"}

        res = await client.post("/api/v1/ocr/batch", files=files, data=data, headers=AUTH_HEADERS)
        assert res.status_code == 200
        json_data = res.json()
        assert json_data["total_files"] == 2
        assert json_data["successful_count"] == 2


@pytest.mark.asyncio
async def test_async_extraction_with_callback_url():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        pdf_bytes = create_mock_pdf_bytes("Bank Statement Account 12345")
        files = {"file": ("statement.pdf", pdf_bytes, "application/pdf")}
        data = {
            "document_type": "bank_statement",
            "callback_url": "https://example.com/api/webhook",
            "callback_secret": "test_secret_key_123",
            "clean_with_ai": "false",
        }

        res = await client.post("/api/v1/ocr/extract", files=files, data=data, headers=AUTH_HEADERS)
        assert res.status_code == 202
        json_data = res.json()
        assert json_data["status"] == "processing"
        assert json_data["callback_url"] == "https://example.com/api/webhook"
        assert "id" in json_data
