"""
EN 16931 and XRechnung (BR-DE) business rule checks on the semantic invoice model.
These run before generation (to reject bad input with field-level messages) and after parsing uploaded
documents. The KoSIT validator, when configured, remains the authoritative check for XRechnung.
"""

from decimal import Decimal
from typing import List, Tuple

from app.schemas.einvoice import EInvoiceData, EInvoiceProfile, ValidationIssue
from app.services.einvoice.calculator import Calculation, calculate, round_cent
from app.services.einvoice.codes import (
    CARD_MEANS,
    CREDIT_NOTE_TYPE_CODES,
    CREDIT_TRANSFER_MEANS,
    DIRECT_DEBIT_MEANS,
    XRECHNUNG_TYPE_CODES,
    fmt_amount,
    leitweg_check_digits_ok,
    looks_like_leitweg_id,
)


def _issue(rule_id: str, field: str, message: str, severity: str = "error") -> ValidationIssue:
    return ValidationIssue(rule_id=rule_id, field=field, message=message, severity=severity, source="business-rules")


def _vat_rules(invoice: EInvoiceData) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    categories = {line.vat_category.value for line in invoice.lines}
    categories |= {item.vat_category.value for item in invoice.allowances + invoice.charges}
    seller, buyer = invoice.seller, invoice.buyer
    seller_registered = bool(seller.vat_id or seller.tax_number)

    for category, rule_id in (("S", "BR-S-02"), ("Z", "BR-Z-02"), ("E", "BR-E-02"), ("L", "BR-AF-02"), ("M", "BR-AG-02")):
        if category in categories and not seller_registered:
            issues.append(_issue(rule_id, "seller.vat_id", f"Lines with VAT category {category} require the seller VAT ID (BT-31) or tax number (BT-32)"))
    if "AE" in categories:
        if not seller_registered:
            issues.append(_issue("BR-AE-02", "seller.vat_id", "Reverse charge (AE) requires the seller VAT ID (BT-31) or tax number (BT-32)"))
        if not (buyer.vat_id or buyer.legal_registration_id):
            issues.append(_issue("BR-AE-02", "buyer.vat_id", "Reverse charge (AE) requires the buyer VAT ID (BT-48) or legal registration ID (BT-47)"))
    if "K" in categories:
        if not seller.vat_id:
            issues.append(_issue("BR-IC-02", "seller.vat_id", "Intra-community supply (K) requires the seller VAT ID (BT-31)"))
        if not buyer.vat_id:
            issues.append(_issue("BR-IC-02", "buyer.vat_id", "Intra-community supply (K) requires the buyer VAT ID (BT-48)"))
        if not (invoice.delivery_date or invoice.billing_period_start):
            issues.append(_issue("BR-IC-11", "delivery_date", "Intra-community supply (K) requires the delivery date (BT-72) or invoicing period (BG-14)"))
        if not invoice.delivery_address:
            issues.append(_issue("BR-IC-12", "delivery_address", "Intra-community supply (K) requires the deliver-to address with country (BT-80)"))
    if "G" in categories and not seller.vat_id:
        issues.append(_issue("BR-G-02", "seller.vat_id", "Export outside the EU (G) requires the seller VAT ID (BT-31)"))
    if "O" in categories and (seller.vat_id or buyer.vat_id):
        issues.append(_issue("BR-O-02", "seller.vat_id", "Invoices with VAT category O (not subject to VAT) must not contain the seller or buyer VAT ID"))
    if "O" in categories and len(categories) > 1:
        issues.append(_issue("BR-O-11", "lines", "VAT category O (not subject to VAT) cannot be combined with other VAT categories"))
    return issues


def _payment_rules(invoice: EInvoiceData, calculation: Calculation) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    payment = invoice.payment
    payable = calculation.totals.payable_amount or 0
    if payable > 0 and not (payment and (payment.due_date or payment.terms or payment.discount_days)):
        issues.append(_issue("BR-CO-25", "payment.due_date", "A positive amount due requires a payment due date (BT-9) or payment terms (BT-20)"))
    if payment and payment.means_code in CREDIT_TRANSFER_MEANS and not payment.iban:
        issues.append(_issue("BR-61", "payment.iban", "Credit transfer (payment means 30/58) requires the payee IBAN / account (BT-84)"))
    if payment and payment.due_date and payment.due_date < invoice.issue_date:
        issues.append(_issue("DE-DUE-DATE", "payment.due_date", "Payment due date is before the issue date", "warning"))
    return issues


def _allowance_percent_rules(invoice: EInvoiceData, profile: EInvoiceProfile) -> List[ValidationIssue]:
    """When base amount and percentage are both given, amount = base × percent ÷ 100 (PEPPOL-EN16931-R040,
    enforced by XRechnung; advisory for ZUGFeRD)."""
    severity = "error" if profile == EInvoiceProfile.XRECHNUNG else "warning"
    entries = [(f"allowances[{i}]", a) for i, a in enumerate(invoice.allowances)]
    entries += [(f"charges[{i}]", c) for i, c in enumerate(invoice.charges)]
    for li, line in enumerate(invoice.lines):
        entries += [(f"lines[{li}].allowances[{i}]", a) for i, a in enumerate(line.allowances)]
        entries += [(f"lines[{li}].charges[{i}]", c) for i, c in enumerate(line.charges)]
    issues = []
    for path, item in entries:
        if item.base_amount is not None and item.percent is not None:
            expected = round_cent(item.base_amount * item.percent / Decimal(100))
            if expected != item.amount:
                issues.append(_issue(
                    "PEPPOL-EN16931-R040", f"{path}.amount",
                    f"Amount must equal base amount × percentage ÷ 100: expected {fmt_amount(expected)}, got {fmt_amount(item.amount)}",
                    severity,
                ))
    return issues


def _seller_identification(invoice: EInvoiceData) -> List[ValidationIssue]:
    seller = invoice.seller
    legal_id = seller.legal.commercial_register if seller.legal else None
    if not (seller.id or seller.global_id or legal_id or seller.vat_id):
        return [_issue(
            "BR-CO-26", "seller.vat_id",
            "The seller needs an identifier (BT-29 id/global_id), legal registration ID (BT-30 legal.commercial_register) or VAT ID (BT-31)",
        )]
    return []


def _address_rules(invoice: EInvoiceData, profile: EInvoiceProfile) -> List[ValidationIssue]:
    """UStG §14 requires the full address of both parties; XRechnung enforces city/post code (BR-DE-3/4/8/9)."""
    issues: List[ValidationIssue] = []
    xrechnung_rules = {"seller": {"city": "BR-DE-3", "postcode": "BR-DE-4"}, "buyer": {"city": "BR-DE-8", "postcode": "BR-DE-9"}}
    for role in ("seller", "buyer"):
        address = getattr(invoice, role).address
        for part in ("street", "postcode", "city"):
            if getattr(address, part):
                continue
            rule = xrechnung_rules[role].get(part) if profile == EInvoiceProfile.XRECHNUNG else None
            if rule:
                issues.append(_issue(rule, f"{role}.address.{part}", f"{role.capitalize()} {part} is mandatory for XRechnung"))
            else:
                # Mandatory for German parties (UStG §14 (4) Nr. 1); only advisory for foreign addresses.
                severity = "error" if address.country == "DE" else "warning"
                issues.append(_issue("DE-UStG-14-ADDRESS", f"{role}.address.{part}", f"{role.capitalize()} address is incomplete: {part} missing (UStG §14)", severity))
    return issues


def _german_rules(invoice: EInvoiceData) -> List[ValidationIssue]:
    """Plausibility checks derived from UStG §14 (mandatory invoice content in Germany)."""
    issues: List[ValidationIssue] = []
    if not (invoice.delivery_date or invoice.billing_period_start):
        issues.append(_issue(
            "DE-UStG-14-DELIVERY", "delivery_date",
            "UStG §14 (4) Nr. 6 requires the date of supply (delivery_date) or a billing period", "warning",
        ))
    if (invoice.type_code in CREDIT_NOTE_TYPE_CODES or invoice.type_code == "384") and not invoice.preceding_invoice_number:
        issues.append(_issue(
            "DE-PRECEDING-INVOICE", "preceding_invoice_number",
            "Credit notes and corrected invoices should reference the preceding invoice (BT-25)", "warning",
        ))
    return issues


def _xrechnung_rules(invoice: EInvoiceData) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    seller, buyer, payment = invoice.seller, invoice.buyer, invoice.payment
    contact = seller.contact

    if invoice.type_code not in XRECHNUNG_TYPE_CODES:
        issues.append(_issue("BR-DE-17", "type_code", f"XRechnung only allows the type codes {', '.join(sorted(XRECHNUNG_TYPE_CODES))}"))

    reference = (buyer.buyer_reference or "").strip()
    if not reference:
        issues.append(_issue("BR-DE-15", "buyer.buyer_reference", "Buyer reference / Leitweg-ID (BT-10) is mandatory for XRechnung"))
    elif looks_like_leitweg_id(reference):
        if not leitweg_check_digits_ok(reference):
            issues.append(_issue("LEITWEG-ID", "buyer.buyer_reference", f"Leitweg-ID '{reference}' has invalid check digits (ISO 7064 Mod 97-10)"))
    else:
        issues.append(_issue(
            "LEITWEG-ID", "buyer.buyer_reference",
            "Buyer reference does not look like a Leitweg-ID (e.g. 04011000-1234512345-06); public-sector portals will reject it", "warning",
        ))

    if not contact:
        issues.append(_issue("BR-DE-2", "seller.contact", "Seller contact (BG-6) is mandatory for XRechnung"))
    else:
        if not contact.name:
            issues.append(_issue("BR-DE-5", "seller.contact.name", "Seller contact point (BT-41) is mandatory for XRechnung"))
        if not contact.phone:
            issues.append(_issue("BR-DE-6", "seller.contact.phone", "Seller contact telephone (BT-42) is mandatory for XRechnung"))
        if not contact.email:
            issues.append(_issue("BR-DE-7", "seller.contact.email", "Seller contact email (BT-43) is mandatory for XRechnung"))

    if not seller.electronic_address:
        issues.append(_issue("PEPPOL-EN16931-R020", "seller.electronic_address", "Seller electronic address (BT-34) is mandatory in XRechnung 3.x"))
    if not buyer.electronic_address:
        issues.append(_issue("PEPPOL-EN16931-R010", "buyer.electronic_address", "Buyer electronic address (BT-49) is mandatory in XRechnung 3.x"))

    if invoice.delivery_address:
        if not invoice.delivery_address.city:
            issues.append(_issue("BR-DE-10", "delivery_address.city", "Deliver-to city (BT-77) is mandatory in XRechnung when a delivery address is given"))
        if not invoice.delivery_address.postcode:
            issues.append(_issue("BR-DE-11", "delivery_address.postcode", "Deliver-to post code (BT-78) is mandatory in XRechnung when a delivery address is given"))

    if not (seller.vat_id or seller.tax_number):
        issues.append(_issue("BR-DE-16", "seller.vat_id", "Seller VAT ID (BT-31) or tax number (BT-32) is mandatory for XRechnung"))

    if not payment:
        issues.append(_issue("BR-DE-1", "payment", "Payment instructions (BG-16) are mandatory for XRechnung"))
    else:
        if payment.means_code in DIRECT_DEBIT_MEANS:
            if not payment.mandate_reference:
                issues.append(_issue("SEPA-MANDATE", "payment.mandate_reference", "Direct debit (code 49/59) requires the mandate reference (BT-89)"))
            if not payment.creditor_id:
                issues.append(_issue("BR-DE-30", "payment.creditor_id", "Direct debit requires the SEPA creditor ID (BT-90)"))
            if not payment.debtor_iban:
                issues.append(_issue("BR-DE-31", "payment.debtor_iban", "Direct debit requires the debited IBAN (BT-91)"))
        if payment.means_code in CARD_MEANS:
            issues.append(_issue("BR-DE-24-a", "payment.means_code", "Card payments require card details (BG-18), which this API does not support"))
    return issues


def check_business_rules(invoice: EInvoiceData, profile: EInvoiceProfile) -> Tuple[Calculation, List[ValidationIssue], List[ValidationIssue]]:
    """Runs calculation + rule checks. Returns (calculation, errors, warnings)."""
    calculation = calculate(invoice)
    issues = list(calculation.issues)
    issues += _vat_rules(invoice)
    issues += _payment_rules(invoice, calculation)
    issues += _seller_identification(invoice)
    issues += _allowance_percent_rules(invoice, profile)
    issues += _address_rules(invoice, profile)
    issues += _german_rules(invoice)
    if profile == EInvoiceProfile.XRECHNUNG:
        issues += _xrechnung_rules(invoice)
    errors = [issue for issue in issues if issue.severity == "error"]
    warnings = [issue for issue in issues if issue.severity == "warning"]
    return calculation, errors, warnings
