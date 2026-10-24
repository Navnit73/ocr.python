"""
Secure XML parsing and mapping of CII (ZUGFeRD 2.x / Factur-X / XRechnung-CII) and UBL (XRechnung-UBL)
documents back into the EInvoiceData JSON shape used by the create endpoints.
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from lxml import etree

from app.services.einvoice.codes import detect_profile

CII_NS = {
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
    "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
    "qdt": "urn:un:unece:uncefact:data:standard:QualifiedDataType:100",
}
ZUGFERD1_NS = {
    "rsm": "urn:ferd:CrossIndustryDocument:invoice:1p0",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:12",
}
UBL_INVOICE_NS = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
UBL_CREDIT_NOTE_NS = "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2"
UBL_NS = {
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
}

_SKONTO = re.compile(r"#SKONTO#TAGE=(\d+)#PROZENT=(\d+(?:\.\d+)?)#")


class UnsafeXMLError(ValueError):
    """Raised for XML that is not well-formed or uses DTDs/entities (XXE, entity expansion)."""


def parse_xml_secure(content: bytes) -> etree._Element:
    head = content[:4096].lstrip(b"\xef\xbb\xbf \t\r\n")
    # Scan the whole document: a DOCTYPE may follow an arbitrarily long prolog of comments/whitespace.
    upper = content.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise UnsafeXMLError("DOCTYPE and entity declarations are not allowed in e-invoice XML")
    if not head.startswith(b"<"):
        raise UnsafeXMLError("Content is not XML")
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False, remove_comments=True)
    try:
        return etree.fromstring(content, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise UnsafeXMLError(f"XML is not well-formed: {exc}") from exc


@dataclass
class DocumentInfo:
    syntax: str                 # CII | UBL | ZUGFeRD-1.0 | UNKNOWN
    profile: str
    profile_urn: Optional[str]
    xrechnung_version: Optional[str]


def detect_document(root: etree._Element) -> DocumentInfo:
    qname = etree.QName(root)
    if qname.namespace == CII_NS["rsm"] and qname.localname == "CrossIndustryInvoice":
        urn = _text(root, "rsm:ExchangedDocumentContext/ram:GuidelineSpecifiedDocumentContextParameter/ram:ID", CII_NS)
        profile, version = detect_profile(urn)
        return DocumentInfo("CII", profile, urn, version)
    if qname.namespace == ZUGFERD1_NS["rsm"]:
        urn = _text(root, "rsm:SpecifiedExchangedDocumentContext/ram:GuidelineSpecifiedDocumentContextParameter/ram:ID", ZUGFERD1_NS)
        profile, _ = detect_profile(urn)
        return DocumentInfo("ZUGFeRD-1.0", profile, urn, None)
    if qname.namespace in (UBL_INVOICE_NS, UBL_CREDIT_NOTE_NS):
        urn = _text(root, "cbc:CustomizationID", UBL_NS)
        profile, version = detect_profile(urn)
        return DocumentInfo("UBL", profile, urn, version)
    return DocumentInfo("UNKNOWN", "UNKNOWN", None, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _text(node: Optional[etree._Element], path: str, ns: Dict[str, str]) -> Optional[str]:
    if node is None:
        return None
    found = node.find(path, ns)
    if found is None or found.text is None:
        return None
    return found.text.strip() or None


def _attr(node: Optional[etree._Element], path: str, attr: str, ns: Dict[str, str]) -> Optional[str]:
    if node is None:
        return None
    found = node.find(path, ns)
    return found.get(attr) if found is not None else None


def _cii_date(node: Optional[etree._Element], path: str) -> Optional[str]:
    value = _text(node, path, CII_NS)
    if value and re.fullmatch(r"\d{8}", value):
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value


def _any(d: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return d if any(v is not None for v in d.values()) else None


def _split_allowance_charges(nodes, is_charge, to_dict) -> Dict[str, List[Dict[str, Any]]]:
    result: Dict[str, List[Dict[str, Any]]] = {"allowances": [], "charges": []}
    for node in nodes:
        result["charges" if is_charge(node) else "allowances"].append(to_dict(node))
    return result


def split_payment_terms(text: Optional[str]) -> Dict[str, Any]:
    """Separates the XRechnung '#SKONTO#' segment from free-text payment terms."""
    if not text:
        return {"terms": None, "discount_days": None, "discount_percent": None}
    match = _SKONTO.search(text)
    # Only the first #SKONTO# segment maps to discount_days/percent; any further segments stay in the text.
    lines = [line for line in text.splitlines() if line.strip()]
    if match:
        lines.remove(next(line for line in lines if match.group(0) in line))
    free = "\n".join(lines).strip()
    return {
        "terms": free or None,
        "discount_days": int(match.group(1)) if match else None,
        "discount_percent": match.group(2) if match else None,
    }


# ---------------------------------------------------------------------------
# CII
# ---------------------------------------------------------------------------

def _cii_address(address: Optional[etree._Element]) -> Optional[Dict[str, Any]]:
    if address is None:
        return None
    ns = CII_NS
    return {
        "street": _text(address, "ram:LineOne", ns),
        "additional_street": _text(address, "ram:LineTwo", ns),
        "address_line_3": _text(address, "ram:LineThree", ns),
        "postcode": _text(address, "ram:PostcodeCode", ns),
        "city": _text(address, "ram:CityName", ns),
        "country": _text(address, "ram:CountryID", ns),
        "country_subdivision": _text(address, "ram:CountrySubDivisionName", ns),
    }


def _cii_party(node: Optional[etree._Element], seller: bool) -> Optional[Dict[str, Any]]:
    if node is None:
        return None
    ns = CII_NS
    global_id = None
    for gid in node.findall("ram:GlobalID", ns):
        if gid.get("schemeID") == "0088" and gid.text:
            global_id = gid.text.strip()
    vat_id = tax_number = None
    for reg in node.findall("ram:SpecifiedTaxRegistration/ram:ID", ns):
        if reg.get("schemeID") == "VA":
            vat_id = (reg.text or "").strip() or None
        elif reg.get("schemeID") == "FC":
            tax_number = (reg.text or "").strip() or None
    party: Dict[str, Any] = {
        "id": _text(node, "ram:ID", ns),
        "global_id": global_id,
        "name": _text(node, "ram:Name", ns),
        "address": _cii_address(node.find("ram:PostalTradeAddress", ns)) or {"country": None},
        "vat_id": vat_id,
        "electronic_address": _text(node, "ram:URIUniversalCommunication/ram:URIID", ns),
        "electronic_address_scheme": _attr(node, "ram:URIUniversalCommunication/ram:URIID", "schemeID", ns),
        "contact": _any({
            "name": _text(node, "ram:DefinedTradeContact/ram:PersonName", ns),
            "phone": _text(node, "ram:DefinedTradeContact/ram:TelephoneUniversalCommunication/ram:CompleteNumber", ns),
            "email": _text(node, "ram:DefinedTradeContact/ram:EmailURIUniversalCommunication/ram:URIID", ns),
        }),
    }
    if seller:
        party["tax_number"] = tax_number
        party["trading_name"] = _text(node, "ram:SpecifiedLegalOrganization/ram:TradingBusinessName", ns)
        party["legal"] = _any({
            "commercial_register": _text(node, "ram:SpecifiedLegalOrganization/ram:ID", ns),
            "additional_info": _text(node, "ram:Description", ns),
        })
    return party


def _cii_allowance_charge(node: etree._Element) -> Dict[str, Any]:
    ns = CII_NS
    return {
        "amount": _text(node, "ram:ActualAmount", ns),
        "reason": _text(node, "ram:Reason", ns),
        "reason_code": _text(node, "ram:ReasonCode", ns),
        "base_amount": _text(node, "ram:BasisAmount", ns),
        "percent": _text(node, "ram:CalculationPercent", ns),
        "vat_category": _text(node, "ram:CategoryTradeTax/ram:CategoryCode", ns),
        "vat_rate": _text(node, "ram:CategoryTradeTax/ram:RateApplicablePercent", ns) or "0",
    }


def parse_cii(root: etree._Element) -> Dict[str, Any]:
    ns = CII_NS
    doc = root.find("rsm:ExchangedDocument", ns)
    tx = root.find("rsm:SupplyChainTradeTransaction", ns)
    agreement = tx.find("ram:ApplicableHeaderTradeAgreement", ns) if tx is not None else None
    delivery = tx.find("ram:ApplicableHeaderTradeDelivery", ns) if tx is not None else None
    settlement = tx.find("ram:ApplicableHeaderTradeSettlement", ns) if tx is not None else None
    currency = _text(settlement, "ram:InvoiceCurrencyCode", ns)

    lines: List[Dict[str, Any]] = []
    for item in (tx.findall("ram:IncludedSupplyChainTradeLineItem", ns) if tx is not None else []):
        gtin = None
        for gid in item.findall("ram:SpecifiedTradeProduct/ram:GlobalID", ns):
            if gid.get("schemeID") == "0160" and gid.text:
                gtin = gid.text.strip()
        lines.append({
            "position": _text(item, "ram:AssociatedDocumentLineDocument/ram:LineID", ns),
            "note": _text(item, "ram:AssociatedDocumentLineDocument/ram:IncludedNote/ram:Content", ns),
            "description": _text(item, "ram:SpecifiedTradeProduct/ram:Name", ns),
            "item_description": _text(item, "ram:SpecifiedTradeProduct/ram:Description", ns),
            "seller_article_number": _text(item, "ram:SpecifiedTradeProduct/ram:SellerAssignedID", ns),
            "buyer_article_number": _text(item, "ram:SpecifiedTradeProduct/ram:BuyerAssignedID", ns),
            "gtin": gtin,
            "gross_price": _text(item, "ram:SpecifiedLineTradeAgreement/ram:GrossPriceProductTradePrice/ram:ChargeAmount", ns),
            "net_price": _text(item, "ram:SpecifiedLineTradeAgreement/ram:NetPriceProductTradePrice/ram:ChargeAmount", ns),
            "price_base_quantity": _text(item, "ram:SpecifiedLineTradeAgreement/ram:NetPriceProductTradePrice/ram:BasisQuantity", ns),
            "quantity": _text(item, "ram:SpecifiedLineTradeDelivery/ram:BilledQuantity", ns),
            "unit": _attr(item, "ram:SpecifiedLineTradeDelivery/ram:BilledQuantity", "unitCode", ns) or "C62",
            "vat_category": _text(item, "ram:SpecifiedLineTradeSettlement/ram:ApplicableTradeTax/ram:CategoryCode", ns),
            "vat_rate": _text(item, "ram:SpecifiedLineTradeSettlement/ram:ApplicableTradeTax/ram:RateApplicablePercent", ns) or "0",
            "line_net_amount": _text(item, "ram:SpecifiedLineTradeSettlement/ram:SpecifiedTradeSettlementLineMonetarySummation/ram:LineTotalAmount", ns),
            **_split_allowance_charges(
                item.findall("ram:SpecifiedLineTradeSettlement/ram:SpecifiedTradeAllowanceCharge", ns),
                lambda n: (_text(n, "ram:ChargeIndicator/udt:Indicator", ns) or "").lower() == "true",
                lambda n: {
                    "amount": _text(n, "ram:ActualAmount", ns), "reason": _text(n, "ram:Reason", ns),
                    "reason_code": _text(n, "ram:ReasonCode", ns), "base_amount": _text(n, "ram:BasisAmount", ns),
                    "percent": _text(n, "ram:CalculationPercent", ns),
                },
            ),
        })

    allowances, charges = [], []
    for node in (settlement.findall("ram:SpecifiedTradeAllowanceCharge", ns) if settlement is not None else []):
        is_charge = (_text(node, "ram:ChargeIndicator/udt:Indicator", ns) or "").lower() == "true"
        (charges if is_charge else allowances).append(_cii_allowance_charge(node))

    vat_breakdown = [
        {
            "category": _text(tax, "ram:CategoryCode", ns),
            "rate": _text(tax, "ram:RateApplicablePercent", ns) or "0",
            "basis_amount": _text(tax, "ram:BasisAmount", ns),
            "tax_amount": _text(tax, "ram:CalculatedAmount", ns),
            "exemption_reason": _text(tax, "ram:ExemptionReason", ns),
            "exemption_reason_code": _text(tax, "ram:ExemptionReasonCode", ns),
        }
        for tax in (settlement.findall("ram:ApplicableTradeTax", ns) if settlement is not None else [])
    ]

    summation = settlement.find("ram:SpecifiedTradeSettlementHeaderMonetarySummation", ns) if settlement is not None else None
    vat_total = None
    for amount in (summation.findall("ram:TaxTotalAmount", ns) if summation is not None else []):
        if amount.get("currencyID") in (None, currency):
            vat_total = (amount.text or "").strip()
            break
    totals = {
        "line_total": _text(summation, "ram:LineTotalAmount", ns),
        "charges": _text(summation, "ram:ChargeTotalAmount", ns),
        "allowances": _text(summation, "ram:AllowanceTotalAmount", ns),
        "net_total": _text(summation, "ram:TaxBasisTotalAmount", ns),
        "vat_total": vat_total,
        "gross_total": _text(summation, "ram:GrandTotalAmount", ns),
        "prepaid_amount": _text(summation, "ram:TotalPrepaidAmount", ns),
        "rounding_amount": _text(summation, "ram:RoundingAmount", ns),
        "payable_amount": _text(summation, "ram:DuePayableAmount", ns),
    }

    means = settlement.find("ram:SpecifiedTradeSettlementPaymentMeans", ns) if settlement is not None else None
    terms = settlement.find("ram:SpecifiedTradePaymentTerms", ns) if settlement is not None else None
    payment = _any({
        "means_code": _text(means, "ram:TypeCode", ns),
        "iban": _text(means, "ram:PayeePartyCreditorFinancialAccount/ram:IBANID", ns),
        "account_name": _text(means, "ram:PayeePartyCreditorFinancialAccount/ram:AccountName", ns),
        "bic": _text(means, "ram:PayeeSpecifiedCreditorFinancialInstitution/ram:BICID", ns),
        "debtor_iban": _text(means, "ram:PayerPartyDebtorFinancialAccount/ram:IBANID", ns),
        "payment_reference": _text(settlement, "ram:PaymentReference", ns),
        "creditor_id": _text(settlement, "ram:CreditorReferenceID", ns),
        "due_date": _cii_date(terms, "ram:DueDateDateTime/udt:DateTimeString"),
        "mandate_reference": _text(terms, "ram:DirectDebitMandateID", ns),
        **split_payment_terms(_text(terms, "ram:Description", ns)),
    })
    if payment is not None and payment["means_code"] is None:
        payment["means_code"] = "1"

    buyer = _cii_party(agreement.find("ram:BuyerTradeParty", ns) if agreement is not None else None, seller=False)
    if buyer is not None:
        buyer["buyer_reference"] = _text(agreement, "ram:BuyerReference", ns)
        buyer["legal_registration_id"] = _text(agreement, "ram:BuyerTradeParty/ram:SpecifiedLegalOrganization/ram:ID", ns)

    return {
        "invoice_number": _text(doc, "ram:ID", ns),
        "issue_date": _cii_date(doc, "ram:IssueDateTime/udt:DateTimeString"),
        "type_code": _text(doc, "ram:TypeCode", ns),
        "currency": currency,
        "delivery_date": _cii_date(delivery, "ram:ActualDeliverySupplyChainEvent/ram:OccurrenceDateTime/udt:DateTimeString"),
        "delivery_name": _text(delivery, "ram:ShipToTradeParty/ram:Name", ns),
        "delivery_address": _cii_address(delivery.find("ram:ShipToTradeParty/ram:PostalTradeAddress", ns) if delivery is not None else None),
        "billing_period_start": _cii_date(settlement, "ram:BillingSpecifiedPeriod/ram:StartDateTime/udt:DateTimeString"),
        "billing_period_end": _cii_date(settlement, "ram:BillingSpecifiedPeriod/ram:EndDateTime/udt:DateTimeString"),
        "order_reference": _text(agreement, "ram:BuyerOrderReferencedDocument/ram:IssuerAssignedID", ns),
        "contract_reference": _text(agreement, "ram:ContractReferencedDocument/ram:IssuerAssignedID", ns),
        "preceding_invoice_number": _text(settlement, "ram:InvoiceReferencedDocument/ram:IssuerAssignedID", ns),
        "preceding_invoice_date": _cii_date(settlement, "ram:InvoiceReferencedDocument/ram:FormattedIssueDateTime/qdt:DateTimeString"),
        "notes": [n.text.strip() for n in (doc.findall("ram:IncludedNote/ram:Content", ns) if doc is not None else []) if n.text and n.text.strip()],
        "seller": _cii_party(agreement.find("ram:SellerTradeParty", ns) if agreement is not None else None, seller=True),
        "buyer": buyer,
        "lines": lines,
        "allowances": allowances,
        "charges": charges,
        "vat_breakdown": vat_breakdown,
        "totals": totals,
        "payment": payment,
    }


# ---------------------------------------------------------------------------
# UBL (XRechnung UBL syntax)
# ---------------------------------------------------------------------------

def _ubl_address(address: Optional[etree._Element]) -> Optional[Dict[str, Any]]:
    if address is None:
        return None
    ns = UBL_NS
    return {
        "street": _text(address, "cbc:StreetName", ns),
        "additional_street": _text(address, "cbc:AdditionalStreetName", ns),
        "address_line_3": _text(address, "cac:AddressLine/cbc:Line", ns),
        "postcode": _text(address, "cbc:PostalZone", ns),
        "city": _text(address, "cbc:CityName", ns),
        "country": _text(address, "cac:Country/cbc:IdentificationCode", ns),
        "country_subdivision": _text(address, "cbc:CountrySubentity", ns),
    }


def _ubl_party(node: Optional[etree._Element], seller: bool) -> Optional[Dict[str, Any]]:
    if node is None:
        return None
    ns = UBL_NS
    party_id = global_id = None
    for pid in node.findall("cac:PartyIdentification/cbc:ID", ns):
        if pid.get("schemeID") == "0088":
            global_id = (pid.text or "").strip() or None
        elif pid.get("schemeID") == "SEPA":
            continue  # BT-90 creditor identifier, mapped into payment.creditor_id
        elif party_id is None:
            party_id = (pid.text or "").strip() or None
    vat_id = tax_number = None
    for scheme in node.findall("cac:PartyTaxScheme", ns):
        if _text(scheme, "cac:TaxScheme/cbc:ID", ns) == "VAT":
            vat_id = _text(scheme, "cbc:CompanyID", ns)
        else:
            tax_number = _text(scheme, "cbc:CompanyID", ns)
    party: Dict[str, Any] = {
        "id": party_id,
        "global_id": global_id,
        "name": _text(node, "cac:PartyLegalEntity/cbc:RegistrationName", ns) or _text(node, "cac:PartyName/cbc:Name", ns),
        "address": _ubl_address(node.find("cac:PostalAddress", ns)) or {"country": None},
        "vat_id": vat_id,
        "electronic_address": _text(node, "cbc:EndpointID", ns),
        "electronic_address_scheme": _attr(node, "cbc:EndpointID", "schemeID", ns),
        "contact": _any({
            "name": _text(node, "cac:Contact/cbc:Name", ns),
            "phone": _text(node, "cac:Contact/cbc:Telephone", ns),
            "email": _text(node, "cac:Contact/cbc:ElectronicMail", ns),
        }),
    }
    if seller:
        party["tax_number"] = tax_number
        trading = _text(node, "cac:PartyName/cbc:Name", ns)
        party["trading_name"] = trading if trading != party["name"] else None
        party["legal"] = _any({
            "commercial_register": _text(node, "cac:PartyLegalEntity/cbc:CompanyID", ns),
            "additional_info": _text(node, "cac:PartyLegalEntity/cbc:CompanyLegalForm", ns),
        })
    return party


def parse_ubl(root: etree._Element) -> Dict[str, Any]:
    ns = UBL_NS
    credit_note = etree.QName(root).namespace == UBL_CREDIT_NOTE_NS
    line_tag, qty_tag = ("cac:CreditNoteLine", "cbc:CreditedQuantity") if credit_note else ("cac:InvoiceLine", "cbc:InvoicedQuantity")
    currency = _text(root, "cbc:DocumentCurrencyCode", ns)

    lines = []
    for item in root.findall(line_tag, ns):
        gtin = None
        for sid in item.findall("cac:Item/cac:StandardItemIdentification/cbc:ID", ns):
            if sid.get("schemeID") == "0160":
                gtin = (sid.text or "").strip() or None
        lines.append({
            "position": _text(item, "cbc:ID", ns),
            "note": _text(item, "cbc:Note", ns),
            "description": _text(item, "cac:Item/cbc:Name", ns),
            "item_description": _text(item, "cac:Item/cbc:Description", ns),
            "seller_article_number": _text(item, "cac:Item/cac:SellersItemIdentification/cbc:ID", ns),
            "buyer_article_number": _text(item, "cac:Item/cac:BuyersItemIdentification/cbc:ID", ns),
            "gtin": gtin,
            "net_price": _text(item, "cac:Price/cbc:PriceAmount", ns),
            "gross_price": _text(item, "cac:Price/cac:AllowanceCharge/cbc:BaseAmount", ns),
            "price_base_quantity": _text(item, "cac:Price/cbc:BaseQuantity", ns),
            "quantity": _text(item, qty_tag, ns),
            "unit": _attr(item, qty_tag, "unitCode", ns) or "C62",
            "vat_category": _text(item, "cac:Item/cac:ClassifiedTaxCategory/cbc:ID", ns),
            "vat_rate": _text(item, "cac:Item/cac:ClassifiedTaxCategory/cbc:Percent", ns) or "0",
            "line_net_amount": _text(item, "cbc:LineExtensionAmount", ns),
            **_split_allowance_charges(
                item.findall("cac:AllowanceCharge", ns),
                lambda n: (_text(n, "cbc:ChargeIndicator", ns) or "").lower() == "true",
                lambda n: {
                    "amount": _text(n, "cbc:Amount", ns), "reason": _text(n, "cbc:AllowanceChargeReason", ns),
                    "reason_code": _text(n, "cbc:AllowanceChargeReasonCode", ns), "base_amount": _text(n, "cbc:BaseAmount", ns),
                    "percent": _text(n, "cbc:MultiplierFactorNumeric", ns),
                },
            ),
        })

    allowances, charges = [], []
    for node in root.findall("cac:AllowanceCharge", ns):
        entry = {
            "amount": _text(node, "cbc:Amount", ns),
            "reason": _text(node, "cbc:AllowanceChargeReason", ns),
            "reason_code": _text(node, "cbc:AllowanceChargeReasonCode", ns),
            "base_amount": _text(node, "cbc:BaseAmount", ns),
            "percent": _text(node, "cbc:MultiplierFactorNumeric", ns),
            "vat_category": _text(node, "cac:TaxCategory/cbc:ID", ns),
            "vat_rate": _text(node, "cac:TaxCategory/cbc:Percent", ns) or "0",
        }
        is_charge = (_text(node, "cbc:ChargeIndicator", ns) or "").lower() == "true"
        (charges if is_charge else allowances).append(entry)

    vat_total = None
    for tax_total in root.findall("cac:TaxTotal", ns):
        amount = tax_total.find("cbc:TaxAmount", ns)
        if amount is not None and amount.get("currencyID") in (None, currency):
            vat_total = (amount.text or "").strip()
            break
    vat_breakdown = [
        {
            "category": _text(sub, "cac:TaxCategory/cbc:ID", ns),
            "rate": _text(sub, "cac:TaxCategory/cbc:Percent", ns) or "0",
            "basis_amount": _text(sub, "cbc:TaxableAmount", ns),
            "tax_amount": _text(sub, "cbc:TaxAmount", ns),
            "exemption_reason": _text(sub, "cac:TaxCategory/cbc:TaxExemptionReason", ns),
            "exemption_reason_code": _text(sub, "cac:TaxCategory/cbc:TaxExemptionReasonCode", ns),
        }
        for sub in root.findall("cac:TaxTotal/cac:TaxSubtotal", ns)
    ]
    monetary = root.find("cac:LegalMonetaryTotal", ns)
    totals = {
        "line_total": _text(monetary, "cbc:LineExtensionAmount", ns),
        "allowances": _text(monetary, "cbc:AllowanceTotalAmount", ns),
        "charges": _text(monetary, "cbc:ChargeTotalAmount", ns),
        "net_total": _text(monetary, "cbc:TaxExclusiveAmount", ns),
        "vat_total": vat_total,
        "gross_total": _text(monetary, "cbc:TaxInclusiveAmount", ns),
        "prepaid_amount": _text(monetary, "cbc:PrepaidAmount", ns),
        "rounding_amount": _text(monetary, "cbc:PayableRoundingAmount", ns),
        "payable_amount": _text(monetary, "cbc:PayableAmount", ns),
    }

    means = root.find("cac:PaymentMeans", ns)
    payment = _any({
        "means_code": _text(means, "cbc:PaymentMeansCode", ns),
        "payment_reference": _text(means, "cbc:PaymentID", ns),
        "iban": _text(means, "cac:PayeeFinancialAccount/cbc:ID", ns),
        "account_name": _text(means, "cac:PayeeFinancialAccount/cbc:Name", ns),
        "bic": _text(means, "cac:PayeeFinancialAccount/cac:FinancialInstitutionBranch/cbc:ID", ns),
        "mandate_reference": _text(means, "cac:PaymentMandate/cbc:ID", ns),
        "debtor_iban": _text(means, "cac:PaymentMandate/cac:PayerFinancialAccount/cbc:ID", ns),
        "due_date": _text(root, "cbc:DueDate", ns) or _text(means, "cbc:PaymentDueDate", ns),
        **split_payment_terms(_text(root, "cac:PaymentTerms/cbc:Note", ns)),
    })
    if payment is not None and payment["means_code"] is None:
        payment["means_code"] = "1"

    buyer = _ubl_party(root.find("cac:AccountingCustomerParty/cac:Party", ns), seller=False)
    if buyer is not None:
        buyer["buyer_reference"] = _text(root, "cbc:BuyerReference", ns)
        buyer["legal_registration_id"] = _text(root, "cac:AccountingCustomerParty/cac:Party/cac:PartyLegalEntity/cbc:CompanyID", ns)
    creditor_id = None
    for path in ("cac:PayeeParty/cac:PartyIdentification/cbc:ID", "cac:AccountingSupplierParty/cac:Party/cac:PartyIdentification/cbc:ID"):
        for pid in root.findall(path, ns):
            if pid.get("schemeID") == "SEPA" and creditor_id is None:
                creditor_id = (pid.text or "").strip() or None
    if payment is not None:
        payment["creditor_id"] = creditor_id

    return {
        "invoice_number": _text(root, "cbc:ID", ns),
        "issue_date": _text(root, "cbc:IssueDate", ns),
        "type_code": _text(root, "cbc:CreditNoteTypeCode" if credit_note else "cbc:InvoiceTypeCode", ns),
        "currency": currency,
        "delivery_date": _text(root, "cac:Delivery/cbc:ActualDeliveryDate", ns),
        "delivery_name": _text(root, "cac:Delivery/cac:DeliveryParty/cac:PartyName/cbc:Name", ns),
        "delivery_address": _ubl_address(root.find("cac:Delivery/cac:DeliveryLocation/cac:Address", ns)),
        "billing_period_start": _text(root, "cac:InvoicePeriod/cbc:StartDate", ns),
        "billing_period_end": _text(root, "cac:InvoicePeriod/cbc:EndDate", ns),
        "order_reference": _text(root, "cac:OrderReference/cbc:ID", ns),
        "contract_reference": _text(root, "cac:ContractDocumentReference/cbc:ID", ns),
        "preceding_invoice_number": _text(root, "cac:BillingReference/cac:InvoiceDocumentReference/cbc:ID", ns),
        "preceding_invoice_date": _text(root, "cac:BillingReference/cac:InvoiceDocumentReference/cbc:IssueDate", ns),
        "notes": [n.text.strip() for n in root.findall("cbc:Note", ns) if n.text and n.text.strip()],
        "seller": _ubl_party(root.find("cac:AccountingSupplierParty/cac:Party", ns), seller=True),
        "buyer": buyer,
        "lines": lines,
        "allowances": allowances,
        "charges": charges,
        "vat_breakdown": vat_breakdown,
        "totals": totals,
        "payment": payment,
    }


def parse_invoice(root: etree._Element, info: DocumentInfo) -> Optional[Dict[str, Any]]:
    """Maps a CII or UBL document into the EInvoiceData JSON shape. ZUGFeRD 1.0 is detected but not mapped."""
    if info.syntax == "CII":
        return parse_cii(root)
    if info.syntax == "UBL":
        return parse_ubl(root)
    return None
