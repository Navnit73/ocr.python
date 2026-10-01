"""
Tests for Accounting Direct Exports (OFX, QBO, QIF).
"""

from app.services.accounting_export_service import AccountingExportService


def test_ofx_generation():
    bank_data = {
        "bank_name": "Chase Bank",
        "account_holder": "Jane Doe",
        "account_number_masked": "XXXX-4321",
        "currency": "USD",
        "closing_balance": 15200.50,
        "transactions": [
            {"date": "2026-08-01", "description": "GROCERY MARKET", "reference": "REF001", "debit": 120.00, "credit": None},
            {"date": "2026-08-02", "description": "PAYROLL DEPOSIT", "reference": "REF002", "debit": None, "credit": 5000.00},
        ],
    }

    ofx_bytes = AccountingExportService.generate_ofx("doc_ofx_01", "bank_statement", bank_data, is_qbo=False)
    ofx_text = ofx_bytes.decode("utf-8")

    assert "OFXHEADER:100" in ofx_text
    assert "<OFX>" in ofx_text
    assert "<CURDEF>USD" in ofx_text
    assert "<TRNAMT>-120.00" in ofx_text
    assert "<TRNAMT>5000.00" in ofx_text
    assert "<BALAMT>15200.50" in ofx_text
    assert "</OFX>" in ofx_text


def test_qbo_generation():
    bank_data = {
        "bank_name": "State Bank of India",
        "account_holder": "Mr. NAVNIT RAI",
        "account_number_masked": "XXXX-123456",
        "currency": "INR",
        "closing_balance": 34500.00,
        "transactions": [
            {"date": "2026-09-01", "description": "STARBUCKS", "debit": 450.00, "credit": None},
        ],
    }

    qbo_bytes = AccountingExportService.generate_ofx("doc_qbo_01", "bank_statement", bank_data, is_qbo=True)
    qbo_text = qbo_bytes.decode("utf-8")

    assert "<INTU.BID>3000" in qbo_text
    assert "<CURDEF>INR" in qbo_text
    assert "<TRNAMT>-450.00" in qbo_text


def test_qif_generation():
    bank_data = {
        "transactions": [
            {"date": "2026-09-01", "description": "COFFEE SHOP", "reference": "TX100", "debit": 150.00},
            {"date": "2026-09-02", "description": "CONSULTING FEE", "reference": "TX101", "credit": 2500.00},
        ],
    }

    qif_bytes = AccountingExportService.generate_qif("doc_qif_01", "bank_statement", bank_data)
    qif_text = qif_bytes.decode("utf-8")

    assert "!Type:Bank" in qif_text
    assert "T-150.00" in qif_text
    assert "T2500.00" in qif_text
    assert "PCOFFEE SHOP" in qif_text
    assert "^" in qif_text
