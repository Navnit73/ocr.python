"""
Tests for High Capacity PDF Processing (Up to 200 Pages).
"""

import pymupdf
import pytest
from app.services.pdf_service import PDFService


def test_200_page_digital_pdf_processing():
    # Build a 200-page digital PDF in memory
    doc = pymupdf.open()
    for page_idx in range(1, 201):
        page = doc.new_page()
        page.insert_text(
            (50, 50),
            f"Page {page_idx}: Account Statement Transaction Record for Client Mr. Navnit Rai",
            fontsize=10,
        )
    pdf_bytes = doc.tobytes()
    doc.close()

    # Process 200 pages
    results, total_pages = PDFService.process_pdf(pdf_bytes)

    assert total_pages == 200
    assert len(results) == 200
    assert all(not r.is_scanned for r in results)
    assert "Page 1:" in results[0].text
    assert "Page 200:" in results[199].text


def test_exceeding_max_pages_limit():
    from fastapi import HTTPException

    doc = pymupdf.open()
    for _ in range(205):
        doc.new_page()
    pdf_bytes = doc.tobytes()
    doc.close()

    with pytest.raises(HTTPException) as exc_info:
        PDFService.process_pdf(pdf_bytes)

    assert exc_info.value.status_code == 400
    assert "exceeds maximum allowed limit of 200 pages" in exc_info.value.detail
