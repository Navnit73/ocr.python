"""
End-to-End API Integration Tests for Docling Engine Endpoints.
"""

import io
import pytest
from httpx import ASGITransport, AsyncClient
import pymupdf
from PIL import Image, ImageDraw

from app.db.mongodb import MongoDBManager
from app.main import app

AUTH_HEADERS = {"X-API-Key": "ocr_dev_key_secret_2026"}


def create_table_pdf_bytes() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 40), "COMMERCIAL INVOICE #1010", fontsize=14)
    page.insert_text((50, 70), "Supplier: Global Logistics Corp", fontsize=10)
    page.draw_rect(pymupdf.Rect(50, 100, 450, 180), width=1)
    page.draw_line(pymupdf.Point(50, 125), pymupdf.Point(450, 125), width=1)
    page.insert_text((55, 118), "Item", fontsize=9)
    page.insert_text((300, 118), "Price", fontsize=9)
    page.insert_text((55, 145), "Shipping Freight", fontsize=9)
    page.insert_text((300, 145), "$450.00", fontsize=9)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


@pytest.mark.asyncio
async def test_extract_endpoint_with_docling_engine():
    """Verify /extract synchronous endpoint with extraction_engine=docling."""
    await MongoDBManager.connect(force_mock=True)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            pdf_bytes = create_table_pdf_bytes()
            files = {"file": ("invoice.pdf", pdf_bytes, "application/pdf")}
            data = {
                "document_type": "invoice",
                "extraction_engine": "docling",
                "extract_tables": "true",
                "enable_ocr": "true",
                "output_format": "json",
                "clean_with_ai": "false",
                "request_id": "docling_req_101",
            }

            resp = await client.post("/api/v1/ocr/extract", files=files, data=data, headers=AUTH_HEADERS)
            assert resp.status_code == 200
            res_json = resp.json()

            assert res_json["id"] == "docling_req_101"
            assert res_json["status"] in ("success", "partial_success")
            assert res_json["metadata"]["extraction_engine"] == "docling"
            assert "COMMERCIAL INVOICE" in res_json["raw_text"]
            assert res_json["metadata"]["tables_extracted"] >= 0
    finally:
        await MongoDBManager.disconnect()


@pytest.mark.asyncio
async def test_extract_async_endpoint_with_docling():
    """Verify /extract-async queues job with docling extraction_engine."""
    await MongoDBManager.connect(force_mock=True)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            pdf_bytes = create_table_pdf_bytes()
            files = {"file": ("async_invoice.pdf", pdf_bytes, "application/pdf")}
            data = {
                "document_type": "invoice",
                "extraction_engine": "docling",
                "clean_with_ai": "false",
            }

            resp = await client.post("/api/v1/ocr/extract-async", files=files, data=data, headers=AUTH_HEADERS)
            assert resp.status_code == 202
            res_json = resp.json()

            assert "job_id" in res_json
            assert "document_id" in res_json
            assert res_json["status"] == "queued"
    finally:
        await MongoDBManager.disconnect()


@pytest.mark.asyncio
async def test_batch_extract_with_docling():
    """Verify /batch endpoint processes files with docling engine parameter."""
    await MongoDBManager.connect(force_mock=True)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            pdf_bytes1 = create_table_pdf_bytes()
            pdf_bytes2 = create_table_pdf_bytes()
            files = [
                ("files", ("inv1.pdf", pdf_bytes1, "application/pdf")),
                ("files", ("inv2.pdf", pdf_bytes2, "application/pdf")),
            ]
            data = {
                "document_type": "invoice",
                "extraction_engine": "docling",
                "clean_with_ai": "false",
            }

            resp = await client.post("/api/v1/ocr/batch", files=files, data=data, headers=AUTH_HEADERS)
            assert resp.status_code == 200
            res_json = resp.json()

            assert res_json["total_files"] == 2
            assert res_json["successful_count"] == 2
    finally:
        await MongoDBManager.disconnect()


@pytest.mark.asyncio
async def test_api_backward_compatibility():
    """Verify standard calls without new parameters continue to work identically."""
    await MongoDBManager.connect(force_mock=True)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            pdf_bytes = create_table_pdf_bytes()
            files = {"file": ("legacy_call.pdf", pdf_bytes, "application/pdf")}
            data = {
                "document_type": "general",
                "clean_with_ai": "false",
            }

            # No extraction_engine, enable_ocr, extract_tables, or output_format passed
            resp = await client.post("/api/v1/ocr/extract", files=files, data=data, headers=AUTH_HEADERS)
            assert resp.status_code == 200
            res_json = resp.json()

            assert res_json["status"] in ("success", "partial_success")
            assert "raw_text" in res_json
            assert "metadata" in res_json
    finally:
        await MongoDBManager.disconnect()
