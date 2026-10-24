"""
Reference example data for the e-invoicing endpoints, based on the official ZUGFeRD sample invoice 471102
(Lieferant GmbH → Kunden AG Mitte): 198.00 + 275.00 = 473.00 net, 37.62 + 19.25 = 56.87 VAT, 529.87 gross.
"""

import copy
from typing import Any, Dict

SAMPLE_INVOICE_471102: Dict[str, Any] = {
    "invoice_number": "471102",
    "issue_date": "2018-03-05",
    "type_code": "380",
    "currency": "EUR",
    "delivery_date": "2018-03-05",
    "notes": ["Rechnung gemäß Bestellung vom 01.03.2018."],
    "seller": {
        "id": "549910",
        "global_id": "4000001123452",
        "name": "Lieferant GmbH",
        "address": {"street": "Lieferantenstraße 20", "postcode": "80333", "city": "München", "country": "DE"},
        "tax_number": "201/113/40209",
        "vat_id": "DE123456789",
        "contact": {"name": "Hans Muster", "phone": "+49 89 1234567", "email": "rechnung@lieferant.de"},
        "legal": {"managing_director": "Hans Muster", "commercial_register": "HRB 123456", "register_court": "Amtsgericht München"},
    },
    "buyer": {
        "id": "GE2020211",
        "name": "Kunden AG Mitte",
        "address": {"street": "Kundenstraße 15", "postcode": "69876", "city": "Frankfurt", "country": "DE"},
    },
    "lines": [
        {
            "position": "1",
            "description": "Trennblätter A4",
            "seller_article_number": "TB100A4",
            "gtin": "4012345001235",
            "net_price": 9.90,
            "gross_price": 9.90,
            "quantity": 20,
            "unit": "Stk",
            "vat_category": "S",
            "vat_rate": 19,
            "line_net_amount": 198.00,
        },
        {
            "position": "2",
            "description": "Joghurt Banane",
            "seller_article_number": "ARNR2",
            "gtin": "4000050986428",
            "net_price": 5.50,
            "gross_price": 5.50,
            "quantity": 50,
            "unit": "Stk",
            "vat_category": "S",
            "vat_rate": 7,
            "line_net_amount": 275.00,
        },
    ],
    "vat_breakdown": [
        {"category": "S", "rate": 19, "basis_amount": 198.00, "tax_amount": 37.62},
        {"category": "S", "rate": 7, "basis_amount": 275.00, "tax_amount": 19.25},
    ],
    "totals": {
        "line_total": 473.00,
        "charges": None,
        "allowances": None,
        "net_total": 473.00,
        "vat_total": 56.87,
        "gross_total": 529.87,
        "prepaid_amount": None,
        "payable_amount": 529.87,
    },
    "payment": {
        "means_code": "58",
        "due_date": "2018-04-04",
        "terms": "Zahlbar innerhalb 30 Tagen netto bis 04.04.2018",
        "discount_days": 10,
        "discount_percent": 3,
        "iban": "DE02120300000000202051",
        "bic": "BYLADEM1001",
        "account_name": "Lieferant GmbH",
        "payment_reference": "471102",
    },
}


def sample_xrechnung_invoice() -> Dict[str, Any]:
    """Sample 471102 extended with the fields XRechnung additionally requires (Leitweg-ID, electronic addresses)."""
    data = copy.deepcopy(SAMPLE_INVOICE_471102)
    data["seller"]["electronic_address"] = "rechnung@lieferant.de"
    data["buyer"]["buyer_reference"] = "04011000-1234512345-06"
    data["buyer"]["electronic_address"] = "einkauf@kunden-ag.de"
    return data


SAMPLE_XRECHNUNG_INVOICE = sample_xrechnung_invoice()

SAMPLE_FLAT_INVOICE: Dict[str, Any] = {
    "invoice_number": "471102",
    "issue_date": "2018-03-05",
    "format_type": "ZUGFeRD",
    "buyer_name": "Kunden AG Mitte",
    "buyer_address": "Kundenstraße 15, 69876 Frankfurt, DE",
    "routing_id": None,
    "seller_name": "Lieferant GmbH",
    "seller_address": "Lieferantenstraße 20, 80333 München, DE",
    "seller_vat_id": "DE123456789",
    "line_items": [
        {"item_description": "Trennblätter A4", "net_price": 9.90, "quantity": 20, "unit": "Stk", "tax_rate": 19.0, "line_net_amount": 198.00},
        {"item_description": "Joghurt Banane", "net_price": 5.50, "quantity": 50, "unit": "Stk", "tax_rate": 7.0, "line_net_amount": 275.00},
    ],
    "tax_basis_total": 473.00,
    "tax_total": 56.87,
    "grand_total": 529.87,
    "payment_terms": "Zahlbar innerhalb 30 Tagen netto",
    "delivery_date": "2018-03-05",
}
