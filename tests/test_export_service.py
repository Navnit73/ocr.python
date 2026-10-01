"""
Tests for Document Export Service and Export API Endpoints (Excel, CSV, PDF).
"""

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


def test_generate_excel(sample_bank_extraction):
    file_bytes = ExportService.generate_excel(
        doc_id="test_doc_01",
        document_type="bank_statement",
        extraction=sample_bank_extraction,
    )
    assert isinstance(file_bytes, bytes)
    assert len(file_bytes) > 0
    # Check Excel zip signature
    assert file_bytes[:2] == b"PK"


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
    # Check PDF signature
    assert pdf_bytes.startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_export_download_by_id_e2e(sample_bank_extraction):
    # Seed cache
    doc_id = "cache_test_id_7788"
    ResultCache.set(
        doc_id,
        {
            "id": doc_id,
            "document_type": "bank_statement",
            "extraction": sample_bank_extraction,
            "raw_text": "Sample text",
        },
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Download Excel
        res_xlsx = await client.get(f"/api/v1/export/download/{doc_id}?format=xlsx")
        assert res_xlsx.status_code == 200
        assert "application/vnd.openxmlformats" in res_xlsx.headers["content-type"]
        assert "bank_statement_cache_test_id_7788.xlsx" in res_xlsx.headers["content-disposition"]
        assert res_xlsx.content[:2] == b"PK"

        # 2. Download CSV
        res_csv = await client.get(f"/api/v1/export/download/{doc_id}?format=csv")
        assert res_csv.status_code == 200
        assert "text/csv" in res_csv.headers["content-type"]
        assert "bank_statement_cache_test_id_7788.csv" in res_csv.headers["content-disposition"]

        # 3. Download PDF
        res_pdf = await client.get(f"/api/v1/export/download/{doc_id}?format=pdf")
        assert res_pdf.status_code == 200
        assert "application/pdf" in res_pdf.headers["content-type"]
        assert res_pdf.content.startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_export_download_not_found():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/export/download/nonexistent_id_9999?format=xlsx")
        assert res.status_code == 404
        assert "not found or has expired" in res.json()["error"]


@pytest.mark.asyncio
async def test_export_direct_generate_e2e(sample_bank_extraction):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "id": "direct_req_123",
            "document_type": "bank_statement",
            "extraction": sample_bank_extraction,
            "raw_text": "Sample direct text",
        }
        res = await client.post("/api/v1/export/generate?format=xlsx", json=payload)
        assert res.status_code == 200
        assert res.content[:2] == b"PK"
        assert "bank_statement_direct_req_123.xlsx" in res.headers["content-disposition"]
