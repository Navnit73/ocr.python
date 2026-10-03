"""
Unit and Integration Tests for IBM Docling Document Extraction Engine.
"""

import io
import pytest
import pymupdf
from PIL import Image, ImageDraw

from app.schemas.ocr import ExtractionWarning
from app.services.docling_engine import DoclingEngine
from app.services.engine_interface import EngineResult


def create_digital_pdf_with_table() -> bytes:
    """Creates a sample PDF with title, headings, paragraph, and a table."""
    doc = pymupdf.open()
    page = doc.new_page()

    # Title and Headings
    page.insert_text((50, 40), "QUARTERLY FINANCIAL REPORT", fontsize=16)
    page.insert_text((50, 70), "Executive Summary", fontsize=13)
    page.insert_text((50, 95), "This report outlines financial metrics for Q1 2026.", fontsize=10)

    # Table Grid
    page.draw_rect(pymupdf.Rect(50, 130, 500, 220), width=1)
    page.draw_line(pymupdf.Point(50, 160), pymupdf.Point(500, 160), width=1)
    page.draw_line(pymupdf.Point(200, 130), pymupdf.Point(200, 220), width=1)
    page.draw_line(pymupdf.Point(350, 130), pymupdf.Point(350, 220), width=1)

    # Headers
    page.insert_text((60, 150), "Department", fontsize=10)
    page.insert_text((210, 150), "Budget (USD)", fontsize=10)
    page.insert_text((360, 150), "Spent (USD)", fontsize=10)

    # Row 1
    page.insert_text((60, 185), "Engineering", fontsize=10)
    page.insert_text((210, 185), "150000.00", fontsize=10)
    page.insert_text((360, 185), "120000.00", fontsize=10)

    # Row 2
    page.insert_text((60, 205), "Operations", fontsize=10)
    page.insert_text((210, 205), "75000.00", fontsize=10)
    page.insert_text((360, 205), "60000.00", fontsize=10)

    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def create_scanned_image_bytes() -> bytes:
    """Creates a synthetic image with invoice text."""
    img = Image.new("RGB", (600, 300), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.text((40, 30), "INVOICE #INV-9988", fill=(0, 0, 0))
    draw.text((40, 60), "Supplier: Cloud Solutions LLC", fill=(0, 0, 0))
    draw.text((40, 90), "Amount Due: $1,250.00", fill=(0, 0, 0))
    draw.text((40, 120), "Due Date: 2026-03-31", fill=(0, 0, 0))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_docling_engine_digital_pdf_extraction():
    """Verify Docling extracts text, headings, and tables from a digital PDF."""
    pdf_bytes = create_digital_pdf_with_table()
    engine = DoclingEngine.get_instance()

    result = await engine.extract_document(
        file_bytes=pdf_bytes,
        filename="financial_report.pdf",
        doc_category="pdf",
        extract_tables=True,
    )

    assert isinstance(result, EngineResult)
    assert result.engine_name == "docling"
    assert len(result.pages) >= 1
    assert "QUARTERLY FINANCIAL REPORT" in result.raw_text
    assert "Executive Summary" in result.raw_text
    assert result.markdown is not None
    assert len(result.markdown) > 0

    # Verify Table Extraction
    assert len(result.tables) >= 1
    table = result.tables[0]
    assert table.num_rows >= 2
    assert table.num_cols >= 2
    assert table.markdown is not None
    assert "Engineering" in str(table.grid) or "Engineering" in result.raw_text


@pytest.mark.asyncio
async def test_docling_engine_image_ocr_extraction():
    """Verify Docling handles image input via OCR."""
    img_bytes = create_scanned_image_bytes()
    engine = DoclingEngine.get_instance()

    result = await engine.extract_document(
        file_bytes=img_bytes,
        filename="scanned_invoice.png",
        doc_category="image",
        enable_ocr=True,
    )

    assert isinstance(result, EngineResult)
    assert result.engine_name == "docling"
    assert len(result.pages) >= 1
    assert len(result.raw_text) > 0
    assert "INVOICE" in result.raw_text or "9988" in result.raw_text or "Cloud" in result.raw_text


@pytest.mark.asyncio
async def test_docling_engine_multipage_pdf():
    """Verify Docling handles multi-page documents."""
    doc = pymupdf.open()
    for i in range(3):
        page = doc.new_page()
        page.insert_text((50, 50), f"Section Header {i + 1}", fontsize=14)
        page.insert_text((50, 80), f"Detailed page content for page number {i + 1}.", fontsize=10)
    pdf_bytes = doc.tobytes()
    doc.close()

    engine = DoclingEngine.get_instance()
    result = await engine.extract_document(
        file_bytes=pdf_bytes,
        filename="multipage.pdf",
        doc_category="pdf",
    )

    assert result.engine_name == "docling"
    assert len(result.pages) == 3
    assert result.metadata["total_pages"] == 3


@pytest.mark.asyncio
async def test_docling_engine_structured_exports():
    """Verify Docling provides Markdown and structured JSON export formats."""
    pdf_bytes = create_digital_pdf_with_table()
    engine = DoclingEngine.get_instance()

    result = await engine.extract_document(
        file_bytes=pdf_bytes,
        filename="export_test.pdf",
        doc_category="pdf",
    )

    assert result.markdown is not None
    assert result.structured_json is not None or result.raw_text is not None
    assert "QUARTERLY" in result.markdown or "QUARTERLY" in result.raw_text


@pytest.mark.asyncio
async def test_docling_engine_corrupted_file_error():
    """Verify Docling handles corrupted bytes by raising appropriate exception."""
    corrupted_bytes = b"NOT_A_VALID_PDF_HEADER_12345678"
    engine = DoclingEngine.get_instance()

    with pytest.raises(Exception):
        await engine.extract_document(
            file_bytes=corrupted_bytes,
            filename="bad.pdf",
            doc_category="pdf",
        )
