"""
PDF Processing Service using PyMuPDF (pymupdf).
"""

from typing import List, Optional, Tuple
from fastapi import HTTPException, status
import pymupdf
import numpy as np

from app.core.config import get_settings


class PDFPageResult:
    """Represents the text or rendered image of a single PDF page."""
    def __init__(
        self,
        page_number: int,
        is_scanned: bool,
        text: str = "",
        image_bytes: bytes = b"",
    ):
        self.page_number = page_number
        self.is_scanned = is_scanned
        self.text = text
        self.image_bytes = image_bytes


class PDFService:
    """Handles PDF inspection, digital text extraction, and page rendering for OCR in 10-page chunked parts."""

    @classmethod
    def get_pdf_page_count(
        cls,
        pdf_bytes: bytes,
        password: Optional[str] = None,
    ) -> int:
        """
        Quickly inspects PDF document and returns total page count.
        Validates encryption and enforces max_pdf_pages limit.
        """
        settings = get_settings()

        try:
            doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to parse PDF document (corrupted or invalid format): {str(e)}"
            )

        # Handle password-protected / encrypted PDFs
        if doc.is_encrypted or doc.needs_pass:
            if not password:
                doc.close()
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="PDF is password-protected. Please provide the 'password' parameter."
                )
            auth_success = doc.authenticate(password)
            if not auth_success:
                doc.close()
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="PDF authentication failed: Incorrect password provided."
                )

        total_pages = len(doc)
        doc.close()

        if total_pages == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="PDF document contains 0 pages."
            )

        if total_pages > settings.max_pdf_pages:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"PDF page count ({total_pages}) exceeds maximum allowed limit of {settings.max_pdf_pages} pages."
            )

        return total_pages

    @classmethod
    def process_pdf_chunk(
        cls,
        pdf_bytes: bytes,
        start_page: int,
        end_page: int,
        password: Optional[str] = None,
        dpi: Optional[int] = None,
        min_digital_chars_per_page: int = 30,
    ) -> List[PDFPageResult]:
        """
        Extracts digital text or renders images ONLY for a specific 1-indexed page range [start_page, end_page].
        Allows processing 10-page parts sequentially without exhausting memory on large documents.
        """
        settings = get_settings()
        effective_dpi = dpi or settings.pdf_render_dpi

        try:
            doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to parse PDF document: {str(e)}"
            )

        if doc.is_encrypted or doc.needs_pass:
            if not password:
                doc.close()
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="PDF is password-protected. Please provide the 'password' parameter."
                )
            if not doc.authenticate(password):
                doc.close()
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="PDF authentication failed: Incorrect password provided."
                )

        total_pages = len(doc)
        clamped_start = max(1, start_page)
        clamped_end = min(total_pages, end_page)

        page_results: List[PDFPageResult] = []
        zoom = effective_dpi / 72.0
        matrix = pymupdf.Matrix(zoom, zoom)

        try:
            for i in range(clamped_start - 1, clamped_end):
                page = doc[i]
                page_num = i + 1
                embedded_text = page.get_text("text").strip()

                if len(embedded_text) >= min_digital_chars_per_page:
                    page_results.append(
                        PDFPageResult(
                            page_number=page_num,
                            is_scanned=False,
                            text=embedded_text,
                        )
                    )
                else:
                    pix = page.get_pixmap(matrix=matrix, alpha=False)
                    img_bytes = pix.tobytes("jpg")
                    del pix
                    page_results.append(
                        PDFPageResult(
                            page_number=page_num,
                            is_scanned=True,
                            text=embedded_text,
                            image_bytes=img_bytes,
                        )
                    )
        finally:
            doc.close()

        return page_results

    @classmethod
    def process_pdf(
        cls,
        pdf_bytes: bytes,
        password: Optional[str] = None,
        dpi: int = 300,
        min_digital_chars_per_page: int = 30,
    ) -> Tuple[List[PDFPageResult], int]:
        """
        Parses PDF document. Extracts embedded digital text directly, or renders
        scanned pages to high-resolution image bytes for downstream OCR.
        Handles password-protected / encrypted PDFs safely.
        """
        total_pages = cls.get_pdf_page_count(pdf_bytes, password=password)
        page_results = cls.process_pdf_chunk(
            pdf_bytes=pdf_bytes,
            start_page=1,
            end_page=total_pages,
            password=password,
            dpi=dpi,
            min_digital_chars_per_page=min_digital_chars_per_page,
        )
        return page_results, total_pages
