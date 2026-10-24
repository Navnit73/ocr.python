"""
E-Invoicing Pydantic Schemas (EN 16931 semantic model for ZUGFeRD 2.x and XRechnung).
Field descriptions reference the EN 16931 business terms (BT-xx / BG-xx).
"""

import re
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Dict, List, Literal, Optional

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer, ValidationInfo, field_validator, model_validator

from app.services.einvoice.codes import (
    COUNTRY_CODES,
    CURRENCY_CODES,
    INVOICE_TYPE_CODES,
    ZERO_RATE_CATEGORIES,
    compact,
    is_valid_bic,
    is_valid_gtin,
    is_valid_iban,
    is_payment_means_code,
    looks_like_leitweg_id,
    normalize_unit,
    vat_id_format_ok,
    vat_id_prefix_ok,
)


def _as_number(value: Decimal) -> float:
    return float(value)


def _snap_float(places: int):
    """JSON clients computing in binary floats send e.g. 29.700000000000003 for 9.9 × 3. Such values are
    snapped to the intended decimal; genuinely over-precise values still fail the decimal_places check."""
    def snap(value: Any) -> Any:
        if isinstance(value, float):
            rounded = round(value, places)
            if abs(value - rounded) < 1e-9:
                return Decimal(repr(rounded))
        return value
    return BeforeValidator(snap)


# Decimals are kept exact internally and serialised as JSON numbers.
_NUMBER = PlainSerializer(_as_number, return_type=float, when_used="json")
Amount = Annotated[Decimal, _snap_float(2), Field(max_digits=19, decimal_places=2), _NUMBER]
UnitPrice = Annotated[Decimal, _snap_float(10), Field(max_digits=24, decimal_places=10), _NUMBER]
Quantity = Annotated[Decimal, _snap_float(10), Field(max_digits=24, decimal_places=10), _NUMBER]
Rate = Annotated[Decimal, _snap_float(2), Field(ge=0, le=100, max_digits=5, decimal_places=2), _NUMBER]

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class InvoiceTypeCode(str, Enum):
    """Common BT-3 invoice type codes. `type_code` accepts every code of the EN 16931 list (BR-CL-01);
    XRechnung is limited to the codes below (BR-DE-17)."""
    PARTIAL_INVOICE = "326"
    COMMERCIAL_INVOICE = "380"
    CREDIT_NOTE = "381"
    CORRECTED_INVOICE = "384"
    SELF_BILLED_INVOICE = "389"
    PARTIAL_CONSTRUCTION_INVOICE = "875"
    PARTIAL_FINAL_CONSTRUCTION_INVOICE = "876"
    FINAL_CONSTRUCTION_INVOICE = "877"


class VatCategory(str, Enum):
    """BT-151 / BT-118 VAT category code (UNTDID 5305)."""
    S = "S"
    Z = "Z"
    E = "E"
    AE = "AE"
    K = "K"
    G = "G"
    O = "O"
    L = "L"
    M = "M"


class EInvoiceProfile(str, Enum):
    ZUGFERD_EN16931 = "ZUGFERD_EN16931"
    XRECHNUNG = "XRECHNUNG"


# ---------------------------------------------------------------------------
# Parties
# ---------------------------------------------------------------------------

class Address(_Strict):
    """BG-5 / BG-8 postal address. EN 16931 only requires the country; street, post code and city are enforced
    by the business rules (UStG §14 for German parties, BR-DE-3/4/8/9 for XRechnung)."""
    street: Optional[str] = Field(default=None, max_length=200, description="BT-35/BT-50 address line 1", examples=["Lieferantenstraße 20"])
    additional_street: Optional[str] = Field(default=None, max_length=200, description="BT-36/BT-51 address line 2")
    address_line_3: Optional[str] = Field(default=None, max_length=200, description="BT-162/BT-163 address line 3")
    postcode: Optional[str] = Field(default=None, max_length=20, description="BT-38/BT-53 post code", examples=["80333"])
    city: Optional[str] = Field(default=None, max_length=100, description="BT-37/BT-52 city", examples=["München"])
    country: str = Field(..., description="BT-40/BT-55 ISO 3166-1 alpha-2 country code", examples=["DE"])
    country_subdivision: Optional[str] = Field(default=None, max_length=100, description="BT-39/BT-54 region")

    @field_validator("country")
    @classmethod
    def check_country(cls, value: str) -> str:
        code = value.strip().upper()
        if code not in COUNTRY_CODES:
            raise ValueError(f"'{value}' is not an ISO 3166-1 alpha-2 country code (BR-CL-14)")
        return code


class Contact(_Strict):
    """BG-6 / BG-9 contact. Mandatory for the seller in XRechnung (BR-DE-2, BR-DE-5..7)."""
    name: Optional[str] = Field(default=None, max_length=200, description="BT-41/BT-56 contact point", examples=["Max Mustermann"])
    phone: Optional[str] = Field(default=None, max_length=50, description="BT-42/BT-57 telephone", examples=["+49 89 1234567"])
    email: Optional[str] = Field(default=None, max_length=200, description="BT-43/BT-58 email", examples=["rechnung@lieferant.de"])

    @field_validator("email")
    @classmethod
    def check_email(cls, value: Optional[str]) -> Optional[str]:
        if value and not _EMAIL.match(value):
            raise ValueError("Invalid email address")
        return value


class LegalInfo(_Strict):
    """Seller legal information printed on German invoices (GmbH/AG: managing director, commercial register)."""
    managing_director: Optional[str] = Field(default=None, max_length=200, description="Geschäftsführer", examples=["Hans Muster"])
    commercial_register: Optional[str] = Field(default=None, max_length=100, description="BT-30 legal registration ID (Handelsregisternummer)", examples=["HRB 123456"])
    register_court: Optional[str] = Field(default=None, max_length=100, description="Registergericht", examples=["Amtsgericht München"])
    additional_info: Optional[str] = Field(default=None, max_length=1000, description="BT-33 additional legal information (free text)")

    def as_text(self) -> Optional[str]:
        parts = []
        if self.managing_director:
            parts.append(f"Geschäftsführer: {self.managing_director}")
        # After an extract → create round trip the register is already part of additional_info.
        if self.commercial_register and self.commercial_register not in (self.additional_info or ""):
            court = f", {self.register_court}" if self.register_court else ""
            parts.append(f"Handelsregister: {self.commercial_register}{court}")
        if self.additional_info:
            parts.append(self.additional_info)
        return ", ".join(parts) or None


class _Party(_Strict):
    id: Optional[str] = Field(default=None, max_length=100, description="BT-29/BT-46 party identifier (e.g. supplier/customer number)")
    global_id: Optional[str] = Field(default=None, description="BT-29/BT-46 global identifier: GLN (scheme 0088)", examples=["4000001123452"])
    name: str = Field(..., min_length=1, max_length=200, description="BT-27/BT-44 legal name")
    address: Address
    vat_id: Optional[str] = Field(default=None, description="BT-31/BT-48 VAT identifier (USt-IdNr.)", examples=["DE123456789"])
    electronic_address: Optional[str] = Field(
        default=None, max_length=200,
        description="BT-34/BT-49 electronic address (mandatory in XRechnung 3.x). Email or e.g. a Leitweg-ID",
        examples=["rechnung@lieferant.de"],
    )
    electronic_address_scheme: Optional[str] = Field(
        default=None, max_length=10,
        description="EAS scheme of the electronic address. Auto: 'EM' for emails, '0204' for Leitweg-IDs",
    )
    contact: Optional[Contact] = None

    @field_validator("global_id")
    @classmethod
    def check_gln(cls, value: Optional[str]) -> Optional[str]:
        if value and not is_valid_gtin(value):
            raise ValueError("Global ID must be a valid 13-digit GLN (GS1 check digit failed)")
        return value

    @field_validator("vat_id")
    @classmethod
    def check_vat_id(cls, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        vat = compact(value)
        if not vat_id_prefix_ok(vat):
            raise ValueError("VAT identifier must start with an ISO 3166-1 country code (BR-CO-9)")
        if not vat_id_format_ok(vat):
            raise ValueError("Invalid VAT identifier format (German USt-IdNr.: 'DE' + 9 digits)")
        return vat

    @model_validator(mode="after")
    def resolve_scheme(self):
        if self.electronic_address and not self.electronic_address_scheme:
            if "@" in self.electronic_address:
                self.electronic_address_scheme = "EM"
            elif looks_like_leitweg_id(self.electronic_address):
                self.electronic_address_scheme = "0204"
            else:
                raise ValueError("electronic_address_scheme is required when the electronic address is not an email or Leitweg-ID")
        return self


class Seller(_Party):
    """BG-4 Seller (Verkäufer)."""
    trading_name: Optional[str] = Field(default=None, max_length=200, description="BT-28 trading name")
    tax_number: Optional[str] = Field(default=None, max_length=50, description="BT-32 tax registration (Steuernummer)", examples=["201/113/40209"])
    legal: Optional[LegalInfo] = None


class Buyer(_Party):
    """BG-7 Buyer (Käufer)."""
    legal_registration_id: Optional[str] = Field(default=None, max_length=100, description="BT-47 buyer legal registration ID (e.g. commercial register)")
    buyer_reference: Optional[str] = Field(
        default=None, max_length=100,
        description="BT-10 buyer reference. For XRechnung this is the Leitweg-ID and is mandatory (BR-DE-15)",
        examples=["04011000-1234512345-06"],
    )


# ---------------------------------------------------------------------------
# Lines, allowances/charges, VAT, totals, payment
# ---------------------------------------------------------------------------

class InvoiceLine(_Strict):
    """BG-25 Invoice line."""
    position: Optional[str] = Field(default=None, max_length=50, description="BT-126 line ID (auto-numbered when omitted)", examples=["1"])
    description: str = Field(..., min_length=1, max_length=2000, description="BT-153 item name", examples=["Trennblätter A4"])
    item_description: Optional[str] = Field(default=None, max_length=10000, description="BT-154 item description")
    seller_article_number: Optional[str] = Field(default=None, max_length=100, description="BT-155 seller's item ID", examples=["TB100A4"])
    buyer_article_number: Optional[str] = Field(default=None, max_length=100, description="BT-156 buyer's item ID")
    gtin: Optional[str] = Field(default=None, description="BT-157 item standard identifier: GTIN (scheme 0160)", examples=["4012345001235"])
    note: Optional[str] = Field(default=None, max_length=10000, description="BT-127 line note")
    net_price: UnitPrice = Field(..., ge=0, description="BT-146 item net price (BR-27: not negative)", examples=[9.90])
    gross_price: Optional[UnitPrice] = Field(default=None, ge=0, description="BT-148 item gross price before price discount", examples=[9.90])
    price_base_quantity: Optional[Quantity] = Field(default=None, gt=0, description="BT-149 price base quantity (default 1)")
    quantity: Quantity = Field(..., description="BT-129 invoiced quantity", examples=[20])
    unit: str = Field(default="C62", description="BT-130 unit (UN/ECE Rec 20). Aliases: Stk→C62, Std→HUR, kg→KGM …", examples=["C62"])
    vat_category: VatCategory = Field(default=VatCategory.S, description="BT-151 VAT category")
    vat_rate: Rate = Field(..., description="BT-152 VAT rate in percent (19 or 7 in Germany)", examples=[19])
    allowances: List["LineAllowanceCharge"] = Field(default_factory=list, description="BG-27 line allowances (Zeilenrabatt)")
    charges: List["LineAllowanceCharge"] = Field(default_factory=list, description="BG-28 line charges (Zeilenzuschlag)")
    line_net_amount: Optional[Amount] = Field(
        default=None, examples=[198.00],
        description="BT-131 line net amount = quantity × net price ÷ base quantity − line allowances + line charges. Recalculated server-side",
    )

    @field_validator("unit")
    @classmethod
    def check_unit(cls, value: str) -> str:
        code = normalize_unit(value)
        if not code:
            raise ValueError(f"Unknown unit '{value}'. Use a UN/ECE Rec 20 code (C62, HUR, KGM …) or an alias like 'Stk'")
        return code

    @field_validator("gtin")
    @classmethod
    def check_gtin(cls, value: Optional[str]) -> Optional[str]:
        if value and not is_valid_gtin(value):
            raise ValueError("GTIN check digit is invalid")
        return value

    @model_validator(mode="after")
    def check_line(self):
        if self.quantity == 0:
            raise ValueError("quantity must not be 0 (BR-22)")
        if self.vat_category in ZERO_RATE_CATEGORIES and self.vat_rate != 0:
            raise ValueError(f"VAT rate must be 0 for category {self.vat_category.value}")
        _check_category_rate(self.vat_category, self.vat_rate, "05")
        if self.gross_price is not None and self.gross_price < self.net_price:
            raise ValueError("gross_price must be greater than or equal to net_price (net = gross − price discount)")
        return self


_RATE_RULE_PREFIX = {"S": "S", "Z": "Z", "E": "E", "AE": "AE", "K": "IC", "G": "G", "O": "O"}


def _check_category_rate(category: "VatCategory", rate: Decimal, rule_suffix: str) -> None:
    """BR-S-05/06/07: standard rate > 0; BR-Z/E/AE/IC/G/O-05/06/07: rate 0. IGIC (L) / IPSI (M) may be 0 or more."""
    prefix = _RATE_RULE_PREFIX.get(category.value)
    if category.value in ZERO_RATE_CATEGORIES and rate != 0:
        raise ValueError(f"VAT rate must be 0 for category {category.value} (BR-{prefix}-{rule_suffix})")
    if category == VatCategory.S and rate <= 0:
        raise ValueError(f"VAT rate must be greater than 0 for category S (BR-S-{rule_suffix})")


class LineAllowanceCharge(_Strict):
    """BG-27 line allowance / BG-28 line charge (no own VAT category: it inherits the line's)."""
    amount: Amount = Field(..., gt=0, description="BT-136/BT-141 amount")
    reason: Optional[str] = Field(default=None, max_length=1000, description="BT-139/BT-144 reason")
    reason_code: Optional[str] = Field(default=None, max_length=10, description="BT-140/BT-145 reason code")
    base_amount: Optional[Amount] = Field(default=None, description="BT-137/BT-142 base amount")
    percent: Optional[Rate] = Field(default=None, description="BT-138/BT-143 percentage")

    @model_validator(mode="after")
    def check_reason(self):
        if not self.reason and not self.reason_code:
            raise ValueError("reason or reason_code is required (BR-42 / BR-44)")
        return self


InvoiceLine.model_rebuild()


class AllowanceCharge(_Strict):
    """BG-20 document level allowance (Nachlass) / BG-21 charge (Zuschlag)."""
    amount: Amount = Field(..., gt=0, description="BT-92/BT-99 amount (net)")
    reason: Optional[str] = Field(default=None, max_length=200, description="BT-97/BT-104 reason", examples=["Treuerabatt"])
    reason_code: Optional[str] = Field(default=None, max_length=10, description="BT-98/BT-105 reason code (UNTDID 5189 / 7161)")
    base_amount: Optional[Amount] = Field(default=None, description="BT-93/BT-100 base amount")
    percent: Optional[Rate] = Field(default=None, description="BT-94/BT-101 percentage")
    vat_category: VatCategory = Field(default=VatCategory.S, description="BT-95/BT-102 VAT category")
    vat_rate: Rate = Field(..., description="BT-96/BT-103 VAT rate")

    @model_validator(mode="after")
    def check_reason(self):
        if not self.reason and not self.reason_code:
            raise ValueError("reason or reason_code is required (BR-33 / BR-38)")
        _check_category_rate(self.vat_category, self.vat_rate, "06/07")
        return self


class VatBreakdownItem(_Strict):
    """BG-23 VAT breakdown. Calculated server-side; supplied values are checked against the calculation."""
    category: VatCategory = Field(..., description="BT-118 VAT category")
    rate: Rate = Field(..., description="BT-119 VAT rate")
    basis_amount: Amount = Field(..., description="BT-116 taxable amount")
    tax_amount: Amount = Field(..., description="BT-117 tax amount")
    exemption_reason: Optional[str] = Field(default=None, max_length=500, description="BT-120 exemption reason (required for E, AE, K, G, O)")
    exemption_reason_code: Optional[str] = Field(default=None, max_length=30, description="BT-121 exemption reason code (VATEX)")


class Totals(_Strict):
    """BG-22 document totals. All values are recalculated; supplied values must match to the cent."""
    line_total: Optional[Amount] = Field(default=None, description="BT-106 sum of line net amounts")
    allowances: Optional[Amount] = Field(default=None, description="BT-107 sum of allowances")
    charges: Optional[Amount] = Field(default=None, description="BT-108 sum of charges")
    net_total: Optional[Amount] = Field(default=None, description="BT-109 total without VAT")
    vat_total: Optional[Amount] = Field(default=None, description="BT-110 total VAT")
    gross_total: Optional[Amount] = Field(default=None, description="BT-112 total with VAT")
    prepaid_amount: Optional[Amount] = Field(default=None, description="BT-113 paid amount")
    rounding_amount: Optional[Amount] = Field(default=None, description="BT-114 rounding amount")
    payable_amount: Optional[Amount] = Field(default=None, description="BT-115 amount due for payment")


class Payment(_Strict):
    """BG-16 payment instructions and BT-9/BT-20 due date and terms."""
    means_code: Optional[str] = Field(
        default=None,
        description="BT-81 payment means code (UNTDID 4461): 58 SEPA transfer, 59 SEPA direct debit, 30 transfer, 1 not defined … "
                    "Default: 58 when an IBAN is given, 59 when a debtor IBAN is given, otherwise 1",
    )
    due_date: Optional[date] = Field(default=None, description="BT-9 payment due date")
    terms: Optional[str] = Field(default=None, max_length=1000, description="BT-20 payment terms text", examples=["Zahlbar innerhalb 30 Tagen netto"])
    discount_days: Optional[int] = Field(default=None, ge=1, le=365, description="Cash discount (Skonto) period in days")
    discount_percent: Optional[Rate] = Field(default=None, gt=0, description="Cash discount (Skonto) percentage")
    iban: Optional[str] = Field(default=None, description="BT-84 payee IBAN", examples=["DE02120300000000202051"])
    bic: Optional[str] = Field(default=None, description="BT-86 payee BIC", examples=["BYLADEM1001"])
    account_name: Optional[str] = Field(default=None, max_length=200, description="BT-85 account name")
    payment_reference: Optional[str] = Field(default=None, max_length=1000, description="BT-83 remittance information (Verwendungszweck)")
    mandate_reference: Optional[str] = Field(default=None, max_length=100, description="BT-89 SEPA mandate reference (direct debit)")
    creditor_id: Optional[str] = Field(default=None, max_length=100, description="BT-90 SEPA creditor ID (direct debit)")
    debtor_iban: Optional[str] = Field(default=None, description="BT-91 debited account IBAN (direct debit)")

    @field_validator("means_code")
    @classmethod
    def check_means(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not is_payment_means_code(value):
            raise ValueError(f"'{value}' is not a UNTDID 4461 payment means code (BR-CL-16)")
        return value

    @field_validator("iban", "debtor_iban")
    @classmethod
    def check_iban(cls, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        if not is_valid_iban(value):
            raise ValueError("Invalid IBAN (checksum failed) (BR-DE-19)")
        return compact(value)

    @field_validator("bic")
    @classmethod
    def check_bic(cls, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        if not is_valid_bic(value):
            raise ValueError("Invalid BIC format")
        return compact(value)

    @model_validator(mode="after")
    def check_discount(self):
        if (self.discount_days is None) != (self.discount_percent is None):
            raise ValueError("discount_days and discount_percent must be given together")
        if self.means_code is None:
            self.means_code = "58" if self.iban else "59" if self.debtor_iban else "1"
        return self


# ---------------------------------------------------------------------------
# Invoice
# ---------------------------------------------------------------------------

class EInvoiceData(_Strict):
    """Structured invoice input for ZUGFeRD (EN 16931 COMFORT) and XRechnung generation."""
    invoice_number: str = Field(..., min_length=1, max_length=100, description="BT-1 invoice number (BR-02)", examples=["471102"])
    issue_date: date = Field(..., description="BT-2 issue date YYYY-MM-DD (BR-03)", examples=["2018-03-05"])
    type_code: str = Field(default="380", description="BT-3 (UNTDID 1001): 380 invoice, 381 credit note, 384 corrected, 389 self-billed, 326 partial, 875–877 construction …")
    currency: str = Field(default="EUR", description="BT-5 ISO 4217 currency (BR-05)")
    delivery_date: Optional[date] = Field(default=None, description="BT-72 actual delivery date (Leistungsdatum, UStG §14)")
    delivery_name: Optional[str] = Field(default=None, max_length=200, description="BT-70 deliver-to party name")
    delivery_address: Optional[Address] = Field(
        default=None, description="BG-15 deliver-to address. Required for intra-community supplies (BR-IC-12); XRechnung needs city and post code (BR-DE-10/11)",
    )
    billing_period_start: Optional[date] = Field(default=None, description="BT-73 invoicing period start")
    billing_period_end: Optional[date] = Field(default=None, description="BT-74 invoicing period end")
    order_reference: Optional[str] = Field(default=None, max_length=100, description="BT-13 purchase order reference")
    contract_reference: Optional[str] = Field(default=None, max_length=100, description="BT-12 contract reference")
    preceding_invoice_number: Optional[str] = Field(default=None, max_length=100, description="BT-25 preceding invoice (for credit notes / corrections)")
    preceding_invoice_date: Optional[date] = Field(default=None, description="BT-26 preceding invoice date")
    notes: List[str] = Field(default_factory=list, description="BT-22 invoice notes (free text)", examples=[["Rechnung gemäß Bestellung vom 01.03.2018."]])
    seller: Seller
    buyer: Buyer
    lines: List[InvoiceLine] = Field(..., min_length=1, max_length=9999, description="BG-25 invoice lines (BR-16: at least one)")
    allowances: List[AllowanceCharge] = Field(default_factory=list, description="BG-20 document level allowances")
    charges: List[AllowanceCharge] = Field(default_factory=list, description="BG-21 document level charges")
    vat_breakdown: Optional[List[VatBreakdownItem]] = Field(default=None, description="BG-23 VAT breakdown (calculated automatically when omitted)")
    totals: Optional[Totals] = Field(default=None, description="BG-22 totals (calculated automatically when omitted)")
    payment: Optional[Payment] = None

    @field_validator("type_code", mode="before")
    @classmethod
    def check_type_code(cls, value: Any) -> str:
        code = str(value.value if isinstance(value, Enum) else value).strip()
        if code not in INVOICE_TYPE_CODES:
            raise ValueError(f"'{code}' is not an EN 16931 invoice type code (BR-CL-01)")
        return code

    @field_validator("currency")
    @classmethod
    def check_currency(cls, value: str) -> str:
        code = value.strip().upper()
        if code not in CURRENCY_CODES:
            raise ValueError(f"'{value}' is not an ISO 4217 currency code (BR-CL-04)")
        return code

    @field_validator("notes", mode="before")
    @classmethod
    def coerce_notes(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value.strip() else []
        return value

    @model_validator(mode="after")
    def finalize(self):
        if self.billing_period_start and self.billing_period_end and self.billing_period_end < self.billing_period_start:
            raise ValueError("billing_period_end must not be before billing_period_start")
        positions = set()
        for index, line in enumerate(self.lines, start=1):
            if not line.position:
                line.position = str(index)
            if line.position in positions:
                raise ValueError(f"Duplicate line position '{line.position}'")
            positions.add(line.position)
        return self


# ---------------------------------------------------------------------------
# Simplified flat schema for POST /convert (frontend form)
# ---------------------------------------------------------------------------

class FormatType(str, Enum):
    ZUGFERD = "ZUGFeRD"
    XRECHNUNG = "XRechnung"


class FlatLineItem(_Strict):
    item_description: str = Field(..., min_length=1, max_length=500, description="Name of the service/product")
    net_price: float = Field(..., ge=0, description="Price per unit before tax")
    quantity: float = Field(..., description="Number of units")
    unit: str = Field(..., description="e.g. 'Stk' (pieces) or 'Std' (hours)")
    tax_rate: float = Field(..., ge=0, le=100, description="e.g. 19.0 or 7.0")
    line_net_amount: float = Field(..., description="Total net for this line")


class InvoiceDataSchema(_Strict):
    """Flat invoice form used by POST /convert. Addresses are 'Street, Zip City, Country' strings."""
    invoice_number: str = Field(..., min_length=1, max_length=100, examples=["RE-2026-0142"])
    issue_date: date = Field(..., examples=["2026-03-05"])
    format_type: FormatType
    buyer_name: str = Field(..., min_length=1, max_length=200)
    buyer_address: str = Field(..., min_length=1, examples=["Kundenstraße 15, 69876 Frankfurt, DE"])
    routing_id: Optional[str] = Field(default=None, validate_default=True, description="Leitweg-ID, mandatory for XRechnung", examples=["04011000-1234512345-06"])
    seller_name: str = Field(..., min_length=1, max_length=200)
    seller_address: str = Field(..., min_length=1, examples=["Lieferantenstraße 20, 80333 München, DE"])
    seller_vat_id: str = Field(..., examples=["DE123456789"])
    line_items: List[FlatLineItem] = Field(..., min_length=1)
    tax_basis_total: float = Field(..., description="Total net amount (BT-109)")
    tax_total: float = Field(..., description="Total VAT amount (BT-110)")
    grand_total: float = Field(..., description="Total gross amount (BT-112)")
    payment_terms: Optional[str] = Field(default=None, examples=["Zahlbar innerhalb 30 Tagen netto"])
    # Optional extras needed for a valid XRechnung (BR-DE-1, BR-DE-2, BT-34/BT-49)
    currency: str = Field(default="EUR")
    delivery_date: Optional[date] = None
    due_date: Optional[date] = None
    seller_tax_number: Optional[str] = None
    seller_contact_name: Optional[str] = None
    seller_phone: Optional[str] = None
    seller_email: Optional[str] = None
    buyer_email: Optional[str] = None
    iban: Optional[str] = None
    bic: Optional[str] = None

    @field_validator("routing_id")
    @classmethod
    def require_routing_id(cls, value: Optional[str], info: ValidationInfo) -> Optional[str]:
        if info.data.get("format_type") == FormatType.XRECHNUNG and not (value or "").strip():
            raise ValueError("routing_id (Leitweg-ID) is mandatory when format_type is XRechnung (BR-DE-15)")
        return value


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------

class ValidationIssue(BaseModel):
    rule_id: str = Field(..., description="EN 16931 / XRechnung rule ID (e.g. BR-CO-15, BR-DE-15) or check ID (XSD, PDF-…)")
    field: Optional[str] = Field(default=None, description="JSON field path or XPath of the offending element")
    message: str
    severity: Literal["error", "warning"] = "error"
    source: Literal["input", "xsd", "business-rules", "kosit", "pdf", "xml"] = "business-rules"
    line: Optional[int] = Field(default=None, description="Line number in the XML (XSD/KoSIT findings)")
    expected: Optional[str] = None
    actual: Optional[str] = None


class ValidationReport(BaseModel):
    valid: bool
    syntax: Optional[str] = Field(default=None, description="CII, UBL or ZUGFeRD-1.0")
    profile: Optional[str] = Field(default=None, description="Detected profile, e.g. 'EN 16931 (COMFORT)' or 'XRECHNUNG'")
    profile_urn: Optional[str] = Field(default=None, description="Specification identifier BT-24")
    version: Optional[str] = Field(default=None, description="ZUGFeRD / XRechnung version")
    errors: List[ValidationIssue] = Field(default_factory=list)
    warnings: List[ValidationIssue] = Field(default_factory=list)
    checks: Dict[str, str] = Field(default_factory=dict, description="Per-check status: passed | failed | skipped")
    validated_at: datetime


class ArchiveFileInfo(BaseModel):
    role: str = Field(..., description="original_pdf | input_json | uploaded_document | generated_pdf | generated_xml")
    filename: str
    sha256: str
    size: int
    content_type: str


class ArchiveInfo(BaseModel):
    archive_id: str
    archived_at: datetime
    entry_hash: str = Field(..., description="SHA-256 of the ledger entry (hash-chained to the previous entry)")
    files: List[ArchiveFileInfo]


class ArchiveVerification(ArchiveInfo):
    operation: str
    invoice_number: Optional[str] = None
    intact: bool = Field(..., description="True when every file and the ledger entry still match their recorded hashes")
    problems: List[str] = Field(default_factory=list)


class CreateZugferdResponse(BaseModel):
    """JSON variant of POST /create-zugferd (?output=json)."""
    filename: str
    pdf_base64: str
    xml: str
    validation: ValidationReport
    archive: Optional[ArchiveInfo] = None


class CreateXRechnungResponse(BaseModel):
    """JSON variant of POST /create-xrechnung (?output=json)."""
    filename: str
    xml: str
    validation: ValidationReport
    archive: Optional[ArchiveInfo] = None


class ExtractResponse(BaseModel):
    filename: str
    source_type: Literal["pdf", "xml"]
    syntax: Optional[str] = None
    profile: Optional[str] = None
    profile_urn: Optional[str] = None
    version: Optional[str] = None
    embedded_filename: Optional[str] = Field(default=None, description="Name of the XML attachment inside the PDF")
    xmp: Optional[Dict[str, Optional[str]]] = Field(default=None, description="ZUGFeRD/Factur-X and PDF/A XMP metadata")
    invoice: Optional[Dict[str, Any]] = Field(default=None, description="Parsed invoice in the same JSON shape as the create endpoints accept")
    raw_xml: str
    warnings: List[ValidationIssue] = Field(default_factory=list)
    archive: Optional[ArchiveInfo] = None
