"""
Document Normalizer Service.
Normalizes heterogeneous extraction outputs from PyMuPDF, PaddleOCR, and IBM Docling into a consistent format.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from app.schemas.ocr import ExtractionWarning, OCRLine, PageExtraction
from app.services.engine_interface import EngineResult, ExtractedHeading, ExtractedTable

logger = logging.getLogger("document_normalizer")


class DocumentNormalizer:
    """Normalizes extraction outputs across all OCR and document parsing engines."""

    @staticmethod
    def normalize_engine_result(result: EngineResult) -> EngineResult:
        """
        Ensures consistent page numbering, non-empty text fields, sorted tables, and valid Markdown.
        """
        # 1. Normalize Pages
        normalized_pages: List[PageExtraction] = []
        for idx, p in enumerate(result.pages):
            page_no = p.page_number if p.page_number > 0 else (idx + 1)
            normalized_lines: List[OCRLine] = []
            for line in p.lines:
                clean_text = line.text.strip()
                if clean_text:
                    normalized_lines.append(
                        OCRLine(
                            text=clean_text,
                            confidence=max(0.0, min(1.0, round(line.confidence, 4))),
                            bbox=line.bbox,
                        )
                    )
            
            page_text = p.text.strip() if p.text else "\n".join(l.text for l in normalized_lines)
            normalized_pages.append(
                PageExtraction(
                    page_number=page_no,
                    text=page_text,
                    confidence=max(0.0, min(1.0, round(p.confidence, 4))),
                    lines=normalized_lines,
                    is_scanned=p.is_scanned,
                )
            )

        if not normalized_pages:
            normalized_pages = [
                PageExtraction(
                    page_number=1,
                    text=result.raw_text.strip(),
                    confidence=1.0,
                    lines=[],
                    is_scanned=result.is_scanned,
                )
            ]

        # 2. Normalize Raw Text
        full_raw_text = result.raw_text.strip() if result.raw_text else "\n\n".join(p.text for p in normalized_pages if p.text).strip()

        # 3. Normalize Markdown
        markdown_output = result.markdown
        if not markdown_output or not markdown_output.strip():
            # Synthesize Markdown from pages and tables
            md_blocks: List[str] = []
            for p in normalized_pages:
                if p.text:
                    md_blocks.append(f"<!-- Page {p.page_number} -->\n{p.text}")
            for t in result.tables:
                if t.markdown:
                    md_blocks.append(f"\n{t.markdown}\n")
            markdown_output = "\n\n".join(md_blocks) if md_blocks else full_raw_text

        # 4. Normalize Tables
        normalized_tables: List[ExtractedTable] = []
        for idx, t in enumerate(result.tables):
            grid = [[str(cell or "").strip() for cell in row] for row in t.grid] if t.grid else []
            headers = [str(h or "").strip() for h in t.headers] if t.headers else (grid[0] if grid else [])
            t_md = t.markdown.strip() if t.markdown else DocumentNormalizer._grid_to_markdown(grid, headers)
            normalized_tables.append(
                ExtractedTable(
                    page_number=t.page_number if t.page_number > 0 else 1,
                    table_index=idx,
                    num_rows=len(grid),
                    num_cols=len(headers) if headers else 0,
                    headers=headers,
                    grid=grid,
                    markdown=t_md,
                    bbox=t.bbox,
                    confidence=max(0.0, min(1.0, round(t.confidence, 4))),
                )
            )

        return EngineResult(
            engine_name=result.engine_name,
            pages=normalized_pages,
            raw_text=full_raw_text,
            markdown=markdown_output,
            tables=normalized_tables,
            headings=result.headings,
            structured_json=result.structured_json,
            is_scanned=result.is_scanned,
            confidence=max(0.0, min(1.0, round(result.confidence, 4))),
            warnings=result.warnings,
            metadata=result.metadata,
        )

    @staticmethod
    def _grid_to_markdown(grid: List[List[str]], headers: List[str]) -> str:
        """Converts a 2D text matrix into a standard Markdown table."""
        if not grid and not headers:
            return ""
        cols = headers if headers else (grid[0] if grid else [])
        if not cols:
            return ""
        
        md_lines = ["| " + " | ".join(cols) + " |"]
        md_lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
        
        start_row = 1 if (grid and grid[0] == cols) else 0
        for row in grid[start_row:]:
            # Pad or truncate row to match header length
            padded = row + [""] * (len(cols) - len(row)) if len(row) < len(cols) else row[:len(cols)]
            md_lines.append("| " + " | ".join(padded) + " |")
        return "\n".join(md_lines)

    @staticmethod
    def prepare_llm_context(result: EngineResult) -> str:
        """
        Builds high-information context combining structured markdown, tables, and raw text for DeepSeek.
        """
        sections: List[str] = []

        # If high-fidelity Markdown is present, prioritize it
        if result.markdown and result.markdown.strip():
            sections.append(result.markdown.strip())
        else:
            sections.append(result.raw_text.strip())

        # If tables were extracted separately and not already in markdown, append them
        if result.tables and (not result.markdown or " | " not in result.markdown):
            sections.append("\n\n### Extracted Tables:")
            for idx, t in enumerate(result.tables):
                sections.append(f"\n#### Table {idx + 1} (Page {t.page_number}):\n{t.markdown}")

        return "\n\n".join(sections)
