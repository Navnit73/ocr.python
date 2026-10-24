"""
Server-side recalculation of EN 16931 line amounts, VAT breakdown and document totals.
Supplied values are never trusted: they are compared with the calculation and mismatches are reported
with the EN 16931 rule they violate (BR-CO-10 … BR-CO-16, BR-S-08/09 …).
"""

from collections import OrderedDict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, List, Optional, Tuple

from app.schemas.einvoice import EInvoiceData, Totals, ValidationIssue, VatBreakdownItem
from app.services.einvoice.codes import CENT, EXEMPTION_REASON_REQUIRED, VAT_RULE_PREFIX, fmt_amount

ZERO = Decimal("0")


def round_cent(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class Calculation:
    line_amounts: List[Decimal]
    vat_breakdown: List[VatBreakdownItem]
    totals: Totals
    issues: List[ValidationIssue] = field(default_factory=list)


def line_net(line) -> Decimal:
    """BT-131 = quantity × net price ÷ base quantity − line allowances (BG-27) + line charges (BG-28)."""
    gross = round_cent(line.quantity * line.net_price / (line.price_base_quantity or Decimal(1)))
    return gross - sum((a.amount for a in line.allowances), ZERO) + sum((c.amount for c in line.charges), ZERO)


def _mismatch(rule_id: str, field_path: str, message: str, expected: Decimal, actual: Decimal, severity: str = "error") -> ValidationIssue:
    return ValidationIssue(
        rule_id=rule_id,
        field=field_path,
        message=f"{message}: expected {fmt_amount(expected)}, got {fmt_amount(actual)}",
        severity=severity,
        source="business-rules",
        expected=fmt_amount(expected),
        actual=fmt_amount(actual),
    )


def calculate(invoice: EInvoiceData) -> Calculation:
    """Recalculates every derived amount and reports differences to the supplied values."""
    issues: List[ValidationIssue] = []

    # Line net amounts (BT-131)
    line_amounts: List[Decimal] = []
    for index, line in enumerate(invoice.lines):
        computed = line_net(line)
        if line.line_net_amount is not None and line.line_net_amount != computed:
            issues.append(_mismatch(
                "CALC-LINE-NET", f"lines[{index}].line_net_amount",
                f"Line {line.position}: line net amount must equal quantity × net price ÷ base quantity − line allowances + line charges",
                computed, line.line_net_amount,
            ))
        # The supplied amount is what ends up in the XML when it is declared, so sums are checked against it.
        line_amounts.append(line.line_net_amount if line.line_net_amount is not None else computed)

    # VAT breakdown grouped by (category, rate) in order of first appearance (BG-23)
    groups: "OrderedDict[Tuple[str, Decimal], Decimal]" = OrderedDict()
    for line, amount in zip(invoice.lines, line_amounts):
        key = (line.vat_category.value, line.vat_rate)
        groups[key] = groups.get(key, ZERO) + amount
    for charge in invoice.charges:
        key = (charge.vat_category.value, charge.vat_rate)
        groups[key] = groups.get(key, ZERO) + charge.amount
    for allowance in invoice.allowances:
        key = (allowance.vat_category.value, allowance.vat_rate)
        groups[key] = groups.get(key, ZERO) - allowance.amount

    declared: Dict[Tuple[str, Decimal], VatBreakdownItem] = {}
    for index, item in enumerate(invoice.vat_breakdown or []):
        key = (item.category.value, item.rate)
        if key not in groups:
            issues.append(ValidationIssue(
                rule_id="BR-CO-18", field=f"vat_breakdown[{index}]", source="business-rules",
                message=f"VAT breakdown for category {key[0]} at {key[1]}% has no matching lines, allowances or charges",
            ))
            continue
        declared[key] = item

    breakdown: List[VatBreakdownItem] = []
    for (category, rate), basis in groups.items():
        basis = round_cent(basis)
        tax = round_cent(basis * rate / Decimal(100))
        prefix = VAT_RULE_PREFIX[category]
        supplied = declared.get((category, rate))
        index = (invoice.vat_breakdown or []).index(supplied) if supplied else None
        if supplied:
            if supplied.basis_amount != basis:
                issues.append(_mismatch(
                    f"BR-{prefix}-08", f"vat_breakdown[{index}].basis_amount",
                    f"Taxable amount for {category} {rate}% must equal the sum of line, charge and allowance amounts", basis, supplied.basis_amount,
                ))
            if supplied.tax_amount != tax:
                # Some ERP systems round per line; a one-cent deviation is tolerated as a warning.
                severity = "warning" if abs(supplied.tax_amount - tax) <= CENT else "error"
                issues.append(_mismatch(
                    f"BR-{prefix}-09", f"vat_breakdown[{index}].tax_amount",
                    f"Tax amount for {category} {rate}% must equal taxable amount × rate", tax, supplied.tax_amount, severity,
                ))
        reason = supplied.exemption_reason if supplied else None
        reason_code = supplied.exemption_reason_code if supplied else None
        if category in EXEMPTION_REASON_REQUIRED and not (reason or reason_code):
            issues.append(ValidationIssue(
                rule_id=f"BR-{prefix}-10", field="vat_breakdown", source="business-rules",
                message=f"VAT category {category} requires an exemption reason (BT-120) or reason code (BT-121) in vat_breakdown",
            ))
        breakdown.append(VatBreakdownItem(
            category=category, rate=rate, basis_amount=basis,
            tax_amount=supplied.tax_amount if supplied else tax,
            exemption_reason=reason, exemption_reason_code=reason_code,
        ))

    line_total = sum(line_amounts, ZERO)
    allowances = sum((a.amount for a in invoice.allowances), ZERO)
    charges = sum((c.amount for c in invoice.charges), ZERO)
    net_total = line_total - allowances + charges
    vat_total = sum((item.tax_amount for item in breakdown), ZERO)
    gross_total = net_total + vat_total
    supplied_totals = invoice.totals or Totals()
    prepaid = supplied_totals.prepaid_amount or ZERO
    rounding = supplied_totals.rounding_amount or ZERO
    payable = gross_total - prepaid + rounding

    computed = Totals(
        line_total=line_total,
        allowances=allowances if invoice.allowances else None,
        charges=charges if invoice.charges else None,
        net_total=net_total,
        vat_total=vat_total,
        gross_total=gross_total,
        prepaid_amount=supplied_totals.prepaid_amount,
        rounding_amount=supplied_totals.rounding_amount,
        payable_amount=payable,
    )

    checks = (
        ("BR-CO-10", "line_total", "Sum of line net amounts (BT-106)", line_total),
        ("BR-CO-11", "allowances", "Sum of allowances (BT-107)", allowances),
        ("BR-CO-12", "charges", "Sum of charges (BT-108)", charges),
        ("BR-CO-13", "net_total", "Total without VAT (BT-109) = line total − allowances + charges", net_total),
        ("BR-CO-14", "vat_total", "Total VAT (BT-110) = sum of VAT breakdown tax amounts", vat_total),
        ("BR-CO-15", "gross_total", "Total with VAT (BT-112) = total without VAT + total VAT", gross_total),
        ("BR-CO-16", "payable_amount", "Amount due (BT-115) = total with VAT − paid amount + rounding", payable),
    )
    for rule_id, name, label, expected in checks:
        actual = getattr(supplied_totals, name)
        if actual is not None and actual != expected:
            issues.append(_mismatch(rule_id, f"totals.{name}", label, expected, actual))

    return Calculation(line_amounts=line_amounts, vat_breakdown=breakdown, totals=computed, issues=issues)


def complete_invoice(invoice: EInvoiceData, calculation: Calculation) -> EInvoiceData:
    """Returns a copy of the invoice with all derived amounts filled in from the calculation."""
    lines = [
        line.model_copy(update={"line_net_amount": amount})
        for line, amount in zip(invoice.lines, calculation.line_amounts)
    ]
    return invoice.model_copy(update={
        "lines": lines,
        "vat_breakdown": calculation.vat_breakdown,
        "totals": calculation.totals,
    })
