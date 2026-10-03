"""
PyMuPDF Extraction Engine.
Provides high-speed digital PDF text extraction, layout parsing, native table detection, and metadata extraction.
"""

import asyncio
import io
import logging
import time
from typing import Any, List, Optional
import numpy as np
import pymupdf

from app.core.config import get_settings
from app.schemas.ocr import ExtractionWarning, OCRLine, PageExtraction
from app.services.engine_interface import BaseExtractionEngine, EngineResult, ExtractedHeading, ExtractedTable

logger = logging.getLogger("pymupdf_engine")


class PyMuPDFEngine:
    """Extraction engine using PyMuPDF for fast digital text and native PDF table parsing."""

    @property
    def engine_name(self) -> str:
        return "pymupdf"

    async def extract_document(
        self,
        file_bytes: bytes,
        filename: str,
        doc_category: str = "pdf",
        language: str = "en",
        password: Optional[str] = None,
        enable_ocr: bool = True,
        extract_tables: bool = True,
        **kwargs: Any,
    ) -> EngineResult:
        """
        Extracts text, pages, and tables using PyMuPDF asynchronously in a worker thread.
        """
        return await asyncio.to_thread(
            self._extract_sync,
            file_bytes=file_bytes,
            filename=filename,
            doc_category=doc_category,
            language=language,
            password=password,
            extract_tables=extract_tables,
        )

    def _extract_sync(
        self,
        file_bytes: bytes,
        filename: str,
        doc_category: str = "pdf",
        language: str = "en",
        password: Optional[str] = None,
        extract_tables: bool = True,
    ) -> EngineResult:
        """Synchronous PyMuPDF extraction worker."""
        t0 = time.perf_counter()
        warnings: List[ExtractionWarning] = []
        pages: List[PageExtraction] = []
        tables: List[ExtractedTable] = []
        headings: List[ExtractedHeading] = []
        is_document_scanned = False
        settings = get_settings()

        try:
            if doc_category == "pdf":
                doc = pymupdf.open(stream=file_bytes, filetype="pdf")
                if doc.is_encrypted or doc.needs_pass:
                    if not password or not doc.authenticate(password):
                        doc.close()
                        raise ValueError("PDF is password-protected or authentication failed.")
            else:
                # Image file: load into PyMuPDF via image stream
                doc = pymupdf.open(stream=file_bytes, filetype=filename.split(".")[-1].lower() if "." in filename else "png")

            total_pages = len(doc)
            if total_pages == 0:
                doc.close()
                return EngineResult(
                    engine_name=self.engine_name,
                    raw_text="",
                    warnings=[ExtractionWarning(code="EMPTY_DOCUMENT", message="Document contains 0 pages.")],
                )

            for page_idx in range(total_pages):
                page = doc[page_idx]
                page_no = page_idx + 1
                page_text = page.get_text("text").strip()

                # Extract line-level information with bounding boxes
                lines: List[OCRLine] = []
                blocks = page.get_text("blocks")  # (x0, y0, x1, y1, "text", block_no, block_type)
                for b in blocks:
                    if len(b) >= 5 and b[4]:
                        block_text = str(b[4]).strip()
                        if block_text:
                            # Estimate lines inside block
                            bbox = [[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]]
                            # Check if block looks like a heading (short text, large font or standalone)
                            if len(block_text) < 80 and "\n" not in block_text:
                                headings.append(
                                    ExtractedHeading(
                                        text=block_text,
                                        level=1 if page_no == 1 and len(headings) == 0 else 2,
                                        page_number=page_no,
                                    )
                                )
                            for raw_line in block_text.splitlines():
                                cl = raw_line.strip()
                                if cl:
                                    lines.append(
                                        OCRLine(
                                            text=cl,
                                            confidence=1.0,
                                            bbox=bbox,
                                        )
                                    )

                # Check if page is scanned / has no embedded text
                is_scanned = len(page_text) < 30
                if is_scanned:
                    is_document_scanned = True

                # Extract native PDF tables if enabled
                if extract_tables and doc_category == "pdf":
                    try:
                        table_finder = page.find_tables()
                        if table_finder and table_finder.tables:
                            for t_idx, t in enumerate(table_finder.tables):
                                df = t.extract()
                                if df and len(df) > 0:
                                    headers = [str(c or "").strip() for c in df[0]]
                                    grid = [[str(c or "").strip() for c in row] for row in df]
                                    md_lines = []
                                    md_lines.append("| " + " | ".join(headers) + " |")
                                    md_lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
                                    for row in grid[1:]:
                                        md_lines.append("| " + " | ".join(row) + " |")
                                    t_md = "\n".join(md_lines)
                                    tables.append(
                                        ExtractedTable(
                                            page_number=page_no,
                                            table_index=t_idx,
                                            num_rows=len(grid),
                                            num_cols=len(headers),
                                            headers=headers,
                                            grid=grid,
                                            markdown=t_md,
                                            bbox=list(t.bbox) if hasattr(t, "bbox") else None,
                                        )
                                    )
                    except Exception as e:
                        logger.debug(f"PyMuPDF table extraction notice on page {page_no}: {e}")

                pages.append(
                    PageExtraction(
                        page_number=page_no,
                        text=page_text,
                        confidence=1.0 if not is_scanned else 0.5,
                        lines=lines,
                        is_scanned=is_scanned,
                    )
                )

            doc.close()

        except Exception as e:
            logger.error(f"PyMuPDF extraction failed: {e}", exc_info=True)
            warnings.append(
                ExtractionWarning(
                    code="PYMUPDF_EXTRACTION_ERROR",
                    message=f"PyMuPDF engine failed: {str(e)}",
                    severity="error",
                )
            )

        raw_text = "\n\n".join(p.text for p in pages if p.text).strip()
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        # Build basic markdown representation
        md_parts = []
        for p in pages:
            if p.text:
                md_parts.append(f"<!-- Page {p.page_number} -->\n{p.text}")
        for t in tables:
            if t.markdown:
                md_parts.append(f"\n{t.markdown}\n")
        full_md = "\n\n".join(md_parts) if md_parts else None

        return EngineResult(
            engine_name=self.engine_name,
            pages=pages,
            raw_text=raw_text,
            markdown=full_md,
            tables=tables,
            headings=headings,
            is_scanned=is_document_scanned,
            confidence=1.0 if not is_document_scanned else 0.7,
            warnings=warnings,
            metadata={
                "processing_time_ms": elapsed_ms,
                "total_pages": len(pages),
                "tables_count": len(tables),
                "headings_count": len(headings),
            },
        )
