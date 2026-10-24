"""
E-invoice validation pipeline: secure XML parsing → syntax/profile detection → XSD → semantic mapping →
EN 16931 / XRechnung business rules → KoSIT validator (XRechnung). PDFs additionally get ZUGFeRD/PDF-A checks.
"""

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from importlib import resources
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree
from pydantic import ValidationError

from app.core.einvoice_config import get_einvoice_settings
from app.schemas.einvoice import EInvoiceData, EInvoiceProfile, ValidationIssue, ValidationReport
from app.services.einvoice.codes import EN16931_COMPLIANT_PROFILES, PROFILE_XSD_LEVEL, XMP_CONFORMANCE_LEVEL
from app.services.einvoice.kosit import run_kosit
from app.services.einvoice.pdf import VERSION_BY_FILENAME, PdfInputError, extract_embedded_xml, read_xmp
from app.services.einvoice.rules import check_business_rules
from app.services.einvoice.xml_parser import DocumentInfo, UnsafeXMLError, detect_document, parse_invoice, parse_xml_secure

# EN 16931 "shall be present" rule IDs, used only when the field is missing
_FIELD_RULES = {
    "invoice_number": "BR-02", "issue_date": "BR-03", "type_code": "BR-04", "currency": "BR-05",
    "seller.name": "BR-06", "buyer.name": "BR-07", "seller.address": "BR-08", "seller.address.country": "BR-09",
    "buyer.address": "BR-10", "buyer.address.country": "BR-11", "lines": "BR-16",
    "lines[].position": "BR-21", "lines[].quantity": "BR-22", "lines[].unit": "BR-23", "lines[].line_net_amount": "BR-24",
    "lines[].description": "BR-25", "lines[].net_price": "BR-26", "lines[].vat_category": "BR-CO-4",
}
# Rule IDs for present-but-invalid values
_VALUE_RULES = {
    "type_code": "BR-CL-01", "currency": "BR-CL-04", "seller.address.country": "BR-CL-14", "buyer.address.country": "BR-CL-14",
    "lines[].net_price": "BR-27", "lines[].gross_price": "BR-28", "lines[].unit": "BR-CL-23",
}

# Profiles covered by the scenarios of the KoSIT XRechnung validator configuration
KOSIT_PROFILES = {"XRECHNUNG", "EN 16931 (COMFORT)", "BASIC"}

_FACTURX_XSD = {
    "minimum": "facturx-minimum/Factur-X_MINIMUM.xsd",
    "basicwl": "facturx-basicwl/Factur-X_BASICWL.xsd",
    "basic": "facturx-basic/Factur-X_BASIC.xsd",
    "en16931": "facturx-en16931/Factur-X_EN16931.xsd",
    "extended": "facturx-extended/Factur-X_EXTENDED.xsd",
    "cii-full": "cii-extended-ctc-fr/CrossIndustryInvoice_100pD22B.xsd",
    "zugferd1": "zugferd/ZUGFeRD1p0.xsd",
    "ubl-invoice": "ubl-2.1/maindoc/UBL-Invoice-2.1.xsd",
    "ubl-creditnote": "ubl-2.1/maindoc/UBL-CreditNote-2.1.xsd",
}


def format_loc(loc: Tuple[Any, ...]) -> str:
    path = ""
    for part in loc:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}" if path else str(part)
    return path


def pydantic_errors_to_issues(exc: ValidationError, source: str = "input", prefix: Optional[str] = None) -> List[ValidationIssue]:
    """Converts pydantic errors into field-level issues, tagging EN 16931 rule IDs where one applies."""
    issues = []
    for err in exc.errors():
        path = format_loc(err.get("loc", ()))
        generic = re.sub(r"\[\d+\]", "[]", path)
        rule_id = (_FIELD_RULES if err.get("type") == "missing" else _VALUE_RULES).get(generic)
        if rule_id is None:
            found = re.search(r"\((BR-[A-Z0-9-]+)\)", err.get("msg", ""))
            rule_id = found.group(1) if found else "SCHEMA"
        message = err.get("msg", "Invalid value").removeprefix("Value error, ")
        issues.append(ValidationIssue(
            rule_id=rule_id,
            field=f"{prefix}.{path}" if prefix and path else (prefix or path or None),
            message=message,
            source=source,
        ))
    return issues


@lru_cache(maxsize=None)
def _schema(key: str) -> etree.XMLSchema:
    path = resources.files("facturx").joinpath("xsd_and_schematron", _FACTURX_XSD[key])
    return etree.XMLSchema(file=str(path))


def _xsd_key(root: etree._Element, info: DocumentInfo) -> Optional[str]:
    if info.syntax == "CII":
        return PROFILE_XSD_LEVEL.get(info.profile)
    if info.syntax == "ZUGFeRD-1.0":
        return "zugferd1"
    if info.syntax == "UBL":
        return "ubl-creditnote" if etree.QName(root).localname == "CreditNote" else "ubl-invoice"
    return None


def xsd_check(root: etree._Element, info: DocumentInfo) -> Tuple[str, List[ValidationIssue]]:
    key = _xsd_key(root, info)
    if key is None:
        return "skipped", []
    schema = _schema(key)
    if schema.validate(root):
        return "passed", []
    issues = [
        ValidationIssue(rule_id="XSD", field=entry.path or None, message=entry.message, source="xsd", line=entry.line or None)
        for entry in list(schema.error_log)[:50]
    ]
    return "failed", issues


def describe_version(info: DocumentInfo, embedded_filename: Optional[str] = None, xmp_version: Optional[str] = None) -> Optional[str]:
    if info.profile == "XRECHNUNG":
        base = f"XRechnung {info.xrechnung_version}" if info.xrechnung_version else "XRechnung"
        if embedded_filename:
            return f"{base} in {VERSION_BY_FILENAME.get(embedded_filename, 'ZUGFeRD 2.x')}"
        return f"{base} ({info.syntax})"
    if embedded_filename:
        version = VERSION_BY_FILENAME.get(embedded_filename, "ZUGFeRD 2.x")
        return f"{version} (XMP version {xmp_version})" if xmp_version else version
    if info.syntax == "CII":
        return "ZUGFeRD 2.x / Factur-X 1.0 (CII D16B)"
    if info.syntax == "ZUGFeRD-1.0":
        return "ZUGFeRD 1.0"
    if info.syntax == "UBL":
        return "UBL 2.1"
    return None


@dataclass
class Analysis:
    """Everything learned from an uploaded document; shared by /extract and /validate."""
    source_type: str
    raw_xml: Optional[bytes] = None
    info: Optional[DocumentInfo] = None
    invoice: Optional[Dict[str, Any]] = None
    model: Optional[EInvoiceData] = None
    embedded_filename: Optional[str] = None
    xmp: Optional[Dict[str, Optional[str]]] = None
    errors: List[ValidationIssue] = field(default_factory=list)
    warnings: List[ValidationIssue] = field(default_factory=list)
    checks: Dict[str, str] = field(default_factory=dict)

    def report(self) -> ValidationReport:
        info = self.info
        return ValidationReport(
            valid=not self.errors,
            syntax=info.syntax if info else None,
            profile=info.profile if info else None,
            profile_urn=info.profile_urn if info else None,
            version=describe_version(info, self.embedded_filename, (self.xmp or {}).get("version")) if info else None,
            errors=self.errors,
            warnings=self.warnings,
            checks=self.checks,
            validated_at=datetime.now(timezone.utc),
        )


def _error(rule_id: str, message: str, source: str, field_path: Optional[str] = None, severity: str = "error") -> ValidationIssue:
    return ValidationIssue(rule_id=rule_id, field=field_path, message=message, source=source, severity=severity)


def _add(analysis: Analysis, issues: List[ValidationIssue]) -> None:
    for issue in issues:
        (analysis.errors if issue.severity == "error" else analysis.warnings).append(issue)


def _analyse_xml_sync(analysis: Analysis, xml: bytes) -> Optional[EInvoiceProfile]:
    """CPU-bound part of XML analysis. Returns the profile to use for KoSIT, if any."""
    analysis.raw_xml = xml
    try:
        root = parse_xml_secure(xml)
    except UnsafeXMLError as exc:
        analysis.errors.append(_error("XML-WELLFORMED", str(exc), "xml"))
        analysis.checks["xml"] = "failed"
        return None
    analysis.checks["xml"] = "passed"

    info = detect_document(root)
    analysis.info = info
    if info.syntax == "UNKNOWN":
        analysis.errors.append(_error("XML-SYNTAX", "Root element is neither a CII CrossIndustryInvoice nor a UBL Invoice/CreditNote", "xml"))
        return None

    analysis.checks["xsd"], xsd_issues = xsd_check(root, info)
    _add(analysis, xsd_issues)

    if info.profile not in EN16931_COMPLIANT_PROFILES:
        # Still hand the data to /extract (best effort); the semantic checks below need a full EN 16931 invoice.
        analysis.invoice = parse_invoice(root, info)
        analysis.errors.append(_error(
            "PROFILE-NOT-EN16931",
            f"Profile '{info.profile}' does not carry a complete EN 16931 invoice. Use EN 16931 (COMFORT) or XRechnung; "
            "MINIMUM and BASIC WL are not valid invoices under the German VAT Act (UStG).",
            "xml", "ExchangedDocumentContext/GuidelineSpecifiedDocumentContextParameter/ID",
        ))
        analysis.checks["business_rules"] = "skipped"
        return None

    analysis.invoice = parse_invoice(root, info)
    profile = EInvoiceProfile.XRECHNUNG if info.profile == "XRECHNUNG" else EInvoiceProfile.ZUGFERD_EN16931
    try:
        model = EInvoiceData.model_validate(analysis.invoice)
    except ValidationError as exc:
        _add(analysis, pydantic_errors_to_issues(exc, source="xml"))
        analysis.checks["business_rules"] = "failed"
        return profile
    analysis.model = model
    _, errors, warnings = check_business_rules(model, profile)
    _add(analysis, errors + warnings)
    analysis.checks["business_rules"] = "failed" if errors else "passed"
    return profile


async def analyse_xml(analysis: Analysis, xml: bytes, run_kosit_check: bool = True) -> Analysis:
    profile = await asyncio.to_thread(_analyse_xml_sync, analysis, xml)
    if profile is None or not run_kosit_check or analysis.info.profile not in KOSIT_PROFILES:
        return analysis

    # The KoSIT XRechnung configuration also ships an "EN16931 (CII)" scenario, so ZUGFeRD EN 16931/BASIC is
    # checked against the official schematron when the validator is installed. It is only mandatory for XRechnung.
    settings = get_einvoice_settings()
    xrechnung = profile == EInvoiceProfile.XRECHNUNG
    if not settings.kosit_configured and not xrechnung:
        return analysis
    result = await run_kosit(xml)
    analysis.checks["kosit"] = result.status
    _add(analysis, result.errors + result.warnings)
    if result.status in ("skipped", "error"):
        severity = "error" if settings.kosit_required and xrechnung else "warning"
        _add(analysis, [_error("KOSIT", result.detail or "KoSIT validation unavailable", "kosit", severity=severity)])
        if severity == "error":
            analysis.checks["kosit"] = "failed"
    return analysis


async def analyse_pdf(content: bytes) -> Analysis:
    analysis = Analysis(source_type="pdf")
    try:
        embedded_filename, xml = await asyncio.to_thread(extract_embedded_xml, content)
    except PdfInputError as exc:
        analysis.errors.append(_error("PDF-READ", str(exc), "pdf"))
        analysis.checks["pdf"] = "failed"
        return analysis
    analysis.xmp = await asyncio.to_thread(read_xmp, content)
    if not xml:
        analysis.errors.append(_error(
            "PDF-NO-XML", "No embedded e-invoice XML (factur-x.xml, zugferd-invoice.xml, xrechnung.xml) found. "
            "A plain PDF is not an e-invoice under the German 2025 rules.", "pdf",
        ))
        analysis.checks["pdf"] = "failed"
        return analysis
    analysis.embedded_filename = embedded_filename
    await analyse_xml(analysis, xml)

    pdf_warnings = []
    xmp = analysis.xmp
    if xmp.get("pdfa_part") != "3":
        pdf_warnings.append(_error("PDF-A3", f"PDF does not declare PDF/A-3 in its XMP metadata (pdfaid:part={xmp.get('pdfa_part')})", "pdf", severity="warning"))
    if not xmp.get("conformance_level"):
        pdf_warnings.append(_error("PDF-XMP", "Missing ZUGFeRD/Factur-X XMP extension schema (fx:ConformanceLevel)", "pdf", severity="warning"))
    elif analysis.info and analysis.info.profile in XMP_CONFORMANCE_LEVEL:
        level = " ".join(xmp["conformance_level"].upper().replace("-", " ").split())
        expected = XMP_CONFORMANCE_LEVEL[analysis.info.profile]
        # ZUGFeRD 2.0 wrote "COMFORT" for the EN 16931 profile
        if level != expected and not (level == "COMFORT" and expected == "EN 16931"):
            pdf_warnings.append(_error(
                "PDF-XMP-PROFILE", f"XMP conformance level '{xmp['conformance_level']}' does not match the XML profile '{analysis.info.profile}'",
                "pdf", severity="warning",
            ))
    if xmp.get("document_file_name") and xmp["document_file_name"] != embedded_filename:
        pdf_warnings.append(_error(
            "PDF-XMP-FILENAME", f"XMP DocumentFileName '{xmp['document_file_name']}' differs from the attachment '{embedded_filename}'",
            "pdf", severity="warning",
        ))
    analysis.warnings.extend(pdf_warnings)
    analysis.checks["pdf"] = "passed"
    return analysis


async def analyse_upload(content: bytes, kind: str) -> Analysis:
    if kind == "pdf":
        return await analyse_pdf(content)
    return await analyse_xml(Analysis(source_type="xml"), content)
