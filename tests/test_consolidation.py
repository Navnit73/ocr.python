"""
Tests for Multi-Statement Financial Consolidation.
"""

from app.services.consolidation_service import ConsolidationService


def test_multi_statement_consolidation():
    statement_1 = {
        "bank_name": "SBI",
        "currency": "INR",
        "transactions": [
            {"date": "2026-08-01", "description": "OFFICE RENT", "debit": 15000.00, "credit": None},
            {"date": "2026-08-15", "description": "CLIENT INVOICE 101", "debit": None, "credit": 50000.00},
        ],
    }
    statement_2 = {
        "bank_name": "SBI",
        "currency": "INR",
        "transactions": [
            {"date": "2026-09-01", "description": "OFFICE RENT", "debit": 15000.00, "credit": None},
            {"date": "2026-09-20", "description": "CLIENT INVOICE 102", "debit": None, "credit": 60000.00},
        ],
    }

    res = ConsolidationService.consolidate([statement_1, statement_2])

    assert res.statement_count == 2
    assert res.total_transactions == 4
    assert res.consolidated_inflow == 110000.00
    assert res.consolidated_outflow == 30000.00
    assert res.net_savings == 80000.00
    assert len(res.monthly_trends) == 2

    # Check monthly breakdown
    assert res.monthly_trends[0].month == "2026-08"
    assert res.monthly_trends[0].inflow == 50000.00
    assert res.monthly_trends[1].month == "2026-09"
    assert res.monthly_trends[1].inflow == 60000.00

    # Test Excel generation
    excel_bytes = ConsolidationService.generate_consolidated_excel(res, [statement_1, statement_2])
    assert isinstance(excel_bytes, bytes)
    assert excel_bytes[:2] == b"PK"
