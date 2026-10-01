"""
Tests for Document Export Service and Export API Endpoints (Excel, CSV, PDF).
"""

import io
import openpyxl
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.export_service import ExportService
from app.services.result_cache import ResultCache


@pytest.fixture
def sample_bank_extraction():
    return {
        "bank_name": "State Bank of India",
        "account_holder": "Mr. NAVNIT RAI",
        "account_number_masked": "XXXX-123456",
        "currency": "INR",
        "statement_period": "2026-09-01 to 2026-09-30",
        "opening_balance": 10000.00,
        "closing_balance": 34500.00,
        "transactions": [
            {
                "date": "2026-09-01",
                "description": "UPI PAYMENT",
                "reference": "UPI123456",
                "debit": 500.00,
                "credit": None,
                "balance": 9500.00,
            },
            {
                "date": "2026-09-02",
                "description": "SALARY CREDIT",
                "reference": "SAL9988",
                "debit": None,
                "credit": 25000.00,
                "balance": 34500.00,
            },
        ],
    }


@pytest.fixture
def sample_invoice_extraction():
    return {
        "invoice_number": "INV-2026-8800",
        "invoice_date": "2026-09-10",
        "due_date": "2026-10-10",
        "currency": "USD",
        "supplier": {"name": "Cloud Infra Inc.", "address": "123 Tech Blvd", "tax_id": "US-889900"},
        "customer": {"name": "Alpha Corp", "address": "456 Market St", "tax_id": "US-112233"},
        "subtotal": 5000.00,
        "tax": 450.00,
        "total": 5450.00,
        "line_items": [
            {"description": "Server Hosting", "quantity": 2, "unit_price": 2000.00, "tax_rate": "9%", "amount": 4000.00},
            {"description": "Database Backup", "quantity": 1, "unit_price": 1000.00, "tax_rate": "9%", "amount": 1000.00},
        ],
    }


def test_analytics_categorization_and_subscriptions(sample_bank_extraction):
    from app.services.analytics_service import AnalyticsService
    analytics = AnalyticsService.analyze("bank_statement", sample_bank_extraction)

    assert analytics.total_inflow == 25000.00
    assert analytics.total_outflow == 500.00
    assert analytics.net_savings == 24500.00
    assert analytics.transaction_count == 2
    assert len(analytics.ai_insights) > 0

    # Test category matching
    assert AnalyticsService.categorize_description("NETFLIX.COM PAYMENT") == "Entertainment & Subscriptions"
    assert AnalyticsService.categorize_description("STARBUCKS COFFEE") == "Food & Dining"
    assert AnalyticsService.categorize_description("MONTHLY SALARY TRANSFER") == "Salary & Income"
    assert AnalyticsService.categorize_description("UBER TRIP") == "Travel & Commute"


def test_generate_excel_bank_statement(sample_bank_extraction):
    file_bytes = ExportService.generate_excel(
        doc_id="test_doc_01",
        document_type="bank_statement",
        extraction=sample_bank_extraction,
    )
    assert isinstance(file_bytes, bytes)
    assert len(file_bytes) > 0
    assert file_bytes[:2] == b"PK"

    # Verify workbook structure and no diagonal column drift
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes))
    assert "Executive Dashboard" in wb.sheetnames
    assert "Itemized Ledger" in wb.sheetnames

    ws_ledger = wb["Itemized Ledger"]
    # Row 4 is header, Row 5 is Tx 1, Row 6 is Tx 2, Row 7 is Summary
    assert ws_ledger.cell(row=5, column=1).value == "2026-09-01"
    assert ws_ledger.cell(row=5, column=3).value == "UPI PAYMENT"
    assert ws_ledger.cell(row=5, column=5).value == 500.00

    assert ws_ledger.cell(row=6, column=1).value == "2026-09-02"
    assert ws_ledger.cell(row=6, column=3).value == "SALARY CREDIT"
    assert ws_ledger.cell(row=6, column=6).value == 25000.00


def test_generate_excel_invoice(sample_invoice_extraction):
    file_bytes = ExportService.generate_excel(
        doc_id="test_inv_01",
        document_type="invoice",
        extraction=sample_invoice_extraction,
    )
    assert isinstance(file_bytes, bytes)
    assert file_bytes[:2] == b"PK"

    wb = openpyxl.load_workbook(io.BytesIO(file_bytes))
    ws_ledger = wb["Itemized Ledger"]
    # Verify line items on separate rows
    assert ws_ledger.cell(row=5, column=1).value == "Server Hosting"
    assert ws_ledger.cell(row=5, column=3).value == 2
    assert ws_ledger.cell(row=5, column=6).value == 4000.00

    assert ws_ledger.cell(row=6, column=1).value == "Database Backup"
    assert ws_ledger.cell(row=6, column=3).value == 1
    assert ws_ledger.cell(row=6, column=6).value == 1000.00


def test_generate_csv(sample_bank_extraction):
    csv_bytes = ExportService.generate_csv(
        doc_id="test_doc_01",
        document_type="bank_statement",
        extraction=sample_bank_extraction,
    )
    assert isinstance(csv_bytes, bytes)
    csv_text = csv_bytes.decode("utf-8-sig")
    assert "State Bank of India" in csv_text
    assert "Mr. NAVNIT RAI" in csv_text
    assert "UPI PAYMENT" in csv_text


def test_generate_pdf(sample_bank_extraction):
    pdf_bytes = ExportService.generate_pdf(
        doc_id="test_doc_01",
        document_type="bank_statement",
        extraction=sample_bank_extraction,
    )
    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 0
    assert pdf_bytes.startswith(b"%PDF-")


def test_generate_pdf_invoice(sample_invoice_extraction):
    pdf_bytes = ExportService.generate_pdf(
        doc_id="test_inv_01",
        document_type="invoice",
        extraction=sample_invoice_extraction,
    )
    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes.startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_export_download_by_id_e2e(sample_bank_extraction):
    from app.core.security import hash_key
    doc_id = "cache_test_id_7788"
    auth_headers = {"X-API-Key": "ocr_dev_key_secret_2026"}
    owner_hash = hash_key("ocr_dev_key_secret_2026")

    # Seed cache
    ResultCache.set(
        doc_id,
        {
            "id": doc_id,
            "document_type": "bank_statement",
            "extraction": sample_bank_extraction,
            "raw_text": "Sample text",
        },
        owner_hash=owner_hash,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Download Excel
        res_xlsx = await client.get(f"/api/v1/export/download/{doc_id}?format=xlsx", headers=auth_headers)
        assert res_xlsx.status_code == 200
        assert "application/vnd.openxmlformats" in res_xlsx.headers["content-type"]
        assert "bank_statement_cache_test_id_7788.xlsx" in res_xlsx.headers["content-disposition"]
        assert res_xlsx.content[:2] == b"PK"

        # 2. Download CSV
        res_csv = await client.get(f"/api/v1/export/download/{doc_id}?format=csv", headers=auth_headers)
        assert res_csv.status_code == 200
        assert "text/csv" in res_csv.headers["content-type"]
        assert "bank_statement_cache_test_id_7788.csv" in res_csv.headers["content-disposition"]

        # 3. Download PDF
        res_pdf = await client.get(f"/api/v1/export/download/{doc_id}?format=pdf", headers=auth_headers)
        assert res_pdf.status_code == 200
        assert "application/pdf" in res_pdf.headers["content-type"]
        assert res_pdf.content.startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_export_download_not_found():
    auth_headers = {"X-API-Key": "ocr_dev_key_secret_2026"}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/export/download/nonexistent_id_9999?format=xlsx", headers=auth_headers)
        assert res.status_code == 404
        assert "not found or has expired" in res.json()["error"]


@pytest.mark.asyncio
async def test_export_direct_generate_e2e(sample_bank_extraction):
    auth_headers = {"X-API-Key": "ocr_dev_key_secret_2026"}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "id": "direct_req_123",
            "document_type": "bank_statement",
            "extraction": sample_bank_extraction,
            "raw_text": "Sample direct text",
        }
        res = await client.post("/api/v1/export/generate?format=xlsx", json=payload, headers=auth_headers)
        assert res.status_code == 200
        assert res.content[:2] == b"PK"
        assert "bank_statement_direct_req_123.xlsx" in res.headers["content-disposition"]
