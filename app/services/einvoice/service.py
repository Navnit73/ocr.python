"""
E-invoice orchestration: input parsing → business rules → CII generation → validation → PDF/A-3 embedding.
"""

import asyncio
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from app.core.einvoice_config import get_einvoice_settings
from app.schemas.einvoice import (
    EInvoiceData,
    EInvoiceProfile,
    FormatType,
    InvoiceDataSchema,
    PdfTemplate,
    ValidationIssue,
    ValidationReport,
)
from app.services.einvoice.calculator import complete_invoice
from app.services.einvoice.cii_builder import build_cii
from app.services.einvoice.codes import COUNTRY_CODES, COUNTRY_NAME_ALIASES
from app.services.einvoice.pdf import PdfInputError, check_uploaded_pdf, embed_xml, normalize_to_pdfa
from app.services.einvoice.pdf_templates import render_invoice_pdf
from app.services.einvoice.rules import check_business_rules
from app.services.einvoice.validator import Analysis, analyse_pdf, analyse_xml, pydantic_errors_to_issues


class EInvoiceInputError(Exception):
    """Invalid input; rendered as HTTP 422 with field-level errors."""

    def __init__(self, message: str, errors: List[ValidationIssue], report: Optional[ValidationReport] = None):
        super().__init__(message)
        self.message = message
        self.errors = errors
        self.report = report


@dataclass
class ZugferdResult:
    pdf: bytes
    xml: bytes
    report: ValidationReport


@dataclass
class XRechnungResult:
    xml: bytes
    report: ValidationReport


def parse_invoice_json(raw: str, field_name: str = "invoice_data") -> EInvoiceData:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EInvoiceInputError("invoice_data is not valid JSON", [ValidationIssue(
            rule_id="JSON", field=field_name, message=f"Invalid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})", source="input",
        )]) from exc
    try:
        return EInvoiceData.model_validate(payload)
    except ValidationError as exc:
        raise EInvoiceInputError("Invoice data validation failed", pydantic_errors_to_issues(exc, prefix=field_name)) from exc


def prepare_invoice(invoice: EInvoiceData, profile: EInvoiceProfile) -> EInvoiceData:
    """Recalculates all amounts, enforces business rules and returns the completed invoice."""
    calculation, errors, _ = check_business_rules(invoice, profile)
    if errors:
        raise EInvoiceInputError("Invoice violates EN 16931 / XRechnung business rules", errors)
    return complete_invoice(invoice, calculation)


def _ensure_valid(analysis: Analysis, what: str) -> ValidationReport:
    report = analysis.report()
    if not report.valid:
        raise EInvoiceInputError(f"Generated {what} failed validation", report.errors, report)
    return report


async def create_zugferd(invoice: EInvoiceData, source_pdf: Optional[bytes] = None,
                         template: PdfTemplate = PdfTemplate.CLASSIC) -> ZugferdResult:
    completed = prepare_invoice(invoice, EInvoiceProfile.ZUGFERD_EN16931)
    xml = await asyncio.to_thread(build_cii, completed, EInvoiceProfile.ZUGFERD_EN16931)
    # KoSIT (if installed) runs once, on the final PDF below.
    _ensure_valid(await analyse_xml(Analysis(source_type="xml"), xml, run_kosit_check=False), "XML")

    extra_warnings: List[ValidationIssue] = []
    if source_pdf is not None:
        try:
            await asyncio.to_thread(check_uploaded_pdf, source_pdf)
        except PdfInputError as exc:
            raise EInvoiceInputError("Uploaded PDF cannot be used", [ValidationIssue(rule_id="PDF-INPUT", field="file", message=str(exc), source="pdf")]) from exc
        visual, normalised = await normalize_to_pdfa(source_pdf)
        if not normalised:
            extra_warnings.append(ValidationIssue(
                rule_id="PDFA-SOURCE", field="file", severity="warning", source="pdf",
                message="Uploaded PDF was embedded without PDF/A normalisation. Fonts must be embedded and transparency avoided "
                        "for strict PDF/A-3 conformance (set EINVOICE_GHOSTSCRIPT_BIN to enable conversion; verify with veraPDF).",
            ))
    else:
        visual = await asyncio.to_thread(render_invoice_pdf, completed, template)

    try:
        pdf = await asyncio.to_thread(embed_xml, visual, xml, completed)
    except Exception as exc:  # factur-x raises generic exceptions for PDFs it cannot process
        raise EInvoiceInputError("XML could not be embedded into the PDF", [ValidationIssue(
            rule_id="PDF-EMBED", field="file", message=str(exc)[:500], source="pdf",
        )]) from exc

    # Validate the final artefact (embedded XML + XMP + PDF/A identification), not just the XML we built.
    final = await analyse_pdf(pdf)
    final.warnings.extend(extra_warnings)
    report = _ensure_valid(final, "ZUGFeRD PDF")
    return ZugferdResult(pdf=pdf, xml=xml, report=report)


async def create_xrechnung(invoice: EInvoiceData) -> XRechnungResult:
    completed = prepare_invoice(invoice, EInvoiceProfile.XRECHNUNG)
    xml = await asyncio.to_thread(build_cii, completed, EInvoiceProfile.XRECHNUNG, get_einvoice_settings().xrechnung_version)
    report = _ensure_valid(await analyse_xml(Analysis(source_type="xml"), xml), "XRechnung")
    return XRechnungResult(xml=xml, report=report)


# ---------------------------------------------------------------------------
# Flat form (/convert) → EInvoiceData
# ---------------------------------------------------------------------------

_FLAT_FIELD_MAP = [
    (r"^seller\.address(\..*)?$", "seller_address"),
    (r"^buyer\.address(\..*)?$", "buyer_address"),
    (r"^seller\.vat_id$", "seller_vat_id"),
    (r"^seller\.tax_number$", "seller_tax_number"),
    (r"^seller\.name$", "seller_name"),
    (r"^buyer\.name$", "buyer_name"),
    (r"^buyer\.buyer_reference$", "routing_id"),
    (r"^seller\.contact\.name$", "seller_contact_name"),
    (r"^seller\.contact\.phone$", "seller_phone"),
    (r"^seller\.contact\.email$|^seller\.electronic_address$", "seller_email"),
    (r"^seller\.contact$", "seller_contact_name"),
    (r"^buyer\.electronic_address$", "buyer_email"),
    (r"^payment\.iban$|^payment$", "iban"),
    (r"^payment\.bic$", "bic"),
    (r"^payment\.due_date$", "due_date"),
    (r"^totals\.net_total$", "tax_basis_total"),
    (r"^totals\.vat_total$", "tax_total"),
    (r"^totals\.gross_total$|^totals\.payable_amount$", "grand_total"),
    (r"^lines\[(\d+)\]\.description$", r"line_items[\1].item_description"),
    (r"^lines\[(\d+)\]\.vat_rate$", r"line_items[\1].tax_rate"),
    (r"^lines\[(\d+)\]", r"line_items[\1]"),
]


def _flat_field(path: Optional[str]) -> Optional[str]:
    if not path:
        return path
    path = path.removeprefix("invoice_data.")
    for pattern, replacement in _FLAT_FIELD_MAP:
        if re.search(pattern, path):
            return f"invoice_data.{re.sub(pattern, replacement, path)}"
    return f"invoice_data.{path}"


def remap_flat_errors(exc: EInvoiceInputError) -> EInvoiceInputError:
    """Rewrites field paths of the rich model into the flat form's field names."""
    for issue in exc.errors:
        issue.field = _flat_field(issue.field)
    if exc.report:
        for issue in exc.report.errors + exc.report.warnings:
            issue.field = _flat_field(issue.field)
    return exc


def parse_address(text: str, field_name: str) -> Dict[str, Any]:
    """Parses 'Street 1, 12345 City, Country' into the structured address (country defaults to DE)."""
    parts = [part.strip() for part in re.split(r"[,\n]", text or "") if part.strip()]
    country = "DE"
    if parts:
        last = parts[-1]
        if last.upper() in COUNTRY_CODES:
            country = last.upper()
            parts.pop()
        elif last.lower() in COUNTRY_NAME_ALIASES:
            country = COUNTRY_NAME_ALIASES[last.lower()]
            parts.pop()
    for index, part in enumerate(parts):
        match = re.match(r"^(?:[A-Z]{1,2}-)?(\d{4,5})\s+(.+)$", part)
        if match and index > 0:
            return {
                "street": parts[0],
                "additional_street": ", ".join(parts[1:index]) or None,
                "postcode": match.group(1),
                "city": match.group(2),
                "country": country,
            }
    raise EInvoiceInputError("Address could not be parsed", [ValidationIssue(
        rule_id="ADDRESS", field=f"invoice_data.{field_name}", source="input",
        message="Expected 'Street No, Postcode City, Country' (e.g. 'Lieferantenstraße 20, 80333 München, DE')",
    )])


def _dec(value: float) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation:  # pragma: no cover - pydantic already guarantees floats
        return Decimal(0)


def flat_to_invoice(flat: InvoiceDataSchema) -> EInvoiceData:
    lines = []
    for index, item in enumerate(flat.line_items):
        if item.tax_rate == 0:
            raise EInvoiceInputError("Unsupported VAT rate", [ValidationIssue(
                rule_id="VAT-CATEGORY", field=f"invoice_data.line_items[{index}].tax_rate", source="input",
                message="0 % lines need a VAT category and exemption reason; use /api/einvoice/create-zugferd or /create-xrechnung",
            )])
        lines.append({
            "description": item.item_description,
            "net_price": _dec(item.net_price),
            "quantity": _dec(item.quantity),
            "unit": item.unit,
            "vat_category": "S",
            "vat_rate": _dec(item.tax_rate),
            "line_net_amount": _dec(item.line_net_amount),
        })

    contact = {"name": flat.seller_contact_name, "phone": flat.seller_phone, "email": flat.seller_email}
    payment = None
    if flat.payment_terms or flat.iban or flat.due_date:
        payment = {
            "means_code": "58" if flat.iban else "1",
            "terms": flat.payment_terms,
            "due_date": flat.due_date,
            "iban": flat.iban,
            "bic": flat.bic,
        }
    payload = {
        "invoice_number": flat.invoice_number,
        "issue_date": flat.issue_date,
        "currency": flat.currency,
        "delivery_date": flat.delivery_date,
        "seller": {
            "name": flat.seller_name,
            "address": parse_address(flat.seller_address, "seller_address"),
            "vat_id": flat.seller_vat_id,
            "tax_number": flat.seller_tax_number,
            "electronic_address": flat.seller_email,
            "contact": contact if any(contact.values()) else None,
        },
        "buyer": {
            "name": flat.buyer_name,
            "address": parse_address(flat.buyer_address, "buyer_address"),
            "buyer_reference": flat.routing_id,
            "electronic_address": flat.buyer_email,
        },
        "lines": lines,
        "totals": {
            "net_total": _dec(flat.tax_basis_total),
            "vat_total": _dec(flat.tax_total),
            "gross_total": _dec(flat.grand_total),
        },
        "payment": payment,
    }
    try:
        return EInvoiceData.model_validate(payload)
    except ValidationError as exc:
        raise remap_flat_errors(EInvoiceInputError("Invoice data validation failed", pydantic_errors_to_issues(exc, prefix="invoice_data"))) from exc


def flat_profile(flat: InvoiceDataSchema) -> EInvoiceProfile:
    return EInvoiceProfile.XRECHNUNG if flat.format_type == FormatType.XRECHNUNG else EInvoiceProfile.ZUGFERD_EN16931
