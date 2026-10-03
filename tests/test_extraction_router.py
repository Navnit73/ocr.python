"""
Unit Tests for Intelligent Extraction Router and Engine Selection.
"""

import pytest
import pymupdf
from unittest.mock import AsyncMock, patch

from app.schemas.ocr import ExtractionEngineEnum
from app.services.extraction_router import ExtractionRouter
from app.services.engine_interface import EngineResult


def create_sample_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Standard invoice text for test routing.")
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


@pytest.mark.asyncio
async def test_router_explicit_pymupdf():
    """Verify explicit selection of PyMuPDF engine."""
    pdf_bytes = create_sample_pdf()
    router = ExtractionRouter()

    res = await router.route_and_extract(
        file_bytes=pdf_bytes,
        filename="test.pdf",
        doc_category="pdf",
        requested_engine=ExtractionEngineEnum.PYMUPDF.value,
    )

    assert res.engine_name == "pymupdf"
    assert res.metadata["executed_engine"] == "pymupdf"
    assert "Standard invoice" in res.raw_text


@pytest.mark.asyncio
async def test_router_explicit_paddleocr():
    """Verify explicit selection of PaddleOCR engine."""
    pdf_bytes = create_sample_pdf()
    router = ExtractionRouter()

    res = await router.route_and_extract(
        file_bytes=pdf_bytes,
        filename="test.pdf",
        doc_category="pdf",
        requested_engine=ExtractionEngineEnum.PADDLEOCR.value,
    )

    assert res.engine_name == "paddleocr"
    assert res.metadata["executed_engine"] == "paddleocr"


@pytest.mark.asyncio
async def test_router_explicit_docling():
    """Verify explicit selection of IBM Docling engine."""
    pdf_bytes = create_sample_pdf()
    router = ExtractionRouter()

    res = await router.route_and_extract(
        file_bytes=pdf_bytes,
        filename="test.pdf",
        doc_category="pdf",
        requested_engine=ExtractionEngineEnum.DOCLING.value,
    )

    assert res.engine_name == "docling"
    assert res.metadata["executed_engine"] == "docling"
    assert "Standard invoice" in res.raw_text


@pytest.mark.asyncio
async def test_router_auto_selection():
    """Verify automatic deterministic engine selection."""
    pdf_bytes = create_sample_pdf()
    router = ExtractionRouter()

    res = await router.route_and_extract(
        file_bytes=pdf_bytes,
        filename="test.pdf",
        doc_category="pdf",
        requested_engine=ExtractionEngineEnum.AUTO.value,
    )

    assert res.engine_name in ("docling", "pymupdf")
    assert res.metadata["routing_engine"] == "auto"


@pytest.mark.asyncio
async def test_router_fallback_on_primary_failure():
    """Verify router catches primary engine failure and recovers with fallback engine."""
    pdf_bytes = create_sample_pdf()
    router = ExtractionRouter()

    # Mock DoclingEngine to simulate failure
    with patch.object(router._docling_engine, "extract_document", side_effect=RuntimeError("Simulated OOM Error")):
        res = await router.route_and_extract(
            file_bytes=pdf_bytes,
            filename="test.pdf",
            doc_category="pdf",
            requested_engine=ExtractionEngineEnum.DOCLING.value,
        )

        assert res.metadata["fallback_used"] is True
        assert res.engine_name in ("pymupdf", "paddleocr")
        assert any(w.code == "ENGINE_FALLBACK" for w in res.warnings)
