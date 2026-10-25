"""
Visual invoice PDF (the human-readable part of a generated ZUGFeRD file) in several designs.

All designs show the same content (DIN 5008 style letter layout, § 14 UStG mandatory details) and differ only in
styling. Fonts are embedded and no transparency is used, so the output stays a valid PDF/A-3 base for embed_xml().
"""

import io
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import (
    BaseDocTemplate, Frame, KeepTogether, NextPageTemplate, PageTemplate, Paragraph, Spacer, Table, TableStyle,
)

from app.schemas.einvoice import EInvoiceData, PdfTemplate

TYPE_TITLES = {"381": "Gutschrift", "384": "Rechnungskorrektur", "389": "Gutschrift (Selbstfakturierung)",
               "326": "Teilrechnung", "875": "Abschlagsrechnung (Bau)", "876": "Teilschlussrechnung (Bau)", "877": "Schlussrechnung (Bau)"}

# Printed unit names for the UN/ECE Rec 20 codes the form produces (see codes.UNIT_ALIASES); others print as the code.
UNIT_LABELS = {
    "C62": "Stk", "H87": "Stk", "EA": "Stk", "HUR": "Std", "MIN": "Min", "DAY": "Tag(e)", "WEE": "Woche(n)",
    "MON": "Monat(e)", "ANN": "Jahr(e)", "KGM": "kg", "GRM": "g", "TNE": "t", "MTR": "m", "KMT": "km", "CMT": "cm",
    "MMT": "mm", "MTK": "m²", "MTQ": "m³", "LTR": "l", "MLT": "ml", "LS": "pauschal", "XPK": "Paket", "SET": "Set",
    "PR": "Paar", "KWH": "kWh", "ST": "Blatt",
}
CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£"}
# Payment means that ask the buyer to transfer money (UNTDID 4461)
TRANSFER_MEANS = {"30", "31", "42", "58"}

PAGE_W, PAGE_H = A4
MARGIN_X = 20 * mm
CONTENT_W = PAGE_W - 2 * MARGIN_X


@dataclass(frozen=True)
class Theme:
    accent: str
    ink: str = "#111827"
    text: str = "#1f2937"
    muted: str = "#6b7280"
    rule: str = "#e5e7eb"
    tint: str = "#f3f4f6"
    accent_light: str = "#d1d5db"  # text on the accent colour (modern band)
    zebra: Optional[str] = None


THEMES = {
    PdfTemplate.CLASSIC: Theme(accent="#1f2937", tint="#f9fafb"),
    PdfTemplate.MODERN: Theme(accent="#1e40af", tint="#eff6ff", accent_light="#bfdbfe", zebra="#f8fafc", rule="#e2e8f0"),
    PdfTemplate.MINIMAL: Theme(accent="#111827", muted="#71717a", rule="#e4e4e7", tint="#ffffff"),
}


# ---------------------------------------------------------------------------
# German number formatting
# ---------------------------------------------------------------------------

def de_amount(value: Optional[Decimal], currency: str = "") -> str:
    if value is None:
        return ""
    text = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{text}\u00a0{CURRENCY_SYMBOLS.get(currency, currency)}" if currency else text


def de_number(value: Decimal) -> str:
    text = f"{value.normalize():f}"
    return text.replace(".", ",")


def de_price(value: Decimal) -> str:
    """Unit prices keep all significant decimals (min. 2), e.g. 0,0045 instead of a misleading 0,00."""
    whole, _, frac = f"{value.normalize():f}".partition(".")
    whole = f"{int(whole):,}".replace(",", ".") if whole.lstrip("-").isdigit() else whole
    return f"{whole},{frac.ljust(2, '0')}"


def _de_date(value) -> str:
    return value.strftime("%d.%m.%Y")


def _iban(value: str) -> str:
    return " ".join(value[i:i + 4] for i in range(0, len(value), 4))


def _city_line(address) -> str:
    return " ".join(filter(None, [address.postcode, address.city]))


def _register_fonts() -> None:
    if "Vera" in pdfmetrics.getRegisteredFontNames():
        return
    for name, file in (("Vera", "Vera.ttf"), ("VeraBd", "VeraBd.ttf"), ("VeraIt", "VeraIt.ttf"), ("VeraBI", "VeraBI.ttf")):
        pdfmetrics.registerFont(TTFont(name, file))
    pdfmetrics.registerFontFamily("Vera", normal="Vera", bold="VeraBd", italic="VeraIt", boldItalic="VeraBI")


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

class _InvoiceRenderer:
    BAND_H = 40 * mm  # modern header band

    def __init__(self, invoice: EInvoiceData, template: PdfTemplate):
        self.inv = invoice
        self.template = template
        self.theme = THEMES[template]
        self.cur = invoice.currency
        self.title = TYPE_TITLES.get(invoice.type_code, "Rechnung")
        self._styles()

    # -- styles & helpers ---------------------------------------------------

    def _styles(self) -> None:
        t = self.theme
        c = colors.HexColor
        self.s_base = ParagraphStyle("base", fontName="Vera", fontSize=8.5, leading=12, textColor=c(t.text))
        self.s_right = ParagraphStyle("right", parent=self.s_base, alignment=TA_RIGHT)
        self.s_bold = ParagraphStyle("bold", parent=self.s_base, fontName="VeraBd", textColor=c(t.ink))
        self.s_bold_right = ParagraphStyle("boldRight", parent=self.s_bold, alignment=TA_RIGHT)
        self.s_small = ParagraphStyle("small", parent=self.s_base, fontSize=7.5, leading=10, textColor=c(t.muted))
        self.s_small_right = ParagraphStyle("smallRight", parent=self.s_small, alignment=TA_RIGHT)
        self.s_label = ParagraphStyle("label", parent=self.s_small, fontName="VeraBd", fontSize=7, leading=10,
                                      textColor=c(t.accent if self.template == PdfTemplate.MODERN else t.muted))
        self.s_address = ParagraphStyle("address", parent=self.s_base, fontSize=10, leading=14, textColor=c(t.ink))
        self.s_sender = ParagraphStyle("sender", parent=self.s_small, fontSize=6.5, leading=9)
        self.s_footer = ParagraphStyle("footer", parent=self.s_small, fontSize=6.8, leading=9)
        self.s_section = ParagraphStyle("section", parent=self.s_bold, fontSize=9.5, leading=13, spaceAfter=3)
        head_color = colors.white if self.template == PdfTemplate.MODERN else c(t.muted if self.template == PdfTemplate.MINIMAL else t.ink)
        self.s_th = ParagraphStyle("th", parent=self.s_bold, fontSize=7.5, leading=10, textColor=head_color)
        self.s_th_right = ParagraphStyle("thRight", parent=self.s_th, alignment=TA_RIGHT)

    @staticmethod
    def p(text: Optional[str], style: ParagraphStyle) -> Paragraph:
        """Paragraph from plain text (escaped, newlines kept)."""
        return Paragraph(escape(text or "").replace("\n", "<br/>"), style)

    @staticmethod
    def lines(*parts: Optional[str]) -> str:
        return "\n".join(filter(None, parts))

    # -- content blocks -------------------------------------------------------

    def _meta_rows(self) -> List[Tuple[str, str]]:
        inv, buyer, payment = self.inv, self.inv.buyer, self.inv.payment
        rows = [("Rechnungsnummer", inv.invoice_number), ("Rechnungsdatum", _de_date(inv.issue_date))]
        if inv.delivery_date:
            rows.append(("Leistungsdatum", _de_date(inv.delivery_date)))
        if inv.billing_period_start or inv.billing_period_end:
            period = " – ".join(_de_date(d) for d in (inv.billing_period_start, inv.billing_period_end) if d)
            rows.append(("Leistungszeitraum", period))
        if payment and payment.due_date:
            rows.append(("Fällig am", _de_date(payment.due_date)))
        if buyer.id:
            rows.append(("Kundennummer", buyer.id))
        if buyer.buyer_reference:
            rows.append(("Leitweg-ID / Referenz", buyer.buyer_reference))
        if inv.order_reference:
            rows.append(("Bestellnummer", inv.order_reference))
        if inv.contract_reference:
            rows.append(("Vertragsnummer", inv.contract_reference))
        if buyer.vat_id:
            rows.append(("USt-IdNr. Kunde", buyer.vat_id))
        if inv.preceding_invoice_number:
            ref = inv.preceding_invoice_number
            if inv.preceding_invoice_date:
                ref += f" vom {_de_date(inv.preceding_invoice_date)}"
            rows.append(("Bezug auf Rechnung", ref))
        return rows

    def _meta_table(self, width: float) -> Table:
        right = self.template != PdfTemplate.MINIMAL
        value_style = self.s_right if right else self.s_base
        label_w = 34 * mm
        rows = [[self.p(k, self.s_small), self.p(v, value_style)] for k, v in self._meta_rows()]
        modern = self.template == PdfTemplate.MODERN
        table = Table(rows, colWidths=[label_w, width - label_w], cornerRadii=[4, 4, 4, 4] if modern else None)
        style = [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8 if modern else 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8 if modern else 0),
            ("TOPPADDING", (0, 0), (-1, -1), 1.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
        ]
        if modern:
            style += [("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(self.theme.tint)),
                      ("TOPPADDING", (0, 0), (-1, 0), 7), ("BOTTOMPADDING", (0, -1), (-1, -1), 7)]
        table.setStyle(TableStyle(style))
        return table

    def _buyer_label(self) -> str:
        return "GUTSCHRIFT AN" if self.title.startswith("Gutschrift") else "RECHNUNG AN"

    def _buyer_text(self) -> str:
        buyer, seller = self.inv.buyer, self.inv.seller
        addr = buyer.address
        attention = f"z. Hd. {buyer.contact.name}" if buyer.contact and buyer.contact.name else None
        country = addr.country if addr.country != seller.address.country else None
        return self.lines(buyer.name, attention, addr.street, addr.additional_street, addr.address_line_3, _city_line(addr), country)

    def _delivery_block(self) -> List:
        inv = self.inv
        if not (inv.delivery_name or inv.delivery_address):
            return []
        addr = inv.delivery_address
        text = self.lines(inv.delivery_name, addr.street if addr else None, addr.additional_street if addr else None,
                          _city_line(addr) if addr else None)
        return [Spacer(1, 4 * mm), self.p("LIEFERANSCHRIFT" if self.template != PdfTemplate.CLASSIC else "Lieferanschrift", self.s_label),
                self.p(text, self.s_base)]

    def _address_zone(self, buyer_label: Optional[str], sender_line: bool) -> Table:
        seller = self.inv.seller
        left: List = []
        if sender_line:
            sender = " · ".join(filter(None, [seller.name, seller.address.street, _city_line(seller.address)]))
            left += [Paragraph(f"<u>{escape(sender)}</u>", self.s_sender), Spacer(1, 2 * mm)]
        if buyer_label:
            left.append(self.p(buyer_label, self.s_label))
        left.append(self.p(self._buyer_text(), self.s_address))
        left += self._delivery_block()
        meta_w = 86 * mm
        right: List = []
        if self.template == PdfTemplate.MINIMAL:
            right.append(self.p("RECHNUNGSDETAILS", self.s_label))
        right.append(self._meta_table(meta_w))
        zone = Table([[left, right]], colWidths=[CONTENT_W - meta_w, meta_w])
        zone.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        return zone

    def _seller_contact_text(self) -> str:
        seller = self.inv.seller
        contact = seller.contact
        return self.lines(
            seller.address.street, _city_line(seller.address),
            f"Tel. {contact.phone}" if contact and contact.phone else None,
            contact.email if contact and contact.email else None,
        )

    def _notes(self) -> List:
        return [self.p(note, self.s_base) for note in self.inv.notes] + ([Spacer(1, 4 * mm)] if self.inv.notes else [])

    def _line_description(self, line) -> Paragraph:
        parts = [f"<font name='VeraBd' color='{self.theme.ink}'>{escape(line.description)}</font>"]
        small = []
        if line.item_description:
            small.append(escape(line.item_description).replace("\n", "<br/>"))
        ids = " · ".join(filter(None, [
            f"Art.-Nr. {escape(line.seller_article_number)}" if line.seller_article_number else None,
            f"Ihre Art.-Nr. {escape(line.buyer_article_number)}" if line.buyer_article_number else None,
            f"GTIN {escape(line.gtin)}" if line.gtin else None,
        ]))
        if ids:
            small.append(ids)
        if line.note:
            small.append(escape(line.note).replace("\n", "<br/>"))
        for allowance in line.allowances:
            small.append(f"{escape(allowance.reason or 'Rabatt')}: −{de_amount(allowance.amount, self.cur)}")
        for charge in line.charges:
            small.append(f"{escape(charge.reason or 'Zuschlag')}: +{de_amount(charge.amount, self.cur)}")
        if small:
            parts.append(f"<font size='7.5' color='{self.theme.muted}'>{'<br/>'.join(small)}</font>")
        return Paragraph("<br/>".join(parts), self.s_base)

    def _lines_table(self) -> Table:
        t, cur = self.theme, self.cur
        labels = ["Pos.", "Bezeichnung", "Menge", "Einzelpreis", "USt", "Gesamt"]
        if self.template == PdfTemplate.MINIMAL:
            labels = [label.upper() for label in labels]
        rows = [[self.p(label, self.s_th if i < 2 else self.s_th_right) for i, label in enumerate(labels)]]
        for line in self.inv.lines:
            unit = UNIT_LABELS.get(line.unit, line.unit)
            price = de_price(line.net_price) + (f"\u00a0{CURRENCY_SYMBOLS.get(cur, cur)}" if cur else "")
            if line.price_base_quantity and line.price_base_quantity != 1:
                price += f" / {de_number(line.price_base_quantity)} {unit}"
            price_cell = [self.p(price, self.s_right)]
            if line.gross_price is not None and line.gross_price > line.net_price:
                price_cell.append(self.p(f"statt {de_price(line.gross_price)}", self.s_small_right))
            rows.append([
                self.p(line.position, self.s_base), self._line_description(line),
                self.p(f"{de_number(line.quantity)} {unit}", self.s_right), price_cell,
                self.p(f"{de_number(line.vat_rate)} %", self.s_right), self.p(de_amount(line.line_net_amount, cur), self.s_bold_right),
            ])
        widths = [10 * mm, 74 * mm, 22 * mm, 26 * mm, 12 * mm, 26 * mm]
        table = Table(rows, colWidths=widths, repeatRows=1)
        c = colors.HexColor
        style = [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]
        if self.template == PdfTemplate.MODERN:
            style += [
                ("BACKGROUND", (0, 0), (-1, 0), c(t.accent)),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, c(t.zebra)]),
                ("LINEBELOW", (0, -1), (-1, -1), 0.8, c(t.accent)),
                ("TOPPADDING", (0, 0), (-1, 0), 6), ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
            ]
        elif self.template == PdfTemplate.CLASSIC:
            style += [
                ("BACKGROUND", (0, 0), (-1, 0), c(t.tint)),
                ("LINEABOVE", (0, 0), (-1, 0), 1, c(t.ink)),
                ("LINEBELOW", (0, 0), (-1, 0), 0.6, c(t.ink)),
                ("LINEBELOW", (0, 1), (-1, -2), 0.3, c(t.rule)),
                ("LINEBELOW", (0, -1), (-1, -1), 0.8, c(t.ink)),
            ]
        else:
            style += [
                ("LINEBELOW", (0, 0), (-1, 0), 0.6, c(t.ink)),
                ("LINEBELOW", (0, 1), (-1, -1), 0.3, c(t.rule)),
                ("LEFTPADDING", (0, 0), (0, -1), 0), ("RIGHTPADDING", (-1, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 1), (-1, -1), 7), ("BOTTOMPADDING", (0, 1), (-1, -1), 7),
            ]
        table.setStyle(TableStyle(style))
        return table

    def _totals(self) -> Table:
        inv, totals, cur, t = self.inv, self.inv.totals, self.cur, self.theme
        rows: List[Tuple[str, str]] = []
        adjusted = bool(inv.allowances or inv.charges)
        if adjusted:
            rows.append(("Summe Positionen", de_amount(totals.line_total, cur)))
            for allowance in inv.allowances:
                pct = f" ({de_number(allowance.percent)} %)" if allowance.percent else ""
                rows.append((f"abzgl. {allowance.reason or 'Nachlass'}{pct}", f"−{de_amount(allowance.amount, cur)}"))
            for charge in inv.charges:
                pct = f" ({de_number(charge.percent)} %)" if charge.percent else ""
                rows.append((f"zzgl. {charge.reason or 'Zuschlag'}{pct}", de_amount(charge.amount, cur)))
        rows.append(("Nettobetrag", de_amount(totals.net_total, cur)))
        for vat in inv.vat_breakdown or []:
            rows.append((f"USt {de_number(vat.rate)} % auf {de_amount(vat.basis_amount, cur)}", de_amount(vat.tax_amount, cur)))
        rows.append(("Gesamtbetrag", de_amount(totals.gross_total, cur)))
        if totals.prepaid_amount:
            rows.append(("abzgl. bereits gezahlt", f"−{de_amount(totals.prepaid_amount, cur)}"))
        if totals.rounding_amount:
            rows.append(("Rundung", de_amount(totals.rounding_amount, cur)))

        c = colors.HexColor
        modern, minimal = self.template == PdfTemplate.MODERN, self.template == PdfTemplate.MINIMAL
        payable_label = ParagraphStyle("payL", parent=self.s_bold, fontSize=10, leading=14,
                                       textColor=colors.white if modern else c(t.ink))
        payable_value = ParagraphStyle("payV", parent=payable_label, alignment=TA_RIGHT, fontSize=15 if minimal else 11.5,
                                       leading=19 if minimal else 15)
        data = [[self.p(k, self.s_base), self.p(v, self.s_right)] for k, v in rows]
        data.append([self.p("Zahlbetrag", payable_label), self.p(de_amount(totals.payable_amount, cur), payable_value)])
        table = Table(data, colWidths=[62 * mm, 34 * mm], hAlign="RIGHT", cornerRadii=[0, 0, 4, 4] if modern else None)
        last = len(data) - 1
        style = [
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, last), (-1, last), 7), ("BOTTOMPADDING", (0, last), (-1, last), 7),
        ]
        gross_row = next(i for i, (k, _) in enumerate(rows) if k == "Gesamtbetrag")
        style.append(("LINEABOVE", (0, gross_row), (-1, gross_row), 0.4, c(t.rule)))
        if modern:
            style.append(("BACKGROUND", (0, last), (-1, last), c(t.accent)))
        elif minimal:
            style += [("LINEABOVE", (0, last), (-1, last), 0.8, c(t.ink)), ("RIGHTPADDING", (-1, 0), (-1, -1), 0)]
        else:
            style += [("LINEABOVE", (0, last), (-1, last), 1, c(t.ink)), ("BACKGROUND", (0, last), (-1, last), c(t.tint)),
                      ("LINEBELOW", (0, last), (-1, last), 1, c(t.ink))]
        table.setStyle(TableStyle(style))
        return table

    def _vat_notes(self) -> List:
        notes = []
        for vat in self.inv.vat_breakdown or []:
            if vat.exemption_reason or vat.exemption_reason_code:
                reason = vat.exemption_reason or vat.exemption_reason_code
                notes.append(self.p(f"Hinweis zur Umsatzsteuer ({de_number(vat.rate)} %, Kategorie {vat.category.value}): {reason}", self.s_small))
        return ([Spacer(1, 3 * mm)] + notes) if notes else []

    def _payment(self) -> List:
        payment, cur = self.inv.payment, self.cur
        if not payment:
            return []
        payable = de_amount(self.inv.totals.payable_amount, cur)
        text: List[str] = []
        if payment.terms:
            text.append(payment.terms)
        if payment.iban and (payment.means_code in TRANSFER_MEANS):
            due = f" bis zum {_de_date(payment.due_date)}" if payment.due_date else ""
            text.append(f"Bitte überweisen Sie den Betrag von {payable}{due} unter Angabe des Verwendungszwecks auf das unten genannte Konto.")
        if payment.means_code == "59":
            debit = f"Der Betrag von {payable} wird per SEPA-Lastschrift"
            if payment.debtor_iban:
                debit += f" von Ihrem Konto {_iban(payment.debtor_iban)}"
            text.append(debit + (f" zum {_de_date(payment.due_date)}" if payment.due_date else "") + " eingezogen.")
        if payment.discount_days and payment.discount_percent:
            text.append(f"Bei Zahlung innerhalb von {payment.discount_days} Tagen gewähren wir {de_number(payment.discount_percent)} % Skonto.")

        details: List[Tuple[str, str]] = []
        if payment.iban:
            if payment.account_name:
                details.append(("Kontoinhaber", payment.account_name))
            details.append(("IBAN", _iban(payment.iban)))
            if payment.bic:
                details.append(("BIC", payment.bic))
            details.append(("Verwendungszweck", payment.payment_reference or self.inv.invoice_number))
        if payment.mandate_reference:
            details.append(("Mandatsreferenz", payment.mandate_reference))
        if payment.creditor_id:
            details.append(("Gläubiger-ID", payment.creditor_id))
        if not text and not details:
            return []

        t, c = self.theme, colors.HexColor
        inner_w = CONTENT_W - 2 * 10
        left = [self.p(line, self.s_base) for line in text]
        if details:
            detail_table = Table([[self.p(k, self.s_small), self.p(v, self.s_bold)] for k, v in details], colWidths=[32 * mm, 56 * mm])
            detail_table.setStyle(TableStyle([
                ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            body = Table([[left, detail_table]], colWidths=[inner_w - 92 * mm, 92 * mm]) if left else detail_table
            if left:
                body.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                          ("RIGHTPADDING", (0, 0), (0, -1), 8), ("RIGHTPADDING", (1, 0), (1, -1), 0),
                                          ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
        else:
            body = left
        heading = "ZAHLUNGSINFORMATIONEN" if self.template == PdfTemplate.MINIMAL else "Zahlungsinformationen"
        title_style = self.s_label if self.template == PdfTemplate.MINIMAL else self.s_section
        box = Table([[[self.p(heading, title_style), Spacer(1, 1 * mm), body]]], colWidths=[CONTENT_W],
                    cornerRadii=[4, 4, 4, 4] if self.template == PdfTemplate.MODERN else None)
        style = [("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                 ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 9)]
        if self.template == PdfTemplate.MODERN:
            style.append(("BACKGROUND", (0, 0), (-1, -1), c(t.tint)))
        elif self.template == PdfTemplate.CLASSIC:
            style += [("BOX", (0, 0), (-1, -1), 0.5, c(t.rule)), ("BACKGROUND", (0, 0), (-1, -1), c(t.tint))]
        else:
            style += [("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                      ("LINEABOVE", (0, 0), (-1, 0), 0.3, c(t.rule))]
        box.setStyle(TableStyle(style))
        return [Spacer(1, 7 * mm), box]

    def _closing(self) -> List:
        return [Spacer(1, 5 * mm), self.p(
            "Elektronische Rechnung nach EN 16931 (ZUGFeRD): Die strukturierten Rechnungsdaten sind als factur-x.xml in diese PDF eingebettet.",
            self.s_small)]

    # -- page furniture -------------------------------------------------------

    def _footer_table(self) -> Table:
        seller, payment = self.inv.seller, self.inv.payment
        contact, legal = seller.contact, seller.legal
        address = self.lines(seller.address.street, _city_line(seller.address))
        contact_text = self.lines(
            contact.name if contact else None, f"Tel. {contact.phone}" if contact and contact.phone else None,
            contact.email if contact else None,
            seller.electronic_address if seller.electronic_address and (not contact or seller.electronic_address != contact.email) else None,
        )
        register = None
        if legal and legal.commercial_register and legal.commercial_register not in (legal.additional_info or ""):
            register = ", ".join(filter(None, [legal.commercial_register, legal.register_court]))
        legal_text = self.lines(
            f"USt-IdNr. {seller.vat_id}" if seller.vat_id else None,
            f"Steuernummer {seller.tax_number}" if seller.tax_number else None,
            f"Geschäftsführung: {legal.managing_director}" if legal and legal.managing_director else None,
            register, legal.additional_info if legal else None,
        )
        bank_text = self.lines(
            payment.account_name if payment else None, f"IBAN {_iban(payment.iban)}" if payment and payment.iban else None,
            f"BIC {payment.bic}" if payment and payment.bic else None,
        )
        columns = [[Paragraph(f"<b>{escape(seller.name)}</b>", self.s_footer), self.p(address, self.s_footer)]]
        weights = [0.85]
        for text, weight in ((contact_text, 1.0), (legal_text, 1.15), (bank_text, 1.4)):
            if text:
                columns.append([self.p(text, self.s_footer)])
                weights.append(weight)
        table = Table([columns], colWidths=[CONTENT_W * w / sum(weights) for w in weights])
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        return table

    def _draw_footer(self, canv, footer: Table, footer_h: float) -> None:
        t = self.theme
        top = 14 * mm + footer_h
        canv.saveState()
        rule_color, rule_w = {
            PdfTemplate.MODERN: (t.accent, 0.8), PdfTemplate.CLASSIC: (t.ink, 0.5),
        }.get(self.template, (t.rule, 0.4))
        canv.setStrokeColor(colors.HexColor(rule_color))
        canv.setLineWidth(rule_w)
        canv.line(MARGIN_X, top + 3 * mm, PAGE_W - MARGIN_X, top + 3 * mm)
        footer.drawOn(canv, MARGIN_X, 14 * mm)
        canv.restoreState()

    def _draw_band(self, canv) -> None:
        """Modern design: full-width accent band with seller name and document title."""
        t, seller = self.theme, self.inv.seller
        canv.saveState()
        canv.setFillColor(colors.HexColor(t.accent))
        canv.rect(0, PAGE_H - self.BAND_H, PAGE_W, self.BAND_H, stroke=0, fill=1)
        light = colors.HexColor(t.accent_light)
        name_style = ParagraphStyle("bandName", fontName="VeraBd", fontSize=17, leading=21, textColor=colors.white)
        sub_style = ParagraphStyle("bandSub", fontName="Vera", fontSize=8.5, leading=12, textColor=light)
        title_style = ParagraphStyle("bandTitle", parent=name_style, fontSize=20, leading=24, alignment=TA_RIGHT)
        sub_right = ParagraphStyle("bandSubR", parent=sub_style, alignment=TA_RIGHT)
        half = CONTENT_W / 2 - 4 * mm
        self._draw_stack(canv, MARGIN_X, half, [
            (self.p(seller.trading_name or seller.name, name_style), 2),
            (self.p(" · ".join(filter(None, [seller.address.street, _city_line(seller.address)])), sub_style), 0),
        ])
        self._draw_stack(canv, PAGE_W - MARGIN_X - half, half, [
            (self.p(self.title.upper(), title_style), 2),
            (self.p(f"Nr. {self.inv.invoice_number} · {_de_date(self.inv.issue_date)}", sub_right), 0),
        ])
        canv.restoreState()

    def _draw_stack(self, canv, x: float, width: float, items: Sequence[Tuple[Paragraph, float]]) -> None:
        """Draws paragraphs top-down, vertically centred in the band."""
        heights = [para.wrap(width, self.BAND_H)[1] + gap for para, gap in items]
        y = PAGE_H - self.BAND_H / 2 + sum(heights) / 2
        for (para, gap), h in zip(items, heights):
            y -= h - gap
            para.drawOn(canv, x, y)
            y -= gap

    def _draw_later_header(self, canv) -> None:
        t = self.theme
        canv.saveState()
        if self.template == PdfTemplate.MODERN:
            canv.setFillColor(colors.HexColor(t.accent))
            canv.rect(0, PAGE_H - 4 * mm, PAGE_W, 4 * mm, stroke=0, fill=1)
        y = PAGE_H - 14 * mm
        canv.setFont("VeraBd", 8)
        canv.setFillColor(colors.HexColor(t.ink))
        canv.drawString(MARGIN_X, y, self.inv.seller.name[:80])
        canv.setFont("Vera", 8)
        canv.setFillColor(colors.HexColor(t.muted))
        canv.drawRightString(PAGE_W - MARGIN_X, y, f"{self.title} {self.inv.invoice_number}")
        canv.setStrokeColor(colors.HexColor(t.rule))
        canv.setLineWidth(0.4)
        canv.line(MARGIN_X, y - 3 * mm, PAGE_W - MARGIN_X, y - 3 * mm)
        canv.restoreState()

    def _canvas_class(self) -> type:
        """Canvas that stamps 'Seite x von y' once the page count is known (multi-page invoices only)."""
        muted = colors.HexColor(self.theme.muted)

        class NumberedCanvas(pdf_canvas.Canvas):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._pages: List[dict] = []

            def showPage(self):
                self._pages.append(dict(self.__dict__))
                self._startPage()

            def save(self):
                total = len(self._pages)
                for state in self._pages:
                    self.__dict__.update(state)
                    if total > 1:
                        self.setFont("Vera", 7)
                        self.setFillColor(muted)
                        self.drawRightString(PAGE_W - MARGIN_X, 8 * mm, f"Seite {self._pageNumber} von {total}")
                    super().showPage()
                super().save()

        return NumberedCanvas

    # -- first page header per design ---------------------------------------

    def _classic_header(self) -> List:
        seller = self.inv.seller
        name = ParagraphStyle("lhName", parent=self.s_bold, fontSize=16, leading=20)
        letterhead = Table(
            [[self.p(seller.trading_name or seller.name, name), self.p(self._seller_contact_text(), self.s_small_right)]],
            colWidths=[CONTENT_W * 0.6, CONTENT_W * 0.4],
        )
        letterhead.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "BOTTOM"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LINEBELOW", (0, 0), (-1, 0), 1.2, colors.HexColor(self.theme.ink)),
        ]))
        title = ParagraphStyle("title", parent=self.s_bold, fontSize=18, leading=22)
        return [letterhead, Spacer(1, 12 * mm), self._address_zone(None, sender_line=True), Spacer(1, 12 * mm),
                Paragraph(f"{escape(self.title)} <font name='Vera' color='{self.theme.muted}'>Nr. {escape(self.inv.invoice_number)}</font>", title),
                Spacer(1, 5 * mm)]

    def _modern_header(self) -> List:
        return [self._address_zone(self._buyer_label(), sender_line=False),
                Spacer(1, 10 * mm)]

    def _minimal_header(self) -> List:
        seller = self.inv.seller
        title = ParagraphStyle("minTitle", parent=self.s_base, fontSize=26, leading=30, textColor=colors.HexColor(self.theme.ink))
        number = ParagraphStyle("minNo", parent=self.s_small, fontSize=9, leading=12)
        seller_block = Paragraph(
            f"<b><font color='{self.theme.ink}'>{escape(seller.trading_name or seller.name)}</font></b><br/>"
            + escape(self._seller_contact_text()).replace("\n", "<br/>"),
            self.s_small_right,
        )
        top = Table([[[self.p(self.title, title), self.p(f"Nr. {self.inv.invoice_number}", number)], seller_block]],
                    colWidths=[CONTENT_W * 0.55, CONTENT_W * 0.45])
        top.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                 ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0)]))
        return [top, Spacer(1, 14 * mm), self._address_zone(self._buyer_label(), sender_line=False), Spacer(1, 12 * mm)]

    # -- assembly ---------------------------------------------------------------

    def render(self) -> bytes:
        header: Callable[[], List] = {
            PdfTemplate.CLASSIC: self._classic_header, PdfTemplate.MODERN: self._modern_header, PdfTemplate.MINIMAL: self._minimal_header,
        }[self.template]
        story = header() + self._notes() + [self._lines_table(), Spacer(1, 5 * mm), KeepTogether([self._totals()])]
        story += self._vat_notes() + [KeepTogether(self._payment() + self._closing())]
        story.insert(0, NextPageTemplate("later"))

        footer = self._footer_table()
        footer_h = footer.wrap(CONTENT_W, PAGE_H)[1]
        bottom = 14 * mm + footer_h + 8 * mm
        first_top = self.BAND_H + 12 * mm if self.template == PdfTemplate.MODERN else 18 * mm
        later_top = 24 * mm

        def on_first(canv, _doc):
            if self.template == PdfTemplate.MODERN:
                self._draw_band(canv)
            self._draw_footer(canv, footer, footer_h)

        def on_later(canv, _doc):
            self._draw_later_header(canv)
            self._draw_footer(canv, footer, footer_h)

        def frame(top: float, frame_id: str) -> Frame:
            return Frame(MARGIN_X, bottom, CONTENT_W, PAGE_H - top - bottom, id=frame_id,
                         leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)

        buffer = io.BytesIO()
        doc = BaseDocTemplate(
            buffer, pagesize=A4, leftMargin=MARGIN_X, rightMargin=MARGIN_X, topMargin=first_top, bottomMargin=bottom,
            title=f"{self.title} {self.inv.invoice_number}", author=self.inv.seller.name,
            subject=f"{self.title} {self.inv.invoice_number}", lang="de-DE",
        )
        doc.addPageTemplates([
            PageTemplate(id="first", frames=[frame(first_top, "first")], onPage=on_first),
            PageTemplate(id="later", frames=[frame(later_top, "later")], onPage=on_later),
        ])
        doc.build(story, canvasmaker=self._canvas_class())
        return buffer.getvalue()


def render_invoice_pdf(invoice: EInvoiceData, template: PdfTemplate = PdfTemplate.CLASSIC) -> bytes:
    """Renders a human-readable A4 invoice in the chosen design, with embedded fonts (prerequisite for PDF/A)."""
    _register_fonts()
    return _InvoiceRenderer(invoice, template).render()
