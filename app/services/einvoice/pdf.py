"""
PDF handling for ZUGFeRD: visual invoice rendering, uploaded PDF checks, optional PDF/A-3 normalisation,
XML embedding (factur-x.xml via the factur-x library) and extraction of embedded XML + XMP metadata.
"""

import asyncio
import io
import logging
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Dict, Optional, Tuple
from xml.sax.saxutils import escape

import facturx
from lxml import etree
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.core.einvoice_config import get_einvoice_settings
from app.schemas.einvoice import EInvoiceData

logger = logging.getLogger("app.einvoice.pdf")

# Known embedded XML file names: ZUGFeRD 2.1+/Factur-X, ZUGFeRD 2.0, ZUGFeRD 1.0, ZUGFeRD 2.1+ XRECHNUNG profile
EMBEDDED_XML_NAMES = ["factur-x.xml", "zugferd-invoice.xml", "ZUGFeRD-invoice.xml", "xrechnung.xml"]
VERSION_BY_FILENAME = {
    "factur-x.xml": "ZUGFeRD 2.1+ / Factur-X 1.0",
    "zugferd-invoice.xml": "ZUGFeRD 2.0",
    "ZUGFeRD-invoice.xml": "ZUGFeRD 1.0",
    "xrechnung.xml": "ZUGFeRD 2.1+ (XRECHNUNG profile)",
}

TYPE_TITLES = {"381": "Gutschrift", "384": "Rechnungskorrektur", "389": "Gutschrift (Selbstfakturierung)",
               "326": "Teilrechnung", "875": "Abschlagsrechnung (Bau)", "876": "Teilschlussrechnung (Bau)", "877": "Schlussrechnung (Bau)"}


def _address_line(address) -> str:
    return " ".join(filter(None, [address.postcode, address.city]))


class PdfInputError(ValueError):
    """Uploaded PDF cannot be used (corrupt, encrypted, empty)."""


def check_uploaded_pdf(content: bytes) -> int:
    """Returns the page count of an uploaded PDF or raises PdfInputError."""
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            raise PdfInputError("Encrypted / password-protected PDFs cannot be converted to PDF/A-3")
        pages = len(reader.pages)
    except PdfReadError as exc:
        raise PdfInputError(f"PDF could not be read: {exc}") from exc
    if pages == 0:
        raise PdfInputError("PDF has no pages")
    return pages


def _de_amount(value: Optional[Decimal], currency: str = "") -> str:
    if value is None:
        return ""
    text = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{text} {currency}".strip()


def _de_number(value: Decimal) -> str:
    text = f"{value.normalize():f}"
    return text.replace(".", ",")


def _de_price(value: Decimal) -> str:
    """Unit prices keep all significant decimals (min. 2), e.g. 0,0045 instead of a misleading 0,00."""
    whole, _, frac = f"{value.normalize():f}".partition(".")
    whole = f"{int(whole):,}".replace(",", ".") if whole.lstrip("-").isdigit() else whole
    return f"{whole},{frac.ljust(2, '0')}"


def render_invoice_pdf(invoice: EInvoiceData) -> bytes:
    """Renders a human-readable A4 invoice with embedded fonts (prerequisite for PDF/A)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    if "Vera" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("Vera", "Vera.ttf"))
        pdfmetrics.registerFont(TTFont("VeraBd", "VeraBd.ttf"))

    base = ParagraphStyle("base", fontName="Vera", fontSize=9, leading=12)
    small = ParagraphStyle("small", parent=base, fontSize=7, leading=9, textColor=colors.HexColor("#555555"))
    title = ParagraphStyle("title", parent=base, fontName="VeraBd", fontSize=16, leading=20)
    bold = ParagraphStyle("bold", parent=base, fontName="VeraBd")

    def p(text: Optional[str], style: ParagraphStyle = base) -> Paragraph:
        return Paragraph(escape(text or "").replace("\n", "<br/>"), style)

    seller, buyer, cur = invoice.seller, invoice.buyer, invoice.currency
    totals = invoice.totals
    story = []

    seller_line = " · ".join(filter(None, [seller.name, seller.address.street, _address_line(seller.address)]))
    story += [p(seller_line, small), Spacer(1, 4 * mm)]
    buyer_block = "\n".join(filter(None, [
        buyer.name, buyer.address.street, buyer.address.additional_street,
        _address_line(buyer.address), buyer.address.country,
    ]))
    meta_rows = [
        ["Rechnungsnummer", invoice.invoice_number],
        ["Rechnungsdatum", invoice.issue_date.strftime("%d.%m.%Y")],
    ]
    if invoice.delivery_date:
        meta_rows.append(["Lieferdatum", invoice.delivery_date.strftime("%d.%m.%Y")])
    if buyer.buyer_reference:
        meta_rows.append(["Leitweg-ID / Referenz", buyer.buyer_reference])
    if buyer.id:
        meta_rows.append(["Kundennummer", buyer.id])
    if invoice.order_reference:
        meta_rows.append(["Bestellnummer", invoice.order_reference])
    header = Table(
        [[p(buyer_block), Table([[p(k, small), p(v)] for k, v in meta_rows], colWidths=[35 * mm, 45 * mm])]],
        colWidths=[90 * mm, 80 * mm],
    )
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [header, Spacer(1, 8 * mm), p(TYPE_TITLES.get(invoice.type_code, "Rechnung"), title), Spacer(1, 3 * mm)]
    for note in invoice.notes:
        story.append(p(note))
    story.append(Spacer(1, 4 * mm))

    rows = [[p(h, bold) for h in ("Pos.", "Bezeichnung", "Menge", "Einzelpreis", "USt", "Netto")]]
    for line in invoice.lines:
        name = line.description + (f"\nArt.-Nr. {line.seller_article_number}" if line.seller_article_number else "") + (
            f"\nGTIN {line.gtin}" if line.gtin else "")
        rows.append([
            p(line.position), p(name), p(f"{_de_number(line.quantity)} {line.unit}"),
            p(_de_price(line.net_price) + (f" / {_de_number(line.price_base_quantity)} {line.unit}" if line.price_base_quantity else "")),
            p(f"{_de_number(line.vat_rate)} %"), p(_de_amount(line.line_net_amount)),
        ])
    table = Table(rows, colWidths=[12 * mm, 72 * mm, 22 * mm, 24 * mm, 14 * mm, 26 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.black),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story += [table, Spacer(1, 5 * mm)]

    sums = [["Summe Positionen", _de_amount(totals.line_total, cur)]]
    if totals.allowances:
        sums.append(["Nachlässe", f"- {_de_amount(totals.allowances, cur)}"])
    if totals.charges:
        sums.append(["Zuschläge", _de_amount(totals.charges, cur)])
    sums.append(["Gesamt netto", _de_amount(totals.net_total, cur)])
    for vat in invoice.vat_breakdown:
        sums.append([f"USt {_de_number(vat.rate)} % auf {_de_amount(vat.basis_amount, cur)}", _de_amount(vat.tax_amount, cur)])
    sums.append(["Gesamt brutto", _de_amount(totals.gross_total, cur)])
    if totals.prepaid_amount:
        sums.append(["Bereits gezahlt", f"- {_de_amount(totals.prepaid_amount, cur)}"])
    sums.append(["Zahlbetrag", _de_amount(totals.payable_amount, cur)])
    sum_table = Table([[p(k), p(v, bold if k in ("Gesamt brutto", "Zahlbetrag") else base)] for k, v in sums],
                      colWidths=[60 * mm, 35 * mm], hAlign="RIGHT")
    sum_table.setStyle(TableStyle([("LINEABOVE", (0, -1), (-1, -1), 0.6, colors.black)]))
    story += [sum_table, Spacer(1, 6 * mm)]

    payment = invoice.payment
    if payment:
        if payment.terms:
            story.append(p(payment.terms))
        if payment.discount_days and payment.discount_percent:
            story.append(p(f"Bei Zahlung innerhalb von {payment.discount_days} Tagen gewähren wir {_de_number(payment.discount_percent)} % Skonto."))
        if payment.due_date:
            story.append(p(f"Fällig am {payment.due_date.strftime('%d.%m.%Y')}"))
        if payment.iban:
            story.append(p(f"IBAN {payment.iban}" + (f" · BIC {payment.bic}" if payment.bic else "")))
        story.append(Spacer(1, 6 * mm))

    footer = [seller.name, ", ".join(filter(None, [seller.address.street, _address_line(seller.address)]))]
    if seller.vat_id:
        footer.append(f"USt-IdNr.: {seller.vat_id}")
    if seller.tax_number:
        footer.append(f"Steuernummer: {seller.tax_number}")
    if seller.legal and seller.legal.as_text():
        footer.append(seller.legal.as_text())
    if seller.contact:
        footer.append(" · ".join(filter(None, [seller.contact.name, seller.contact.phone, seller.contact.email])))
    story.append(p(" | ".join(filter(None, footer)), small))

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=20 * mm, bottomMargin=20 * mm,
        title=f"Rechnung {invoice.invoice_number}", author=seller.name, subject=f"Rechnung {invoice.invoice_number}",
    )
    doc.build(story)
    return buffer.getvalue()


async def normalize_to_pdfa(content: bytes) -> Tuple[bytes, bool]:
    """Converts an uploaded PDF to PDF/A-3b with Ghostscript when EINVOICE_GHOSTSCRIPT_BIN is set."""
    settings = get_einvoice_settings()
    if not settings.ghostscript_bin:
        return content, False
    with tempfile.TemporaryDirectory(prefix="pdfa_") as workdir:
        source, target = Path(workdir) / "in.pdf", Path(workdir) / "out.pdf"
        source.write_bytes(content)
        cmd = [
            settings.ghostscript_bin, "-dPDFA=3", "-dBATCH", "-dNOPAUSE", "-dNOOUTERSAVE", "-dSAFER",
            "-sColorConversionStrategy=RGB", "-sDEVICE=pdfwrite", "-dPDFACompatibilityPolicy=1",
            f"-sOutputFile={target}", str(source),
        ]
        try:
            process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=settings.ghostscript_timeout_seconds)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            logger.warning("Ghostscript PDF/A conversion timed out")
            return content, False
        except OSError as exc:
            logger.warning("Ghostscript PDF/A conversion failed: %s", exc)
            return content, False
        if process.returncode != 0 or not target.exists():
            logger.warning("Ghostscript PDF/A conversion failed: %s", stderr.decode(errors="replace")[-500:])
            return content, False
        return target.read_bytes(), True


def embed_xml(pdf: bytes, xml: bytes, invoice: EInvoiceData) -> bytes:
    """Embeds factur-x.xml (AFRelationship=Data), adds Factur-X/PDF-A XMP metadata and the sRGB output intent."""
    return facturx.generate_from_binary(
        pdf,
        xml,
        flavor="factur-x",
        level="en16931",
        check_xsd=True,
        pdf_metadata={
            "author": invoice.seller.name,
            "keywords": "Factur-X, ZUGFeRD, Rechnung, EN 16931",
            "title": f"{invoice.seller.name}: Rechnung {invoice.invoice_number}",
            "subject": f"ZUGFeRD-Rechnung {invoice.invoice_number} vom {invoice.issue_date.strftime('%d.%m.%Y')}",
        },
        lang="de-DE",
    )


def extract_embedded_xml(pdf: bytes) -> Tuple[Optional[str], Optional[bytes]]:
    """Returns (attachment filename, xml bytes) or (None, None) when no e-invoice XML is embedded.
    Attachments are read with pypdf and NOT parsed here, so only the hardened parser in xml_parser touches them."""
    try:
        reader = PdfReader(io.BytesIO(pdf))
        if reader.is_encrypted:
            raise PdfInputError("Encrypted / password-protected PDFs are not supported")
        attachments = reader.attachments
    except PdfReadError as exc:
        raise PdfInputError(f"PDF could not be read: {exc}") from exc
    except PdfInputError:
        raise
    except Exception as exc:  # pypdf raises assorted errors for damaged name trees
        raise PdfInputError(f"PDF attachments could not be read: {exc}") from exc
    by_lower = {name.lower(): name for name in attachments}
    for wanted in EMBEDDED_XML_NAMES:
        name = by_lower.get(wanted.lower())
        if name and attachments[name]:
            return name, attachments[name][0]
    return None, None


def read_xmp(pdf: bytes) -> Dict[str, Optional[str]]:
    """Reads PDF/A identification and ZUGFeRD/Factur-X extension schema fields from the XMP packet."""
    result: Dict[str, Optional[str]] = {
        "pdfa_part": None, "pdfa_conformance": None,
        "document_type": None, "document_file_name": None, "version": None, "conformance_level": None, "namespace": None,
    }
    try:
        reader = PdfReader(io.BytesIO(pdf))
        metadata = reader.trailer["/Root"].get("/Metadata")
        if metadata is None:
            return result
        raw = metadata.get_object().get_data()
        parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
        root = etree.fromstring(raw, parser=parser)
    except Exception as exc:
        logger.debug("No readable XMP metadata: %s", exc)
        return result

    def first(local: str, ns_hint: Optional[str] = None) -> Optional[str]:
        for node in root.iter():
            if not isinstance(node.tag, str):
                continue
            qname = etree.QName(node)
            if qname.localname == local and (ns_hint is None or ns_hint in (qname.namespace or "")):
                return (node.text or "").strip() or None
        # Values may also be stored as attributes of rdf:Description
        for node in root.iter():
            if not isinstance(node.tag, str):
                continue
            for key, value in node.attrib.items():
                qname = etree.QName(key)
                if qname.localname == local and (ns_hint is None or ns_hint in (qname.namespace or "")):
                    return value
        return None

    result["pdfa_part"] = first("part", "pdfa/ns/id")
    result["pdfa_conformance"] = first("conformance", "pdfa/ns/id")
    for ns_hint in ("factur-x", "zugferd", "ferd"):
        doc_type = first("DocumentType", ns_hint)
        if doc_type or first("DocumentFileName", ns_hint):
            result["namespace"] = ns_hint
            result["document_type"] = doc_type
            result["document_file_name"] = first("DocumentFileName", ns_hint)
            result["version"] = first("Version", ns_hint)
            result["conformance_level"] = first("ConformanceLevel", ns_hint)
            break
    return result
