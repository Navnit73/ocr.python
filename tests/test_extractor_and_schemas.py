"""
Tests for Structured Extractor, Classifier, and Financial Validation.
"""

import json
import pytest
from unittest.mock import AsyncMock, patch

from app.schemas.ocr import DocumentTypeEnum
from app.services.classifier import DocumentClassifier
from app.services.extractor import StructuredExtractor
from app.services.deepseek_client import DeepSeekClient


def test_document_classifier_heuristics():
    bank_text = "Standard Chartered Bank Account Number: 987654321 Opening Balance: 5000.00 Statement Period: Jan 2026 Debit: 100 Credit: 500"
    assert DocumentClassifier.classify_by_heuristics(bank_text) == DocumentTypeEnum.BANK_STATEMENT.value

    receipt_text = "Supermarket Receipt POS Terminal #4 Cashier: John Subtotal: 45.00 Change Due: 5.00 Total: 50.00"
    assert DocumentClassifier.classify_by_heuristics(receipt_text) == DocumentTypeEnum.RECEIPT.value

    invoice_text = "Tax Invoice Invoice Number: INV-2026-001 Due Date: 2026-02-15 Bill To: ACME Corp GSTIN: 27AAAAA0000A1Z5 Subtotal: 1000.00"
    assert DocumentClassifier.classify_by_heuristics(invoice_text) == DocumentTypeEnum.INVOICE.value


@pytest.mark.asyncio
async def test_bank_statement_extraction_and_audit():
    client = DeepSeekClient(api_key="mock_key")
    extractor = StructuredExtractor(client)

    # Valid statement where opening (1000) - debit (200) + credit (500) = closing (1300)
    mock_payload = {
        "bank_name": "Chase Bank",
        "account_holder": "Jane Doe",
        "account_number_masked": "XXXX-1234",
        "currency": "USD",
        "statement_period": "2026-01-01 to 2026-01-31",
        "opening_balance": 1000.0,
        "closing_balance": 1300.0,
        "transactions": [
            {"date": "2026-01-05", "description": "Grocery Store", "reference": "REF1", "debit": 200.0, "credit": None, "balance": 800.0},
            {"date": "2026-01-10", "description": "Client Payment", "reference": "REF2", "debit": None, "credit": 500.0, "balance": 1300.0},
        ]
    }

    with patch.object(client, "chat_completion", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = json.dumps(mock_payload)

        result, warnings = await extractor.extract("Sample bank statement text", DocumentTypeEnum.BANK_STATEMENT.value)
        assert result is not None
        assert result["bank_name"] == "Chase Bank"
        assert len(result["transactions"]) == 2
        # No arithmetic mismatch warning
        assert not any(w.code == "FINANCIAL_ARITHMETIC_MISMATCH" for w in warnings)


@pytest.mark.asyncio
async def test_bank_statement_arithmetic_mismatch_warning():
    client = DeepSeekClient(api_key="mock_key")
    extractor = StructuredExtractor(client)

    # Statement where math doesn't balance: 1000 - 200 + 0 != 1200
    mock_payload = {
        "bank_name": "Wells Fargo",
        "opening_balance": 1000.0,
        "closing_balance": 1200.0,  # Should be 800
        "transactions": [
            {"date": "2026-01-05", "description": "Utility Bill", "debit": 200.0, "credit": None, "balance": 800.0}
        ]
    }

    with patch.object(client, "chat_completion", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = json.dumps(mock_payload)

        result, warnings = await extractor.extract("Bank statement text", DocumentTypeEnum.BANK_STATEMENT.value)
        assert result is not None
        # Values must NOT be altered
        assert result["closing_balance"] == 1200.0
        # Warning MUST be attached
        assert any(w.code == "FINANCIAL_ARITHMETIC_MISMATCH" for w in warnings)


@pytest.mark.asyncio
async def test_receipt_extraction_and_tax_audit():
    client = DeepSeekClient(api_key="mock_key")
    extractor = StructuredExtractor(client)

    mock_receipt = {
        "merchant": "Starbucks Coffee",
        "receipt_number": "REC-987",
        "date": "2026-02-01",
        "currency": "USD",
        "subtotal": 10.00,
        "tax": 1.00,
        "discount": 0.0,
        "total": 11.00,
        "payment_method": "Credit Card",
        "line_items": [
            {"description": "Latte", "quantity": 2.0, "unit_price": 5.0, "total": 10.0}
        ]
    }

    with patch.object(client, "chat_completion", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = json.dumps(mock_receipt)

        result, warnings = await extractor.extract("Receipt text", DocumentTypeEnum.RECEIPT.value)
        assert result is not None
        assert result["merchant"] == "Starbucks Coffee"
        assert result["total"] == 11.0
        assert not any(w.code == "RECEIPT_TOTAL_MISMATCH" for w in warnings)
