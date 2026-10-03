"""
Unit Tests for Document Normalizer Service.
"""

from app.schemas.ocr import OCRLine, PageExtraction
from app.services.document_normalizer import DocumentNormalizer
from app.services.engine_interface import EngineResult, ExtractedHeading, ExtractedTable


def test_normalizer_cleans_pages_and_lines():
    """Verify normalizer cleans lines, handles empty text, and bounds confidence [0, 1]."""
    raw_res = EngineResult(
        engine_name="test_engine",
        pages=[
            PageExtraction(
                page_number=1,
                text="  Hello World  ",
                confidence=0.85,
                lines=[
                    OCRLine(text="  Hello  ", confidence=0.80),
                    OCRLine(text="  World  ", confidence=0.99999),
                ],
                is_scanned=False,
            )
        ],
        raw_text="Hello World",
    )

    norm = DocumentNormalizer.normalize_engine_result(raw_res)

    assert norm.pages[0].page_number == 1
    assert norm.pages[0].confidence <= 1.0
    assert norm.pages[0].lines[0].text == "Hello"
    assert norm.pages[0].lines[1].text == "World"
    assert norm.pages[0].lines[1].confidence == 1.0


def test_normalizer_grid_to_markdown():
    """Verify table grid to markdown conversion."""
    grid = [["Item", "Cost"], ["Widget A", "$10.00"], ["Widget B", "$20.00"]]
    md = DocumentNormalizer._grid_to_markdown(grid, headers=["Item", "Cost"])

    assert "| Item | Cost |" in md
    assert "| Widget A | $10.00 |" in md
    assert "| Widget B | $20.00 |" in md


def test_normalizer_prepare_llm_context():
    """Verify LLM context builder includes markdown, raw text, and table representations."""
    table = ExtractedTable(
        page_number=1,
        table_index=0,
        num_rows=2,
        num_cols=2,
        headers=["Col1", "Col2"],
        grid=[["A", "B"]],
        markdown="| Col1 | Col2 |\n|---|---|\n| A | B |",
    )
    result = EngineResult(
        engine_name="docling",
        raw_text="Document summary statement.",
        markdown="# Document Header\n\nSome text.",
        tables=[table],
    )

    ctx = DocumentNormalizer.prepare_llm_context(result)

    assert "# Document Header" in ctx
    assert "| Col1 | Col2 |" in ctx
