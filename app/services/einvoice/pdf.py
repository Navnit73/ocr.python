"""
PDF handling for ZUGFeRD: uploaded PDF checks, optional PDF/A-3 normalisation,
XML embedding (factur-x.xml via the factur-x library) and extraction of embedded XML + XMP metadata.
The visual invoice itself is rendered in pdf_templates.
"""

import asyncio
import io
import logging
import tempfile
from pathlib import Path
from typing import Dict, Optional, Tuple

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
