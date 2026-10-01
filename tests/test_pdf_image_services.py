"""
Tests for PDF Service and Image Preprocessing Service.
"""

import io
import pytest
import pymupdf
import numpy as np
from PIL import Image
from fastapi import HTTPException

from app.services.pdf_service import PDFService
from app.services.image_service import ImageService


def create_sample_digital_pdf(text: str = "This is a digital bank statement with account details.") -> bytes:
    """Helper to generate an in-memory digital PDF with PyMuPDF."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 50), text)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def create_sample_scanned_pdf() -> bytes:
    """Helper to generate an in-memory scanned/empty text PDF page with an image."""
    doc = pymupdf.open()
    page = doc.new_page()
    # Draw a rectangle / graphic without text layer
    page.draw_rect(pymupdf.Rect(50, 50, 200, 200), color=(0, 0, 0), fill=(0.5, 0.5, 0.5))
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def create_sample_image_bytes() -> bytes:
    """Helper to create a simple PNG image."""
    img = Image.new("RGB", (200, 100), color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_digital_pdf_processing():
    sample_text = "Standard Bank Account Statement\nAccount Number: 123456789\nOpening Balance: 1000.00"
    pdf_bytes = create_sample_digital_pdf(sample_text)
    
    pages, total = PDFService.process_pdf(pdf_bytes)
    assert total == 1
    assert len(pages) == 1
    assert pages[0].is_scanned is False
    assert "Standard Bank Account Statement" in pages[0].text


def test_scanned_pdf_rendering():
    pdf_bytes = create_sample_scanned_pdf()
    pages, total = PDFService.process_pdf(pdf_bytes)
    assert total == 1
    assert len(pages) == 1
    assert pages[0].is_scanned is True
    assert len(pages[0].image_bytes) > 0


def test_corrupted_pdf_error():
    with pytest.raises(HTTPException) as exc_info:
        PDFService.process_pdf(b"not a valid pdf content")
    assert exc_info.value.status_code == 400
    assert "Failed to parse PDF document" in exc_info.value.detail


def test_image_service_preprocessing():
    img_bytes = create_sample_image_bytes()
    img_array = ImageService.load_image_from_bytes(img_bytes)
    assert isinstance(img_array, np.ndarray)
    assert len(img_array.shape) == 3

    # Default 3-channel preprocessed for PaddleOCR stability
    preprocessed_3c = ImageService.preprocess_image(img_array, enhance_contrast=True, auto_deskew=True, as_3channel=True)
    assert isinstance(preprocessed_3c, np.ndarray)
    assert len(preprocessed_3c.shape) == 3

    # Direct grayscale output
    preprocessed_gray = ImageService.preprocess_image(img_array, enhance_contrast=True, auto_deskew=True, as_3channel=False)
    assert isinstance(preprocessed_gray, np.ndarray)
    assert len(preprocessed_gray.shape) == 2

    encoded_bytes = ImageService.image_to_bytes(preprocessed_3c, ".png")
    assert len(encoded_bytes) > 0


def create_sample_encrypted_pdf(text: str = "Confidential Payroll Data", password: str = "secret123") -> bytes:
    """Helper to generate an encrypted PDF."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 50), text)
    pdf_bytes = doc.tobytes(
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw=password,
        owner_pw="ownerpass"
    )
    doc.close()
    return pdf_bytes


def test_password_protected_pdf_success():
    pdf_bytes = create_sample_encrypted_pdf(text="Protected Account Statement", password="mypassword")
    pages, total = PDFService.process_pdf(pdf_bytes, password="mypassword")
    assert total == 1
    assert "Protected Account Statement" in pages[0].text


def test_password_protected_pdf_missing_password():
    pdf_bytes = create_sample_encrypted_pdf(text="Protected Account Statement", password="mypassword")
    with pytest.raises(HTTPException) as exc_info:
        PDFService.process_pdf(pdf_bytes, password=None)
    assert exc_info.value.status_code == 400
    assert "PDF is password-protected" in exc_info.value.detail


def test_password_protected_pdf_wrong_password():
    pdf_bytes = create_sample_encrypted_pdf(text="Protected Account Statement", password="mypassword")
    with pytest.raises(HTTPException) as exc_info:
        PDFService.process_pdf(pdf_bytes, password="wrongpassword")
    assert exc_info.value.status_code == 400
    assert "Incorrect password" in exc_info.value.detail

