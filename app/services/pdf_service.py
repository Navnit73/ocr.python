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
    """Handles PDF inspection, digital text extraction, and page rendering for OCR."""

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
        if total_pages == 0:
            doc.close()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="PDF document contains 0 pages."
            )

        if total_pages > settings.max_pdf_pages:
            doc.close()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"PDF page count ({total_pages}) exceeds maximum allowed limit of {settings.max_pdf_pages} pages."
            )

        page_results: List[PDFPageResult] = []
        zoom = dpi / 72.0  # 72 points per inch default PDF coordinate system
        matrix = pymupdf.Matrix(zoom, zoom)

        try:
            for i, page in enumerate(doc):
                page_num = i + 1
                # Try extracting embedded digital text
                embedded_text = page.get_text("text").strip()

                # If the page contains substantial embedded text, use it directly (fast path)
                if len(embedded_text) >= min_digital_chars_per_page:
                    page_results.append(
                        PDFPageResult(
                            page_number=page_num,
                            is_scanned=False,
                            text=embedded_text,
                        )
                    )
                else:
                    # Render page to high-res pixmap for OCR
                    pix = page.get_pixmap(matrix=matrix, alpha=False)
                    img_bytes = pix.tobytes("png")
                    page_results.append(
                        PDFPageResult(
                            page_number=page_num,
                            is_scanned=True,
                            text=embedded_text,  # Keep any partial text
                            image_bytes=img_bytes,
                        )
                    )
        finally:
            doc.close()

        return page_results, total_pages
