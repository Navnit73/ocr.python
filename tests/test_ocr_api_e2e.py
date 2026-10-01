"""
End-to-End Tests for OCR & Document Extraction API.
"""

import io
import pytest
from httpx import ASGITransport, AsyncClient
import pymupdf
from PIL import Image

from app.main import app


def create_digital_pdf_bytes(text: str) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 50), text)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def create_png_image_bytes() -> bytes:
    img = Image.new("RGB", (200, 80), color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_extract_digital_pdf_e2e():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        pdf_content = create_digital_pdf_bytes(
            "Acme Bank Statement\nAccount Number: 99887766\nOpening Balance: 500.00\nClosing Balance: 500.00"
        )
        files = {
            "file": ("statement.pdf", pdf_content, "application/pdf")
        }
        data = {
            "document_type": "bank_statement",
            "clean_with_ai": "false",
            "request_id": "client_unique_id_999",
        }

        response = await client.post("/api/v1/ocr/extract", files=files, data=data)
        assert response.status_code == 200
        res_json = response.json()

        # Check frontend unique ID preservation
        assert res_json["id"] == "client_unique_id_999"
        assert res_json["status"] == "success"
        assert res_json["document_type"] == "bank_statement"
        assert "Acme Bank Statement" in res_json["raw_text"]
        assert "Acme Bank Statement" in res_json["cleaned_text"]
        assert res_json["metadata"]["pages"] == 1
        assert "processing_time_ms" in res_json["metadata"]
        assert isinstance(res_json["warnings"], list)


@pytest.mark.asyncio
async def test_extract_image_e2e():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        png_content = create_png_image_bytes()
        files = {
            "file": ("receipt.png", png_content, "image/png")
        }
        data = {
            "document_type": "auto",
            "clean_with_ai": "false",
        }

        response = await client.post("/api/v1/ocr/extract", files=files, data=data)
        assert response.status_code == 200
        res_json = response.json()

        # ID should be generated with ocr_ prefix when not supplied
        assert res_json["id"].startswith("ocr_")
        assert res_json["status"] in ["success", "partial_success"]
        assert res_json["metadata"]["ocr_used"] is True


@pytest.mark.asyncio
async def test_corrupted_upload_error():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {
            "file": ("bad.pdf", b"corrupted header", "application/pdf")
        }
        response = await client.post("/api/v1/ocr/extract", files=files)
        assert response.status_code == 400
        res_json = response.json()
        assert res_json["status"] == "error"


@pytest.mark.asyncio
async def test_extract_password_protected_pdf_e2e():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create encrypted PDF
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((50, 50), "Confidential Bank Statement for VIP Client")
        enc_bytes = doc.tobytes(
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            user_pw="bankpass123",
            owner_pw="ownerpass"
        )
        doc.close()

        files = {
            "file": ("encrypted_statement.pdf", enc_bytes, "application/pdf")
        }
        data = {
            "password": "bankpass123",
            "clean_with_ai": "false",
            "request_id": "req_enc_pdf_1",
        }

        response = await client.post("/api/v1/ocr/extract", files=files, data=data)
        assert response.status_code == 200
        res_json = response.json()
        assert res_json["id"] == "req_enc_pdf_1"
        assert "Confidential Bank Statement" in res_json["raw_text"]


@pytest.mark.asyncio
async def test_health_check_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"
