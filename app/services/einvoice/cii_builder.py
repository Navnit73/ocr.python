"""
UN/CEFACT Cross Industry Invoice (CII D16B) generator for ZUGFeRD 2.x EN 16931 (COMFORT) and XRechnung (CII syntax).
Element order follows the Factur-X EN16931 XSD; the generated XML is XSD-validated before it is returned.
"""

from datetime import date
from decimal import Decimal
from typing import Optional

from lxml import etree

from app.schemas.einvoice import Address, AllowanceCharge, EInvoiceData, EInvoiceProfile, Payment, _Party
from app.services.einvoice.codes import (
    DIRECT_DEBIT_MEANS,
    URN_EN16931,
    URN_PEPPOL_BILLING,
    fmt_amount,
    fmt_decimal,
    xrechnung_urn,
)

NS = {
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
    "qdt": "urn:un:unece:uncefact:data:standard:QualifiedDataType:100",
    "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
}


def _q(tag: str) -> str:
    prefix, local = tag.split(":")
    return f"{{{NS[prefix]}}}{local}"


def _sub(parent: etree._Element, tag: str, text: Optional[str] = None, **attrs: str) -> etree._Element:
    element = etree.SubElement(parent, _q(tag), {k: v for k, v in attrs.items() if v is not None})
    if text is not None:
        element.text = text
    return element


def _date(parent: etree._Element, tag: str, value: date, qualified: bool = False) -> None:
    wrapper = _sub(parent, tag)
    _sub(wrapper, "qdt:DateTimeString" if qualified else "udt:DateTimeString", value.strftime("%Y%m%d"), format="102")


def skonto_line(days: int, percent: Decimal) -> str:
    """XRechnung structured cash discount (BR-DE-18): #SKONTO#TAGE=10#PROZENT=3.00#"""
    return f"#SKONTO#TAGE={days}#PROZENT={fmt_amount(percent)}#"


def payment_terms_text(payment: Payment) -> Optional[str]:
    if payment.discount_days is None or payment.discount_percent is None:
        return payment.terms
    # The structured Skonto segment must be terminated by a line break; free text may follow.
    return "\n".join(filter(None, [skonto_line(payment.discount_days, payment.discount_percent), payment.terms])) + "\n"


def _address(parent: etree._Element, address: Address) -> None:
    node = _sub(parent, "ram:PostalTradeAddress")
    if address.postcode:
        _sub(node, "ram:PostcodeCode", address.postcode)
    if address.street:
        _sub(node, "ram:LineOne", address.street)
    if address.additional_street:
        _sub(node, "ram:LineTwo", address.additional_street)
    if address.address_line_3:
        _sub(node, "ram:LineThree", address.address_line_3)
    if address.city:
        _sub(node, "ram:CityName", address.city)
    _sub(node, "ram:CountryID", address.country)
    if address.country_subdivision:
        _sub(node, "ram:CountrySubDivisionName", address.country_subdivision)


def _party(parent: etree._Element, tag: str, party: _Party, legal_text: Optional[str] = None,
           register_id: Optional[str] = None, tax_number: Optional[str] = None) -> None:
    node = _sub(parent, tag)
    if party.id:
        _sub(node, "ram:ID", party.id)
    if party.global_id:
        _sub(node, "ram:GlobalID", party.global_id, schemeID="0088")
    _sub(node, "ram:Name", party.name)
    if legal_text:
        _sub(node, "ram:Description", legal_text)
    trading_name = getattr(party, "trading_name", None)
    register_id = register_id or getattr(party, "legal_registration_id", None)
    if register_id or trading_name:
        legal = _sub(node, "ram:SpecifiedLegalOrganization")
        if register_id:
            _sub(legal, "ram:ID", register_id)
        if trading_name:
            _sub(legal, "ram:TradingBusinessName", trading_name)
    if party.contact and (party.contact.name or party.contact.phone or party.contact.email):
        contact = _sub(node, "ram:DefinedTradeContact")
        if party.contact.name:
            _sub(contact, "ram:PersonName", party.contact.name)
        if party.contact.phone:
            _sub(_sub(contact, "ram:TelephoneUniversalCommunication"), "ram:CompleteNumber", party.contact.phone)
        if party.contact.email:
            _sub(_sub(contact, "ram:EmailURIUniversalCommunication"), "ram:URIID", party.contact.email)
    _address(node, party.address)
    if party.electronic_address:
        _sub(_sub(node, "ram:URIUniversalCommunication"), "ram:URIID", party.electronic_address, schemeID=party.electronic_address_scheme)
    if party.vat_id:
        _sub(_sub(node, "ram:SpecifiedTaxRegistration"), "ram:ID", party.vat_id, schemeID="VA")
    if tax_number:
        _sub(_sub(node, "ram:SpecifiedTaxRegistration"), "ram:ID", tax_number, schemeID="FC")


def _allowance_charge(parent: etree._Element, item: AllowanceCharge, is_charge: bool) -> None:
    node = _sub(parent, "ram:SpecifiedTradeAllowanceCharge")
    _sub(_sub(node, "ram:ChargeIndicator"), "udt:Indicator", "true" if is_charge else "false")
    if item.percent is not None:
        _sub(node, "ram:CalculationPercent", fmt_decimal(item.percent))
    if item.base_amount is not None:
        _sub(node, "ram:BasisAmount", fmt_amount(item.base_amount))
    _sub(node, "ram:ActualAmount", fmt_amount(item.amount))
    if item.reason_code:
        _sub(node, "ram:ReasonCode", item.reason_code)
    if item.reason:
        _sub(node, "ram:Reason", item.reason)
    tax = _sub(node, "ram:CategoryTradeTax")
    _sub(tax, "ram:TypeCode", "VAT")
    _sub(tax, "ram:CategoryCode", item.vat_category.value)
    if item.vat_category.value != "O":
        _sub(tax, "ram:RateApplicablePercent", fmt_decimal(item.vat_rate))


def build_cii(invoice: EInvoiceData, profile: EInvoiceProfile, xrechnung_version: str = "3.0") -> bytes:
    """Builds the CII XML. The invoice must already be completed by calculator.complete_invoice()."""
    if invoice.totals is None or invoice.vat_breakdown is None:
        raise ValueError("Invoice totals must be calculated before building the XML")
    totals = invoice.totals
    currency = invoice.currency

    root = etree.Element(_q("rsm:CrossIndustryInvoice"), nsmap=NS)

    # Document context (BT-23, BT-24)
    context = _sub(root, "rsm:ExchangedDocumentContext")
    if profile == EInvoiceProfile.XRECHNUNG:
        _sub(_sub(context, "ram:BusinessProcessSpecifiedDocumentContextParameter"), "ram:ID", URN_PEPPOL_BILLING)
        guideline = xrechnung_urn(xrechnung_version)
    else:
        guideline = URN_EN16931
    _sub(_sub(context, "ram:GuidelineSpecifiedDocumentContextParameter"), "ram:ID", guideline)

    # Document header (BT-1, BT-3, BT-2, BG-1)
    document = _sub(root, "rsm:ExchangedDocument")
    _sub(document, "ram:ID", invoice.invoice_number)
    _sub(document, "ram:TypeCode", invoice.type_code)
    _date(document, "ram:IssueDateTime", invoice.issue_date)
    for note in invoice.notes:
        _sub(_sub(document, "ram:IncludedNote"), "ram:Content", note)

    transaction = _sub(root, "rsm:SupplyChainTradeTransaction")

    # Lines (BG-25)
    for line in invoice.lines:
        item = _sub(transaction, "ram:IncludedSupplyChainTradeLineItem")
        line_doc = _sub(item, "ram:AssociatedDocumentLineDocument")
        _sub(line_doc, "ram:LineID", line.position)
        if line.note:
            _sub(_sub(line_doc, "ram:IncludedNote"), "ram:Content", line.note)

        product = _sub(item, "ram:SpecifiedTradeProduct")
        if line.gtin:
            _sub(product, "ram:GlobalID", line.gtin, schemeID="0160")
        if line.seller_article_number:
            _sub(product, "ram:SellerAssignedID", line.seller_article_number)
        if line.buyer_article_number:
            _sub(product, "ram:BuyerAssignedID", line.buyer_article_number)
        _sub(product, "ram:Name", line.description)
        if line.item_description:
            _sub(product, "ram:Description", line.item_description)

        agreement = _sub(item, "ram:SpecifiedLineTradeAgreement")
        if line.gross_price is not None:
            gross = _sub(agreement, "ram:GrossPriceProductTradePrice")
            _sub(gross, "ram:ChargeAmount", fmt_decimal(line.gross_price))
            if line.price_base_quantity:
                _sub(gross, "ram:BasisQuantity", fmt_decimal(line.price_base_quantity), unitCode=line.unit)
            discount = line.gross_price - line.net_price
            if discount > 0:
                applied = _sub(gross, "ram:AppliedTradeAllowanceCharge")
                _sub(_sub(applied, "ram:ChargeIndicator"), "udt:Indicator", "false")
                _sub(applied, "ram:ActualAmount", fmt_decimal(discount))
        net = _sub(agreement, "ram:NetPriceProductTradePrice")
        _sub(net, "ram:ChargeAmount", fmt_decimal(line.net_price))
        if line.price_base_quantity:
            _sub(net, "ram:BasisQuantity", fmt_decimal(line.price_base_quantity), unitCode=line.unit)

        delivery = _sub(item, "ram:SpecifiedLineTradeDelivery")
        _sub(delivery, "ram:BilledQuantity", fmt_decimal(line.quantity), unitCode=line.unit)

        settlement = _sub(item, "ram:SpecifiedLineTradeSettlement")
        tax = _sub(settlement, "ram:ApplicableTradeTax")
        _sub(tax, "ram:TypeCode", "VAT")
        _sub(tax, "ram:CategoryCode", line.vat_category.value)
        if line.vat_category.value != "O":
            _sub(tax, "ram:RateApplicablePercent", fmt_decimal(line.vat_rate))
        for item, is_charge in [(a, False) for a in line.allowances] + [(c, True) for c in line.charges]:
            node = _sub(settlement, "ram:SpecifiedTradeAllowanceCharge")
            _sub(_sub(node, "ram:ChargeIndicator"), "udt:Indicator", "true" if is_charge else "false")
            if item.percent is not None:
                _sub(node, "ram:CalculationPercent", fmt_decimal(item.percent))
            if item.base_amount is not None:
                _sub(node, "ram:BasisAmount", fmt_amount(item.base_amount))
            _sub(node, "ram:ActualAmount", fmt_amount(item.amount))
            if item.reason_code:
                _sub(node, "ram:ReasonCode", item.reason_code)
            if item.reason:
                _sub(node, "ram:Reason", item.reason)
        summation = _sub(settlement, "ram:SpecifiedTradeSettlementLineMonetarySummation")
        _sub(summation, "ram:LineTotalAmount", fmt_amount(line.line_net_amount))

    # Header agreement: buyer reference, parties, references (BT-10, BG-4, BG-7, BT-13, BT-12)
    agreement = _sub(transaction, "ram:ApplicableHeaderTradeAgreement")
    if invoice.buyer.buyer_reference:
        _sub(agreement, "ram:BuyerReference", invoice.buyer.buyer_reference)
    seller = invoice.seller
    legal = seller.legal
    _party(
        agreement, "ram:SellerTradeParty", seller,
        legal_text=legal.as_text() if legal else None,
        register_id=legal.commercial_register if legal else None,
        tax_number=seller.tax_number,
    )
    _party(agreement, "ram:BuyerTradeParty", invoice.buyer)
    if invoice.order_reference:
        _sub(_sub(agreement, "ram:BuyerOrderReferencedDocument"), "ram:IssuerAssignedID", invoice.order_reference)
    if invoice.contract_reference:
        _sub(_sub(agreement, "ram:ContractReferencedDocument"), "ram:IssuerAssignedID", invoice.contract_reference)

    # Header delivery (BT-72)
    delivery = _sub(transaction, "ram:ApplicableHeaderTradeDelivery")
    if invoice.delivery_address or invoice.delivery_name:
        ship_to = _sub(delivery, "ram:ShipToTradeParty")
        if invoice.delivery_name:
            _sub(ship_to, "ram:Name", invoice.delivery_name)
        if invoice.delivery_address:
            _address(ship_to, invoice.delivery_address)
    if invoice.delivery_date:
        _date(_sub(delivery, "ram:ActualDeliverySupplyChainEvent"), "ram:OccurrenceDateTime", invoice.delivery_date)

    # Header settlement
    settlement = _sub(transaction, "ram:ApplicableHeaderTradeSettlement")
    payment = invoice.payment
    if payment and payment.means_code in DIRECT_DEBIT_MEANS and payment.creditor_id:
        _sub(settlement, "ram:CreditorReferenceID", payment.creditor_id)
    if payment and payment.payment_reference:
        _sub(settlement, "ram:PaymentReference", payment.payment_reference)
    _sub(settlement, "ram:InvoiceCurrencyCode", currency)

    if payment:
        means = _sub(settlement, "ram:SpecifiedTradeSettlementPaymentMeans")
        _sub(means, "ram:TypeCode", payment.means_code)
        if payment.means_code in DIRECT_DEBIT_MEANS and payment.debtor_iban:
            _sub(_sub(means, "ram:PayerPartyDebtorFinancialAccount"), "ram:IBANID", payment.debtor_iban)
        if payment.iban and payment.means_code not in DIRECT_DEBIT_MEANS:
            account = _sub(means, "ram:PayeePartyCreditorFinancialAccount")
            _sub(account, "ram:IBANID", payment.iban)
            if payment.account_name:
                _sub(account, "ram:AccountName", payment.account_name)
            if payment.bic:
                _sub(_sub(means, "ram:PayeeSpecifiedCreditorFinancialInstitution"), "ram:BICID", payment.bic)

    for vat in invoice.vat_breakdown:
        tax = _sub(settlement, "ram:ApplicableTradeTax")
        _sub(tax, "ram:CalculatedAmount", fmt_amount(vat.tax_amount))
        _sub(tax, "ram:TypeCode", "VAT")
        if vat.exemption_reason:
            _sub(tax, "ram:ExemptionReason", vat.exemption_reason)
        _sub(tax, "ram:BasisAmount", fmt_amount(vat.basis_amount))
        _sub(tax, "ram:CategoryCode", vat.category.value)
        if vat.exemption_reason_code:
            _sub(tax, "ram:ExemptionReasonCode", vat.exemption_reason_code)
        # EN 16931 omits the rate for category O, but XRechnung requires BT-119 in every breakdown (BR-DE-14).
        if vat.category.value != "O" or profile == EInvoiceProfile.XRECHNUNG:
            _sub(tax, "ram:RateApplicablePercent", fmt_decimal(vat.rate))

    if invoice.billing_period_start or invoice.billing_period_end:
        period = _sub(settlement, "ram:BillingSpecifiedPeriod")
        if invoice.billing_period_start:
            _date(period, "ram:StartDateTime", invoice.billing_period_start)
        if invoice.billing_period_end:
            _date(period, "ram:EndDateTime", invoice.billing_period_end)

    for allowance in invoice.allowances:
        _allowance_charge(settlement, allowance, is_charge=False)
    for charge in invoice.charges:
        _allowance_charge(settlement, charge, is_charge=True)

    if payment:
        terms_text = payment_terms_text(payment)
        if terms_text or payment.due_date or payment.mandate_reference:
            terms = _sub(settlement, "ram:SpecifiedTradePaymentTerms")
            if terms_text:
                _sub(terms, "ram:Description", terms_text)
            if payment.due_date:
                _date(terms, "ram:DueDateDateTime", payment.due_date)
            if payment.mandate_reference and payment.means_code in DIRECT_DEBIT_MEANS:
                _sub(terms, "ram:DirectDebitMandateID", payment.mandate_reference)

    summation = _sub(settlement, "ram:SpecifiedTradeSettlementHeaderMonetarySummation")
    _sub(summation, "ram:LineTotalAmount", fmt_amount(totals.line_total))
    if totals.charges is not None:
        _sub(summation, "ram:ChargeTotalAmount", fmt_amount(totals.charges))
    if totals.allowances is not None:
        _sub(summation, "ram:AllowanceTotalAmount", fmt_amount(totals.allowances))
    _sub(summation, "ram:TaxBasisTotalAmount", fmt_amount(totals.net_total))
    _sub(summation, "ram:TaxTotalAmount", fmt_amount(totals.vat_total), currencyID=currency)
    if totals.rounding_amount is not None:
        _sub(summation, "ram:RoundingAmount", fmt_amount(totals.rounding_amount))
    _sub(summation, "ram:GrandTotalAmount", fmt_amount(totals.gross_total))
    if totals.prepaid_amount is not None:
        _sub(summation, "ram:TotalPrepaidAmount", fmt_amount(totals.prepaid_amount))
    _sub(summation, "ram:DuePayableAmount", fmt_amount(totals.payable_amount))

    if invoice.preceding_invoice_number:
        reference = _sub(settlement, "ram:InvoiceReferencedDocument")
        _sub(reference, "ram:IssuerAssignedID", invoice.preceding_invoice_number)
        if invoice.preceding_invoice_date:
            _date(reference, "ram:FormattedIssueDateTime", invoice.preceding_invoice_date, qualified=True)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)
