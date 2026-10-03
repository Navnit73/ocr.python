"""
IBM Docling Document Extraction Engine.
Provides multi-modal layout recognition, advanced table reconstruction, heading/paragraph segmentation,
OCR fallback, and Markdown / JSON structured export.
"""

import asyncio
import io
import logging
import os
import time
from typing import Any, Dict, List, Optional
import numpy as np

from app.core.config import get_settings
from app.schemas.ocr import ExtractionWarning, OCRLine, PageExtraction
from app.services.engine_interface import BaseExtractionEngine, EngineResult, ExtractedHeading, ExtractedTable
from app.services.file_validator import managed_temp_file

logger = logging.getLogger("docling_engine")


class DoclingEngine:
    """Production-grade extraction engine leveraging IBM Docling."""

    _instance: Optional["DoclingEngine"] = None
    _converter: Optional[Any] = None
    _lock = asyncio.Lock()
    _semaphore = asyncio.Semaphore(2)  # Limit concurrent Docling inferences to protect CPU & memory

    def __init__(self):
        self._init_converter()

    @property
    def engine_name(self) -> str:
        return "docling"

    @classmethod
    def get_instance(cls) -> "DoclingEngine":
        """Singleton accessor for DoclingEngine."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _init_converter(self) -> None:
        """Lazily initializes and configures the DocumentConverter singleton."""
        if self._converter is not None:
            return

        try:
            from docling.document_converter import DocumentConverter, PdfFormatOption
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions

            settings = get_settings()
            pipeline_options = PdfPipelineOptions()
            pipeline_options.do_ocr = getattr(settings, "docling_ocr_enabled", True)
            pipeline_options.do_table_structure = getattr(settings, "docling_table_structure_enabled", True)

            # RapidOCR configuration
            if pipeline_options.do_ocr:
                try:
                    ocr_opts = RapidOcrOptions()
                    pipeline_options.ocr_options = ocr_opts
                except Exception as ocr_err:
                    logger.debug(f"RapidOcrOptions config notice: {ocr_err}")

            format_options = {
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
            }

            self._converter = DocumentConverter(format_options=format_options)
            logger.info("✅ IBM Docling DocumentConverter initialized successfully.")
        except Exception as e:
            logger.warning(f"Failed to initialize Docling DocumentConverter: {e}. Will attempt on-demand init.")
            self._converter = None

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
        Asynchronously converts a document using Docling with concurrency bounding.
        """
        async with self._semaphore:
            return await asyncio.to_thread(
                self._extract_sync,
                file_bytes=file_bytes,
                filename=filename,
                doc_category=doc_category,
                language=language,
                password=password,
                enable_ocr=enable_ocr,
                extract_tables=extract_tables,
            )

    def _extract_sync(
        self,
        file_bytes: bytes,
        filename: str,
        doc_category: str = "pdf",
        language: str = "en",
        password: Optional[str] = None,
        enable_ocr: bool = True,
        extract_tables: bool = True,
    ) -> EngineResult:
        """
        Synchronous worker executing Docling document conversion.
        """
        t0 = time.perf_counter()
        warnings: List[ExtractionWarning] = []
        pages: List[PageExtraction] = []
        tables: List[ExtractedTable] = []
        headings: List[ExtractedHeading] = []
        markdown_text: Optional[str] = None
        structured_json: Optional[Dict[str, Any]] = None

        if self._converter is None:
            self._init_converter()

        if self._converter is None:
            raise RuntimeError("Docling DocumentConverter is not available or failed initialization.")

        suffix = f".{filename.split('.')[-1].lower()}" if "." in filename else (".pdf" if doc_category == "pdf" else ".png")

        try:
            from docling.datamodel.document import DocumentStream

            # Use DocumentStream for memory-efficient in-memory streaming
            stream = DocumentStream(name=filename, stream=io.BytesIO(file_bytes))
            conversion_result = self._converter.convert(stream, raises_on_error=True)
            docling_doc = conversion_result.document

            # 1. Export Markdown & JSON Representation
            try:
                markdown_text = docling_doc.export_to_markdown()
            except Exception as md_err:
                logger.warning(f"Docling markdown export warning: {md_err}")
                markdown_text = None

            try:
                structured_json = docling_doc.export_to_dict()
            except Exception as dict_err:
                logger.warning(f"Docling dict export warning: {dict_err}")
                structured_json = None

            # 2. Extract Tables
            if extract_tables and hasattr(docling_doc, "tables"):
                for t_idx, table_item in enumerate(docling_doc.tables):
                    try:
                        grid_data: List[List[str]] = []
                        if hasattr(table_item, "data") and hasattr(table_item.data, "grid"):
                            grid_data = [[str(cell.text or "").strip() for cell in row] for row in table_item.data.grid]
                        
                        headers = grid_data[0] if grid_data else []
                        num_rows = len(grid_data)
                        num_cols = len(headers) if headers else 0

                        # Get table markdown
                        try:
                            t_md = table_item.export_to_markdown(doc=docling_doc)
                        except TypeError:
                            t_md = table_item.export_to_markdown()
                        except Exception:
                            t_md = ""

                        # Determine page number for table
                        table_page_no = 1
                        if hasattr(table_item, "prov") and table_item.prov:
                            first_prov = table_item.prov[0]
                            if hasattr(first_prov, "page_no"):
                                table_page_no = first_prov.page_no

                        tables.append(
                            ExtractedTable(
                                page_number=table_page_no,
                                table_index=t_idx,
                                num_rows=num_rows,
                                num_cols=num_cols,
                                headers=headers,
                                grid=grid_data,
                                markdown=t_md,
                                confidence=0.95,
                            )
                        )
                    except Exception as t_err:
                        logger.warning(f"Error parsing Docling table #{t_idx}: {t_err}")

            # 3. Extract Headings & Paragraphs
            if hasattr(docling_doc, "texts"):
                for item in docling_doc.texts:
                    try:
                        label = getattr(item, "label", None)
                        text_val = getattr(item, "text", "").strip()
                        if not text_val:
                            continue

                        page_no = 1
                        if hasattr(item, "prov") and item.prov:
                            first_prov = item.prov[0]
                            if hasattr(first_prov, "page_no"):
                                page_no = first_prov.page_no

                        label_str = str(label).lower() if label else ""
                        if "title" in label_str or "heading" in label_str or "section" in label_str:
                            level = 1 if "title" in label_str or "heading_1" in label_str else 2
                            headings.append(
                                ExtractedHeading(
                                    text=text_val,
                                    level=level,
                                    page_number=page_no,
                                )
                            )
                    except Exception as h_err:
                        logger.debug(f"Heading parse notice: {h_err}")

            # 4. Reconstruct Page-Level Extractions
            page_numbers = sorted(list(docling_doc.pages.keys())) if hasattr(docling_doc, "pages") and docling_doc.pages else [1]
            page_text_map: Dict[int, List[str]] = {p: [] for p in page_numbers}
            page_lines_map: Dict[int, List[OCRLine]] = {p: [] for p in page_numbers}

            # Map texts to pages
            if hasattr(docling_doc, "texts"):
                for item in docling_doc.texts:
                    text_val = getattr(item, "text", "").strip()
                    if not text_val:
                        continue
                    p_no = 1
                    if hasattr(item, "prov") and item.prov:
                        first_prov = item.prov[0]
                        if hasattr(first_prov, "page_no"):
                            p_no = first_prov.page_no

                    if p_no not in page_text_map:
                        page_text_map[p_no] = []
                        page_lines_map[p_no] = []

                    page_text_map[p_no].append(text_val)
                    page_lines_map[p_no].append(
                        OCRLine(
                            text=text_val,
                            confidence=0.95,
                        )
                    )

            for p_no in sorted(page_text_map.keys()):
                p_text = "\n".join(page_text_map[p_no]).strip()
                pages.append(
                    PageExtraction(
                        page_number=p_no,
                        text=p_text,
                        confidence=0.95,
                        lines=page_lines_map.get(p_no, []),
                        is_scanned=False,
                    )
                )

        except Exception as e:
            logger.error(f"Docling extraction failed: {e}", exc_info=True)
            warnings.append(
                ExtractionWarning(
                    code="DOCLING_EXTRACTION_ERROR",
                    message=f"Docling engine failed: {str(e)}",
                    severity="error",
                )
            )
            # Raise to trigger fallback if configured
            raise

        # Assemble Full Raw Text
        if markdown_text and markdown_text.strip():
            raw_text = markdown_text.strip()
        else:
            raw_text = "\n\n".join(p.text for p in pages if p.text).strip()

        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        return EngineResult(
            engine_name=self.engine_name,
            pages=pages,
            raw_text=raw_text,
            markdown=markdown_text,
            tables=tables,
            headings=headings,
            structured_json=structured_json,
            is_scanned=False,
            confidence=0.95,
            warnings=warnings,
            metadata={
                "processing_time_ms": elapsed_ms,
                "total_pages": len(pages),
                "tables_count": len(tables),
                "headings_count": len(headings),
                "docling_version": "2.132.0",
            },
        )
