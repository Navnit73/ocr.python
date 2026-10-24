"""
Tests for the e-invoicing endpoints (ZUGFeRD 2.x EN 16931 / XRechnung) using the reference sample invoice 471102.
"""

import base64
import copy
import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path

import facturx
import pymupdf
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from lxml import etree

from app.core.config import get_settings
from app.core.einvoice_config import get_einvoice_settings
from app.main import app
from app.schemas.einvoice import EInvoiceData, EInvoiceProfile
from app.schemas.einvoice_examples import SAMPLE_FLAT_INVOICE, SAMPLE_INVOICE_471102, SAMPLE_XRECHNUNG_INVOICE
from app.services.einvoice.calculator import calculate
from app.services.einvoice.codes import is_valid_iban, leitweg_check_digits_ok
from app.services.einvoice.kosit import parse_kosit_report
from app.services.einvoice.rules import check_business_rules

BASE = "/api/einvoice"
NS = {
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
}


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def api_key():
    return get_settings().api_keys[0]


@pytest.fixture
def headers(api_key):
    return {"X-API-Key": api_key}


@pytest_asyncio.fixture(autouse=True)
async def environment(tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    monkeypatch.setattr(settings, "require_api_key", True)
    einvoice = get_einvoice_settings()
    monkeypatch.setattr(einvoice, "archive_dir", str(tmp_path / "archive"))
    monkeypatch.setattr(einvoice, "archive_enabled", True)
    monkeypatch.setattr(einvoice, "kosit_jar", None)
    monkeypatch.setattr(einvoice, "kosit_scenarios", None)
    monkeypatch.setattr(einvoice, "kosit_required", False)
    monkeypatch.setattr(einvoice, "ghostscript_bin", None)
    monkeypatch.setattr(einvoice, "max_upload_size_mb", 10)
    yield


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=120) as http:
        yield http


def sample(**overrides):
    data = copy.deepcopy(SAMPLE_INVOICE_471102)
    data.update(overrides)
    return data


def make_pdf(text: str = "Rechnung 471102") -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    content = doc.tobytes()
    doc.close()
    return content


async def create_zugferd_pdf(client, headers, data=None) -> bytes:
    response = await client.post(
        f"{BASE}/create-zugferd", headers=headers,
        data={"invoice_data": json.dumps(data or SAMPLE_INVOICE_471102)},
    )
    assert response.status_code == 200, response.text
    return response.content


async def create_xrechnung_xml(client, headers) -> bytes:
    response = await client.post(f"{BASE}/create-xrechnung", headers=headers, json=SAMPLE_XRECHNUNG_INVOICE)
    assert response.status_code == 200, response.text
    return response.content


def error_fields(response) -> dict:
    return {e["field"]: e["rule_id"] for e in response.json()["error"]["errors"]}


# ---------------------------------------------------------------------------
# Calculation & rules (unit level)
# ---------------------------------------------------------------------------

def test_sample_471102_totals_are_recalculated():
    calc = calculate(EInvoiceData.model_validate(SAMPLE_INVOICE_471102))
    assert calc.issues == []
    assert calc.totals.line_total == Decimal("473.00")
    assert calc.totals.net_total == Decimal("473.00")
    assert calc.totals.vat_total == Decimal("56.87")
    assert calc.totals.gross_total == Decimal("529.87")
    assert calc.totals.payable_amount == Decimal("529.87")
    breakdown = {(b.category.value, b.rate): (b.basis_amount, b.tax_amount) for b in calc.vat_breakdown}
    assert breakdown == {
        ("S", Decimal("19")): (Decimal("198.00"), Decimal("37.62")),
        ("S", Decimal("7")): (Decimal("275.00"), Decimal("19.25")),
    }


def test_totals_and_breakdown_are_optional_and_computed():
    data = sample(totals=None, vat_breakdown=None)
    for line in data["lines"]:
        line.pop("line_net_amount")
    calc = calculate(EInvoiceData.model_validate(data))
    assert calc.issues == []
    assert calc.totals.gross_total == Decimal("529.87")


def test_document_allowance_and_charge_feed_into_vat_basis():
    data = sample(totals=None, vat_breakdown=None)
    data["allowances"] = [{"amount": 10, "reason": "Rabatt", "vat_category": "S", "vat_rate": 19}]
    data["charges"] = [{"amount": 5, "reason": "Fracht", "vat_category": "S", "vat_rate": 19}]
    calc = calculate(EInvoiceData.model_validate(data))
    assert calc.totals.net_total == Decimal("468.00")
    nineteen = next(b for b in calc.vat_breakdown if b.rate == 19)
    assert nineteen.basis_amount == Decimal("193.00")
    assert nineteen.tax_amount == Decimal("36.67")


def test_mismatched_totals_report_rule_ids():
    data = sample()
    data["totals"]["net_total"] = 470.00
    data["totals"]["gross_total"] = 530.00
    _, errors, _ = check_business_rules(EInvoiceData.model_validate(data), EInvoiceProfile.ZUGFERD_EN16931)
    rules = {(e.rule_id, e.field) for e in errors}
    assert ("BR-CO-13", "totals.net_total") in rules
    assert ("BR-CO-15", "totals.gross_total") in rules


def test_xrechnung_rules_require_leitweg_contact_and_payment():
    data = sample(payment=None)
    data["seller"]["contact"] = None
    _, errors, _ = check_business_rules(EInvoiceData.model_validate(data), EInvoiceProfile.XRECHNUNG)
    rules = {e.rule_id for e in errors}
    assert {"BR-DE-15", "BR-DE-2", "BR-DE-1", "PEPPOL-EN16931-R020", "PEPPOL-EN16931-R010"} <= rules


def test_identifier_checksums():
    assert is_valid_iban("DE02 1203 0000 0000 2020 51")
    assert not is_valid_iban("DE02120300000000202052")
    assert leitweg_check_digits_ok("04011000-1234512345-06")
    assert leitweg_check_digits_ok("991-33333TEST-33")
    assert not leitweg_check_digits_ok("04011000-1234512345-07")


def test_kosit_report_parsing():
    report = b"""<?xml version="1.0"?>
    <rep:report xmlns:rep="http://www.xoev.de/de/validator/varl/1" valid="false">
      <rep:scenarioMatched><rep:validationStepResult>
        <rep:message id="1" level="error" code="BR-DE-15" xpathLocation="/rsm:CrossIndustryInvoice" lineNumber="3">
          [BR-DE-15] Das Element Buyer reference (BT-10) muss uebermittelt werden.</rep:message>
        <rep:message id="2" level="warning" code="PEPPOL-EN16931-R001" xpathLocation="/x">Hinweis</rep:message>
      </rep:validationStepResult></rep:scenarioMatched>
      <rep:assessment><rep:reject/></rep:assessment>
    </rep:report>"""
    result = parse_kosit_report(report)
    assert result.status == "failed"
    assert result.errors[0].rule_id == "BR-DE-15" and result.errors[0].line == 3
    assert result.warnings[0].rule_id == "PEPPOL-EN16931-R001"


# ---------------------------------------------------------------------------
# POST /create-zugferd
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_zugferd_without_pdf_renders_and_embeds(client, headers, tmp_path):
    response = await client.post(f"{BASE}/create-zugferd", headers=headers, data={"invoice_data": json.dumps(SAMPLE_INVOICE_471102)})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["x-validation-valid"] == "true"
    assert response.headers["x-einvoice-profile"] == "EN 16931 (COMFORT)"
    pdf = response.content
    assert response.headers["x-content-sha256"] == hashlib.sha256(pdf).hexdigest()

    filename, xml = facturx.get_xml_from_pdf(pdf, check_xsd=True)
    assert filename == "factur-x.xml"
    root = etree.fromstring(xml)
    assert root.findtext(".//rsm:ExchangedDocumentContext/ram:GuidelineSpecifiedDocumentContextParameter/ram:ID", namespaces=NS) == "urn:cen.eu:en16931:2017"
    assert root.findtext(".//rsm:ExchangedDocument/ram:ID", namespaces=NS) == "471102"
    assert root.findtext(".//ram:GrandTotalAmount", namespaces=NS) == "529.87"
    assert root.findtext(".//ram:TaxTotalAmount", namespaces=NS) == "56.87"
    assert root.find(".//ram:BilledQuantity", namespaces=NS).get("unitCode") == "C62"

    # Generated PDF is archived byte-for-byte
    archive_id = response.headers["x-archive-id"]
    stored = list(Path(tmp_path / "archive").rglob(f"{archive_id}/*generated_pdf*"))
    assert len(stored) == 1 and stored[0].read_bytes() == pdf
    assert not os.access(stored[0], os.W_OK)


@pytest.mark.asyncio
async def test_create_zugferd_with_uploaded_pdf_json_output(client, headers, tmp_path):
    original = make_pdf()
    response = await client.post(
        f"{BASE}/create-zugferd", headers=headers, params={"output": "json"},
        data={"invoice_data": json.dumps(SAMPLE_INVOICE_471102)},
        files={"file": ("rechnung.pdf", original, "application/pdf")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["validation"]["valid"] is True
    assert body["validation"]["checks"]["xsd"] == "passed"
    assert body["validation"]["checks"]["pdf"] == "passed"
    assert any(w["rule_id"] == "PDFA-SOURCE" for w in body["validation"]["warnings"])
    assert "<ram:BuyerReference>" not in body["xml"]
    pdf = base64.b64decode(body["pdf_base64"])
    assert facturx.get_xml_from_pdf(pdf, check_xsd=False)[0] == "factur-x.xml"

    roles = {f["role"]: f for f in body["archive"]["files"]}
    assert set(roles) == {"input_json", "original_pdf", "generated_pdf"}
    assert roles["original_pdf"]["sha256"] == hashlib.sha256(original).hexdigest()


@pytest.mark.asyncio
async def test_create_zugferd_rejects_mismatched_totals(client, headers):
    data = sample()
    data["totals"]["vat_total"] = 56.00
    response = await client.post(f"{BASE}/create-zugferd", headers=headers, data={"invoice_data": json.dumps(data)})
    assert response.status_code == 422
    fields = error_fields(response)
    assert fields["totals.vat_total"] == "BR-CO-14"


@pytest.mark.asyncio
async def test_create_zugferd_rejects_wrong_line_amount(client, headers):
    data = sample()
    data["lines"][0]["line_net_amount"] = 199.00
    response = await client.post(f"{BASE}/create-zugferd", headers=headers, data={"invoice_data": json.dumps(data)})
    assert response.status_code == 422
    assert error_fields(response)["lines[0].line_net_amount"] == "CALC-LINE-NET"


@pytest.mark.asyncio
async def test_create_zugferd_field_level_input_errors(client, headers):
    data = sample()
    data["seller"]["address"]["country"] = "XX"
    data["payment"]["iban"] = "DE02120300000000202052"
    data["lines"][1]["vat_rate"] = 0
    data["currency"] = "EURO"
    del data["invoice_number"]
    response = await client.post(f"{BASE}/create-zugferd", headers=headers, data={"invoice_data": json.dumps(data)})
    assert response.status_code == 422
    fields = error_fields(response)
    assert fields["invoice_data.invoice_number"] == "BR-02"
    assert fields["invoice_data.seller.address.country"] == "BR-CL-14"
    assert fields["invoice_data.payment.iban"] == "BR-DE-19"
    assert fields["invoice_data.lines[1]"] == "BR-S-05"
    assert fields["invoice_data.currency"] == "BR-CL-04"


@pytest.mark.asyncio
async def test_create_zugferd_invalid_json(client, headers):
    response = await client.post(f"{BASE}/create-zugferd", headers=headers, data={"invoice_data": "{not json"})
    assert response.status_code == 422
    assert error_fields(response)["invoice_data"] == "JSON"


@pytest.mark.asyncio
async def test_create_zugferd_rejects_non_pdf_upload(client, headers):
    response = await client.post(
        f"{BASE}/create-zugferd", headers=headers, data={"invoice_data": json.dumps(SAMPLE_INVOICE_471102)},
        files={"file": ("rechnung.pdf", b"\x89PNG\r\n\x1a\nfake", "application/pdf")},
    )
    assert response.status_code == 415


@pytest.mark.asyncio
async def test_create_zugferd_rejects_mime_mismatch(client, headers):
    response = await client.post(
        f"{BASE}/create-zugferd", headers=headers, data={"invoice_data": json.dumps(SAMPLE_INVOICE_471102)},
        files={"file": ("rechnung.pdf", make_pdf(), "image/png")},
    )
    assert response.status_code == 415


@pytest.mark.asyncio
async def test_create_zugferd_rejects_oversized_upload(client, headers, monkeypatch):
    monkeypatch.setattr(get_einvoice_settings(), "max_upload_size_mb", 1)
    big = b"%PDF-1.7\n" + b"0" * (1024 * 1024 + 10)
    response = await client.post(
        f"{BASE}/create-zugferd", headers=headers, data={"invoice_data": json.dumps(SAMPLE_INVOICE_471102)},
        files={"file": ("big.pdf", big, "application/pdf")},
    )
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_create_zugferd_requires_api_key(client):
    response = await client.post(f"{BASE}/create-zugferd", data={"invoice_data": json.dumps(SAMPLE_INVOICE_471102)})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /create-xrechnung
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_xrechnung_returns_xml(client, headers):
    response = await client.post(f"{BASE}/create-xrechnung", headers=headers, json=SAMPLE_XRECHNUNG_INVOICE)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/xml")
    assert response.headers["x-einvoice-profile"] == "XRECHNUNG"
    root = etree.fromstring(response.content)
    guideline = root.findtext(".//ram:GuidelineSpecifiedDocumentContextParameter/ram:ID", namespaces=NS)
    assert guideline == "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0"
    assert root.findtext(".//ram:BuyerReference", namespaces=NS) == "04011000-1234512345-06"
    terms = root.findtext(".//ram:SpecifiedTradePaymentTerms/ram:Description", namespaces=NS)
    assert terms.startswith("#SKONTO#TAGE=10#PROZENT=3.00#\n")
    assert root.findtext(".//ram:PayeePartyCreditorFinancialAccount/ram:IBANID", namespaces=NS) == "DE02120300000000202051"


@pytest.mark.asyncio
async def test_create_xrechnung_json_output_reports_kosit_skipped(client, headers):
    response = await client.post(f"{BASE}/create-xrechnung", headers=headers, params={"output": "json"}, json=SAMPLE_XRECHNUNG_INVOICE)
    assert response.status_code == 200, response.text
    validation = response.json()["validation"]
    assert validation["valid"] is True
    assert validation["profile"] == "XRECHNUNG"
    assert validation["checks"]["kosit"] == "skipped"
    assert any(w["rule_id"] == "KOSIT" for w in validation["warnings"])


@pytest.mark.asyncio
async def test_create_xrechnung_fails_when_kosit_required_but_missing(client, headers, monkeypatch):
    monkeypatch.setattr(get_einvoice_settings(), "kosit_required", True)
    response = await client.post(f"{BASE}/create-xrechnung", headers=headers, json=SAMPLE_XRECHNUNG_INVOICE)
    assert response.status_code == 422
    assert "KOSIT" in error_fields(response).values()


@pytest.mark.asyncio
async def test_create_xrechnung_requires_leitweg_id(client, headers):
    data = copy.deepcopy(SAMPLE_XRECHNUNG_INVOICE)
    data["buyer"]["buyer_reference"] = None
    response = await client.post(f"{BASE}/create-xrechnung", headers=headers, json=data)
    assert response.status_code == 422
    assert error_fields(response)["buyer.buyer_reference"] == "BR-DE-15"


@pytest.mark.asyncio
async def test_create_xrechnung_rejects_bad_leitweg_check_digits(client, headers):
    data = copy.deepcopy(SAMPLE_XRECHNUNG_INVOICE)
    data["buyer"]["buyer_reference"] = "04011000-1234512345-07"
    response = await client.post(f"{BASE}/create-xrechnung", headers=headers, json=data)
    assert response.status_code == 422
    assert error_fields(response)["buyer.buyer_reference"] == "LEITWEG-ID"


@pytest.mark.asyncio
async def test_create_xrechnung_schema_errors_are_422(client, headers):
    data = copy.deepcopy(SAMPLE_XRECHNUNG_INVOICE)
    data["lines"] = []
    response = await client.post(f"{BASE}/create-xrechnung", headers=headers, json=data)
    assert response.status_code == 422
    assert any(d["loc"][-1] == "lines" for d in response.json()["details"])


# ---------------------------------------------------------------------------
# POST /extract
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extract_from_zugferd_pdf_round_trips(client, headers):
    pdf = await create_zugferd_pdf(client, headers)
    response = await client.post(f"{BASE}/extract", headers=headers, files={"file": ("471102.pdf", pdf, "application/pdf")})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source_type"] == "pdf"
    assert body["profile"] == "EN 16931 (COMFORT)"
    assert body["profile_urn"] == "urn:cen.eu:en16931:2017"
    assert body["embedded_filename"] == "factur-x.xml"
    assert body["version"].startswith("ZUGFeRD 2.1+ / Factur-X 1.0")
    assert body["xmp"]["pdfa_part"] == "3"
    assert body["xmp"]["conformance_level"] == "EN 16931"
    assert "<rsm:CrossIndustryInvoice" in body["raw_xml"]
    invoice = body["invoice"]
    assert invoice["invoice_number"] == "471102"
    assert invoice["totals"]["gross_total"] == 529.87
    assert invoice["seller"]["vat_id"] == "DE123456789"
    assert invoice["seller"]["tax_number"] == "201/113/40209"
    assert invoice["lines"][1]["gtin"] == "4000050986428"
    assert invoice["payment"]["discount_percent"] == 3.0
    assert body["archive"]["files"][0]["role"] == "uploaded_document"

    # The extracted JSON is accepted again by the create endpoint
    again = await client.post(f"{BASE}/create-zugferd", headers=headers, data={"invoice_data": json.dumps(invoice)})
    assert again.status_code == 200, again.text


@pytest.mark.asyncio
async def test_extract_from_xrechnung_xml(client, headers):
    xml = await create_xrechnung_xml(client, headers)
    response = await client.post(f"{BASE}/extract", headers=headers, files={"file": ("471102.xml", xml, "application/xml")})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["syntax"] == "CII"
    assert body["profile"] == "XRECHNUNG"
    assert body["version"] == "XRechnung 3.0 (CII)"
    assert body["invoice"]["buyer"]["buyer_reference"] == "04011000-1234512345-06"


@pytest.mark.asyncio
async def test_extract_plain_pdf_without_xml_is_422(client, headers):
    response = await client.post(f"{BASE}/extract", headers=headers, files={"file": ("plain.pdf", make_pdf(), "application/pdf")})
    assert response.status_code == 422
    assert response.json()["error"]["errors"][0]["rule_id"] == "PDF-NO-XML"


# ---------------------------------------------------------------------------
# POST /validate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_validate_valid_zugferd_pdf(client, headers):
    pdf = await create_zugferd_pdf(client, headers)
    response = await client.post(f"{BASE}/validate", headers=headers, files={"file": ("471102.pdf", pdf, "application/pdf")})
    assert response.status_code == 200
    report = response.json()
    assert report["valid"] is True, report["errors"]
    assert report["errors"] == []
    assert report["checks"] == {"xml": "passed", "xsd": "passed", "business_rules": "passed", "pdf": "passed"}


@pytest.mark.asyncio
async def test_validate_detects_tampered_totals(client, headers):
    xml = (await create_xrechnung_xml(client, headers)).decode()
    tampered = xml.replace("<ram:GrandTotalAmount>529.87</ram:GrandTotalAmount>", "<ram:GrandTotalAmount>600.00</ram:GrandTotalAmount>")
    response = await client.post(f"{BASE}/validate", headers=headers, files={"file": ("x.xml", tampered.encode(), "text/xml")})
    assert response.status_code == 200
    report = response.json()
    assert report["valid"] is False
    rules = {(e["rule_id"], e["field"]) for e in report["errors"]}
    assert ("BR-CO-15", "totals.gross_total") in rules


@pytest.mark.asyncio
async def test_validate_reports_xsd_errors(client, headers):
    xml = (await create_xrechnung_xml(client, headers)).decode()
    broken = xml.replace("<ram:TypeCode>380</ram:TypeCode>", "<ram:Bogus>1</ram:Bogus><ram:TypeCode>380</ram:TypeCode>", 1)
    response = await client.post(f"{BASE}/validate", headers=headers, files={"file": ("x.xml", broken.encode(), "application/xml")})
    report = response.json()
    assert report["valid"] is False
    assert report["checks"]["xsd"] == "failed"
    assert any(e["rule_id"] == "XSD" and e["line"] for e in report["errors"])


@pytest.mark.asyncio
async def test_validate_rejects_minimum_profile(client, headers):
    xml = (await create_xrechnung_xml(client, headers)).decode()
    minimum = xml.replace("urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0", "urn:factur-x.eu:1p0:minimum")
    response = await client.post(f"{BASE}/validate", headers=headers, files={"file": ("x.xml", minimum.encode(), "application/xml")})
    report = response.json()
    assert report["valid"] is False
    assert report["profile"] == "MINIMUM"
    assert any(e["rule_id"] == "PROFILE-NOT-EN16931" for e in report["errors"])


@pytest.mark.asyncio
async def test_validate_blocks_xxe(client, headers):
    xxe = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>'
    response = await client.post(f"{BASE}/validate", headers=headers, files={"file": ("x.xml", xxe, "application/xml")})
    assert response.status_code == 200
    report = response.json()
    assert report["valid"] is False
    assert report["errors"][0]["rule_id"] == "XML-WELLFORMED"
    assert "root:" not in json.dumps(report)


@pytest.mark.asyncio
async def test_validate_rejects_unsupported_file(client, headers):
    response = await client.post(f"{BASE}/validate", headers=headers, files={"file": ("x.txt", b"hello", "text/plain")})
    assert response.status_code == 415


# ---------------------------------------------------------------------------
# POST /convert (flat form) and /api/v1/convert alias
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("path", [f"{BASE}/convert", "/api/v1/convert"])
async def test_convert_flat_form_to_zugferd(client, headers, path):
    response = await client.post(
        path, headers=headers, data={"invoice_data": json.dumps(SAMPLE_FLAT_INVOICE)},
        files={"file": ("rechnung.pdf", make_pdf(), "application/pdf")},
    )
    assert response.status_code == 200, response.text
    _, xml = facturx.get_xml_from_pdf(response.content, check_xsd=True)
    assert b"<ram:GrandTotalAmount>529.87</ram:GrandTotalAmount>" in xml


@pytest.mark.asyncio
async def test_convert_flat_form_to_xrechnung(client, headers):
    flat = {
        **SAMPLE_FLAT_INVOICE,
        "format_type": "XRechnung",
        "routing_id": "04011000-1234512345-06",
        "seller_contact_name": "Hans Muster",
        "seller_phone": "+49 89 1234567",
        "seller_email": "rechnung@lieferant.de",
        "buyer_email": "einkauf@kunden-ag.de",
        "iban": "DE02120300000000202051",
        "due_date": "2018-04-04",
    }
    response = await client.post(f"{BASE}/convert", headers=headers, data={"invoice_data": json.dumps(flat)})
    assert response.status_code == 200, response.text
    assert b"xrechnung_3.0" in response.content


@pytest.mark.asyncio
async def test_convert_maps_errors_to_flat_fields(client, headers):
    flat = {**SAMPLE_FLAT_INVOICE, "tax_total": 50.00, "format_type": "XRechnung", "routing_id": None}
    response = await client.post(f"{BASE}/convert", headers=headers, data={"invoice_data": json.dumps(flat)})
    assert response.status_code == 422
    assert "routing_id" in json.dumps(response.json())

    flat = {**SAMPLE_FLAT_INVOICE, "tax_total": 50.00}
    response = await client.post(f"{BASE}/convert", headers=headers, data={"invoice_data": json.dumps(flat)})
    assert response.status_code == 422
    assert error_fields(response)["invoice_data.tax_total"] == "BR-CO-14"

    flat = {**SAMPLE_FLAT_INVOICE, "seller_address": "Nowhere"}
    response = await client.post(f"{BASE}/convert", headers=headers, data={"invoice_data": json.dumps(flat)})
    assert response.status_code == 422
    assert error_fields(response)["invoice_data.seller_address"] == "ADDRESS"


# ---------------------------------------------------------------------------
# Archive verification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_archive_verification_detects_tampering(client, headers, api_key, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "api_keys", [api_key, "einvoice_second_test_key"])
    response = await client.post(f"{BASE}/create-zugferd", headers=headers, data={"invoice_data": json.dumps(SAMPLE_INVOICE_471102)})
    archive_id = response.headers["x-archive-id"]

    verify = await client.get(f"{BASE}/archive/{archive_id}", headers=headers)
    assert verify.status_code == 200
    assert verify.json()["intact"] is True
    assert verify.json()["invoice_number"] == "471102"

    # A different API key cannot see the record
    other = await client.get(f"{BASE}/archive/{archive_id}", headers={"X-API-Key": "einvoice_second_test_key"})
    assert other.status_code == 404

    stored = next(Path(tmp_path / "archive").rglob(f"{archive_id}/*generated_pdf*"))
    os.chmod(stored, 0o644)
    stored.write_bytes(stored.read_bytes() + b"%tampered")
    verify = await client.get(f"{BASE}/archive/{archive_id}", headers=headers)
    assert verify.json()["intact"] is False
    assert any("Hash mismatch" in p for p in verify.json()["problems"])


@pytest.mark.asyncio
async def test_archive_ledger_is_hash_chained(client, headers, tmp_path):
    for _ in range(3):
        await create_zugferd_pdf(client, headers)
    lines = [json.loads(line) for line in (tmp_path / "archive" / "ledger.jsonl").read_text().splitlines()]
    assert len(lines) == 3
    assert lines[0]["prev_hash"] == "0" * 64
    assert lines[1]["prev_hash"] == lines[0]["entry_hash"]
    assert lines[2]["prev_hash"] == lines[1]["entry_hash"]


# ---------------------------------------------------------------------------
# Regression tests for audit findings
# ---------------------------------------------------------------------------

def _rules(data, profile=EInvoiceProfile.ZUGFERD_EN16931):
    _, errors, warnings = check_business_rules(EInvoiceData.model_validate(data), profile)
    return {e.rule_id for e in errors}, {w.rule_id for w in warnings}


def test_binary_float_artifacts_are_snapped_to_cents():
    data = sample(totals=None, vat_breakdown=None)
    data["lines"][0].update(quantity=3, net_price=9.9, gross_price=9.9, line_net_amount=9.9 * 3)  # 29.700000000000003
    invoice = EInvoiceData.model_validate(data)
    assert invoice.lines[0].line_net_amount == Decimal("29.70")
    assert calculate(invoice).issues == []


def test_line_allowances_reduce_line_net_and_round_trip():
    data = sample(totals=None, vat_breakdown=None)
    data["lines"][0]["allowances"] = [{"amount": 18.00, "reason": "Mengenrabatt", "reason_code": "95"}]
    data["lines"][0]["line_net_amount"] = 180.00
    calc = calculate(EInvoiceData.model_validate(data))
    assert calc.issues == []
    assert calc.totals.net_total == Decimal("455.00")


@pytest.mark.asyncio
async def test_line_allowance_survives_extract(client, headers):
    data = sample(totals=None, vat_breakdown=None)
    data["lines"][0]["allowances"] = [{"amount": 18.00, "reason": "Mengenrabatt"}]
    data["lines"][0]["line_net_amount"] = None
    pdf = await create_zugferd_pdf(client, headers, data)
    body = (await client.post(f"{BASE}/extract", headers=headers, files={"file": ("a.pdf", pdf, "application/pdf")})).json()
    line = body["invoice"]["lines"][0]
    assert line["line_net_amount"] == 180.0
    assert line["allowances"][0]["amount"] == 18.0
    assert body["warnings"] == []


@pytest.mark.parametrize("urn, profile", [
    ("urn:cen.eu:en16931:2017#compliant#urn:zugferd.de:2p0:basic", "BASIC"),
    ("urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0", "PEPPOL BIS 3.0"),
    ("urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0#conformant#urn:xeinkauf.de:kosit:extension:xrechnung_3.0", "XRECHNUNG"),
])
def test_profile_detection_for_other_en16931_specifications(urn, profile):
    from app.services.einvoice.codes import detect_profile
    detected, version = detect_profile(urn)
    assert detected == profile
    if profile == "XRECHNUNG":
        assert version == "3.0"


@pytest.mark.asyncio
async def test_extract_returns_data_for_minimum_profile(client, headers):
    xml = (await create_xrechnung_xml(client, headers)).decode()
    minimum = xml.replace("urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0", "urn:factur-x.eu:1p0:minimum")
    response = await client.post(f"{BASE}/extract", headers=headers, files={"file": ("m.xml", minimum.encode(), "application/xml")})
    assert response.status_code == 200
    assert response.json()["invoice"]["invoice_number"] == "471102"


def test_ubl_sepa_creditor_id_maps_to_payment():
    from app.services.einvoice.xml_parser import detect_document, parse_invoice, parse_xml_secure
    ubl = b"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
 xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
 xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
 <cbc:CustomizationID>urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0</cbc:CustomizationID>
 <cbc:ID>U-1</cbc:ID>
 <cac:AccountingSupplierParty><cac:Party>
  <cac:PartyIdentification><cbc:ID schemeID="SEPA">DE98ZZZ09999999999</cbc:ID></cac:PartyIdentification>
  <cac:PartyIdentification><cbc:ID>549910</cbc:ID></cac:PartyIdentification>
 </cac:Party></cac:AccountingSupplierParty>
 <cac:PaymentMeans><cbc:PaymentMeansCode>59</cbc:PaymentMeansCode>
  <cac:PaymentMandate><cbc:ID>M-1</cbc:ID></cac:PaymentMandate></cac:PaymentMeans>
</Invoice>"""
    root = parse_xml_secure(ubl)
    data = parse_invoice(root, detect_document(root))
    assert data["payment"]["creditor_id"] == "DE98ZZZ09999999999"
    assert data["seller"]["id"] == "549910"


def test_real_unit_codes_are_not_rewritten():
    from app.services.einvoice.codes import normalize_unit
    assert [normalize_unit(u) for u in ("ST", "PT", "EA", "H87", "Stk", "STK", "Std", "kg")] == ["ST", "PT", "EA", "H87", "C62", "C62", "HUR", "KGM"]


def test_new_business_rules():
    # BR-IC-12: intra-community supply needs the deliver-to address
    data = sample(totals=None, vat_breakdown=[{"category": "K", "rate": 0, "basis_amount": 473, "tax_amount": 0, "exemption_reason": "IG-Lieferung"}])
    for line in data["lines"]:
        line.update(vat_category="K", vat_rate=0, line_net_amount=None)
    data["buyer"].update(vat_id="FR61954506077")
    assert "BR-IC-12" in _rules(data)[0]
    data["delivery_address"] = {"street": "1 Rue de Rivoli", "postcode": "75001", "city": "Paris", "country": "FR"}
    assert "BR-IC-12" not in _rules(data)[0]

    # BR-CO-26: seller must be identifiable
    data = sample()
    data["seller"].update(vat_id=None, id=None, global_id=None, legal=None)
    assert "BR-CO-26" in _rules(data)[0]

    # BR-61: credit transfer without IBAN; without explicit code the default is 1 (not defined)
    data = sample()
    data["payment"].pop("iban")
    data["payment"].pop("bic")
    assert "BR-61" in _rules(data)[0]
    data["payment"].pop("means_code")
    assert EInvoiceData.model_validate(data).payment.means_code == "1"
    assert "BR-61" not in _rules(data)[0]

    # BR-O-02: category O must not carry VAT IDs
    data = sample(totals=None, vat_breakdown=[{"category": "O", "rate": 0, "basis_amount": 473, "tax_amount": 0, "exemption_reason": "Nicht steuerbar"}])
    for line in data["lines"]:
        line.update(vat_category="O", vat_rate=0, line_net_amount=None)
    assert "BR-O-02" in _rules(data)[0]

    # BR-DE-17: type codes outside the XRechnung list; fine for ZUGFeRD
    data = copy.deepcopy(SAMPLE_XRECHNUNG_INVOICE)
    data["type_code"] = "383"
    assert "BR-DE-17" in _rules(data, EInvoiceProfile.XRECHNUNG)[0]
    assert "BR-DE-17" not in _rules(data)[0]


def test_allowance_percentage_must_match_amount():
    data = copy.deepcopy(SAMPLE_XRECHNUNG_INVOICE)
    data.update(totals=None, vat_breakdown=None)
    data["lines"][0].update(line_net_amount=None, allowances=[{"amount": 18.00, "reason": "Rabatt", "percent": 10, "base_amount": 198.00}])
    assert "PEPPOL-EN16931-R040" in _rules(data, EInvoiceProfile.XRECHNUNG)[0]
    assert "PEPPOL-EN16931-R040" in _rules(data)[1]  # warning only for ZUGFeRD
    data["lines"][0]["allowances"][0]["amount"] = 19.80
    assert "PEPPOL-EN16931-R040" not in _rules(data, EInvoiceProfile.XRECHNUNG)[0]


def test_allowance_category_rate_consistency():
    data = sample(totals=None, vat_breakdown=None)
    data["allowances"] = [{"amount": 10, "reason": "Rabatt", "vat_category": "S", "vat_rate": 0}]
    with pytest.raises(Exception, match="BR-S-06"):
        EInvoiceData.model_validate(data)


def test_address_completeness_depends_on_country():
    data = sample()
    data["buyer"]["address"] = {"country": "AT", "city": "Wien", "postcode": "1010"}
    errors, warnings = _rules(data)
    assert "DE-UStG-14-ADDRESS" in warnings and "DE-UStG-14-ADDRESS" not in errors
    data["buyer"]["address"] = {"country": "DE", "city": "Frankfurt", "postcode": "69876"}
    assert "DE-UStG-14-ADDRESS" in _rules(data)[0]
    data = copy.deepcopy(SAMPLE_XRECHNUNG_INVOICE)
    data["buyer"]["address"] = {"street": "Kundenstraße 15", "country": "DE"}
    assert {"BR-DE-8", "BR-DE-9"} <= _rules(data, EInvoiceProfile.XRECHNUNG)[0]


def test_legal_info_is_not_duplicated_on_round_trip():
    from app.schemas.einvoice import LegalInfo
    original = LegalInfo(managing_director="Hans Muster", commercial_register="HRB 123456", register_court="Amtsgericht München")
    round_tripped = LegalInfo(commercial_register="HRB 123456", additional_info=original.as_text())
    assert round_tripped.as_text() == original.as_text()


def test_skonto_parsing_keeps_additional_segments():
    from app.services.einvoice.xml_parser import split_payment_terms
    parsed = split_payment_terms("#SKONTO#TAGE=10#PROZENT=3.00#\n#SKONTO#TAGE=20#PROZENT=1.00#\nZahlbar in 30 Tagen\n")
    assert parsed["discount_days"] == 10
    assert parsed["terms"] == "#SKONTO#TAGE=20#PROZENT=1.00#\nZahlbar in 30 Tagen"


def test_kosit_no_matching_scenario_is_skipped_not_failed():
    report = b"""<rep:report xmlns:rep="http://www.xoev.de/de/validator/varl/1" valid="false">
      <rep:noScenarioMatched/><rep:assessment><rep:reject/></rep:assessment></rep:report>"""
    assert parse_kosit_report(report).status == "skipped"


def test_raw_xml_uses_declared_encoding():
    from app.api.einvoice.router import decode_xml_text
    xml = '<?xml version="1.0" encoding="ISO-8859-1"?><a>München</a>'.encode("iso-8859-1")
    assert "München" in decode_xml_text(xml)


@pytest.mark.asyncio
async def test_invoice_json_as_file_part_for_large_invoices(client, headers):
    data = sample(totals=None, vat_breakdown=None)
    data["lines"] = [dict(SAMPLE_INVOICE_471102["lines"][0], position=str(i), line_net_amount=None, item_description="x" * 400) for i in range(3000)]
    payload = json.dumps(data).encode()
    assert len(payload) > 1024 * 1024  # above Starlette's 1 MB text-field limit
    response = await client.post(
        f"{BASE}/create-zugferd", headers=headers, params={"output": "json"},
        files={"invoice_file": ("invoice.json", payload, "application/json")},
    )
    assert response.status_code == 200, response.text[:300]

    missing = await client.post(f"{BASE}/create-zugferd", headers=headers, files={"file": ("a.pdf", make_pdf(), "application/pdf")})
    assert missing.status_code == 422


@pytest.mark.asyncio
async def test_content_length_cap_rejects_before_parsing(client, headers, monkeypatch):
    monkeypatch.setattr(get_einvoice_settings(), "max_upload_size_mb", 1)
    response = await client.post(f"{BASE}/validate", headers={**headers, "Content-Length": str(50 * 1024 * 1024)}, content=b"x")
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_archive_detects_deleted_record_and_damaged_ledger(client, headers, tmp_path):
    import shutil
    response = await client.post(f"{BASE}/create-zugferd", headers=headers, data={"invoice_data": json.dumps(SAMPLE_INVOICE_471102)})
    archive_id = response.headers["x-archive-id"]
    record_dir = next(Path(tmp_path / "archive").rglob(archive_id))
    for item in record_dir.iterdir():
        os.chmod(item, 0o644)
    shutil.rmtree(record_dir)
    verify = (await client.get(f"{BASE}/archive/{archive_id}", headers=headers)).json()
    assert verify["intact"] is False and "deleted" in verify["problems"][0]

    ledger = tmp_path / "archive" / "ledger.jsonl"
    ledger.write_bytes(ledger.read_bytes() + b'{"broken": tru')
    response = await client.post(f"{BASE}/create-zugferd", headers=headers, data={"invoice_data": json.dumps(SAMPLE_INVOICE_471102)})
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_convert_routing_id_error_points_at_field(client, headers):
    flat = {**SAMPLE_FLAT_INVOICE, "format_type": "XRechnung", "routing_id": None}
    response = await client.post(f"{BASE}/convert", headers=headers, data={"invoice_data": json.dumps(flat)})
    assert response.status_code == 422
    assert "invoice_data.routing_id" in error_fields(response)


def test_openapi_documents_einvoice_endpoints():
    paths = app.openapi()["paths"]
    for path in ("create-zugferd", "create-xrechnung", "extract", "validate", "convert"):
        assert f"{BASE}/{path}" in paths
    assert "/api/v1/convert" in paths


# ---------------------------------------------------------------------------
# Optional: real KoSIT validator (set KOSIT_TEST_JAR, KOSIT_TEST_SCENARIOS, KOSIT_TEST_REPOSITORY[, KOSIT_TEST_JAVA])
# ---------------------------------------------------------------------------

KOSIT_ENV = {key: os.environ.get(f"KOSIT_TEST_{key.upper()}") for key in ("jar", "scenarios", "repository", "java")}


@pytest.mark.asyncio
@pytest.mark.skipif(not (KOSIT_ENV["jar"] and KOSIT_ENV["scenarios"]), reason="KoSIT validator not available")
async def test_real_kosit_accepts_generated_invoices_and_rejects_broken_ones(client, headers, monkeypatch):
    settings = get_einvoice_settings()
    monkeypatch.setattr(settings, "kosit_jar", KOSIT_ENV["jar"])
    monkeypatch.setattr(settings, "kosit_scenarios", KOSIT_ENV["scenarios"])
    monkeypatch.setattr(settings, "kosit_repository", KOSIT_ENV["repository"])
    monkeypatch.setattr(settings, "java_bin", KOSIT_ENV["java"] or "java")
    monkeypatch.setattr(settings, "kosit_required", True)

    response = await client.post(f"{BASE}/create-xrechnung", headers=headers, params={"output": "json"}, json=SAMPLE_XRECHNUNG_INVOICE)
    assert response.status_code == 200, response.text
    assert response.json()["validation"]["checks"]["kosit"] == "passed"

    response = await client.post(
        f"{BASE}/create-zugferd", headers=headers, params={"output": "json"}, data={"invoice_data": json.dumps(SAMPLE_INVOICE_471102)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["validation"]["checks"]["kosit"] == "passed"

    broken = SAMPLE_XRECHNUNG_INVOICE["buyer"]["buyer_reference"]
    xr = (await create_xrechnung_xml(client, headers)).decode().replace(f"<ram:BuyerReference>{broken}</ram:BuyerReference>", "")
    report = (await client.post(f"{BASE}/validate", headers=headers, files={"file": ("x.xml", xr.encode(), "application/xml")})).json()
    assert report["valid"] is False
    assert any(e["rule_id"] == "BR-DE-15" and e["source"] == "kosit" for e in report["errors"])
