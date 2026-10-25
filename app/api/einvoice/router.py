"""
E-Invoicing API Endpoints (ZUGFeRD 2.x EN 16931 / XRechnung).
Mounted at /api/einvoice; POST /convert is additionally exposed at /api/v1/convert for the upload form.
"""

import base64
import hashlib
import json
import os
import re
from typing import Any, Callable, Dict, Literal, Optional, Set, Tuple

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import ValidationError

from app.core.einvoice_config import get_einvoice_settings
from app.core.rate_limiter import check_rate_limit
from app.core.security import AuthenticatedClient, verify_api_key
from app.schemas.einvoice import (
    ArchiveInfo,
    ArchiveVerification,
    CreateXRechnungResponse,
    CreateZugferdResponse,
    EInvoiceData,
    ExtractResponse,
    FormatType,
    InvoiceDataSchema,
    PdfTemplate,
    ValidationReport,
)
from app.schemas.einvoice_examples import SAMPLE_FLAT_INVOICE, SAMPLE_INVOICE_471102, SAMPLE_XRECHNUNG_INVOICE
from app.services.einvoice import service
from app.services.einvoice.archive import ArchiveFile, ArchiveUnavailableError, EInvoiceArchive
from app.services.einvoice.service import EInvoiceInputError
from app.services.einvoice.validator import analyse_upload, pydantic_errors_to_issues

TAG = "E-Invoicing (ZUGFeRD / XRechnung)"


class SizeLimitedRoute(APIRoute):
    """Rejects oversized requests from the Content-Length header *before* FastAPI parses the body.
    The per-file 10 MB check in read_upload stays authoritative (chunked requests have no Content-Length)."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def limited(request: Request):
            limit = get_einvoice_settings().max_upload_bytes
            # Room for a PDF + a JSON file part + multipart overhead
            allowed = 2 * limit + 1024 * 1024
            length = request.headers.get("content-length")
            if length and length.isdigit() and int(length) > allowed:
                return JSONResponse(status_code=413, content={
                    "id": request.headers.get("X-Request-ID", "unknown"), "status": "error", "status_code": 413,
                    "error": f"Request body exceeds {allowed // (1024 * 1024)} MB.",
                })
            return await handler(request)

        return limited


router = APIRouter(prefix="/api/einvoice", tags=[TAG], dependencies=[Depends(check_rate_limit)], route_class=SizeLimitedRoute)
convert_router = APIRouter(tags=[TAG], dependencies=[Depends(check_rate_limit)], route_class=SizeLimitedRoute)

PDF_MIME = {"application/pdf", "application/x-pdf"}
XML_MIME = {"application/xml", "text/xml", "application/x-xml"}
GENERIC_MIME = {"", "application/octet-stream", "binary/octet-stream"}

_ERROR_EXAMPLE = {
    "id": "unknown",
    "status": "error",
    "status_code": 422,
    "error": {
        "message": "Invoice violates EN 16931 / XRechnung business rules",
        "errors": [{
            "rule_id": "BR-CO-13", "field": "totals.net_total", "severity": "error", "source": "business-rules",
            "message": "Total without VAT (BT-109) = line total − allowances + charges: expected 473.00, got 470.00",
            "expected": "473.00", "actual": "470.00", "line": None,
        }],
    },
}
_REPORT_EXAMPLE = {
    "valid": True,
    "syntax": "CII",
    "profile": "EN 16931 (COMFORT)",
    "profile_urn": "urn:cen.eu:en16931:2017",
    "version": "ZUGFeRD 2.1+ / Factur-X 1.0 (XMP version 1.0)",
    "errors": [],
    "warnings": [],
    "checks": {"xml": "passed", "xsd": "passed", "business_rules": "passed", "pdf": "passed"},
    "validated_at": "2026-10-24T09:30:00Z",
}
COMMON_ERRORS: Dict[int | str, Dict[str, Any]] = {
    401: {"description": "Missing or invalid API key"},
    413: {"description": "File larger than EINVOICE_MAX_UPLOAD_SIZE_MB (default 10 MB)"},
    415: {"description": "Unsupported file type (PDF/XML signature or MIME type mismatch)"},
    422: {"description": "Field-level validation errors (rule ID, field, message)", "content": {"application/json": {"example": _ERROR_EXAMPLE}}},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _unprocessable(exc: EInvoiceInputError) -> HTTPException:
    detail: Dict[str, Any] = {"message": exc.message, "errors": [issue.model_dump() for issue in exc.errors]}
    if exc.report is not None:
        detail["validation"] = exc.report.model_dump(mode="json")
    return HTTPException(status_code=422, detail=detail)


def _detect_kind(content: bytes) -> Optional[str]:
    if content.startswith(b"%PDF-"):
        return "pdf"
    if content.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<"):
        return "xml"
    return None


async def read_upload(upload: UploadFile, allowed: Set[str], field_name: str = "file") -> Tuple[str, bytes]:
    """Reads an upload with a hard size cap and verifies magic bytes, extension and declared MIME type."""
    limit = get_einvoice_settings().max_upload_bytes
    chunks, size = [], 0
    while chunk := await upload.read(1024 * 1024):
        size += len(chunk)
        if size > limit:
            raise HTTPException(
                status_code=413,
                detail=f"'{field_name}' exceeds the maximum size of {limit // (1024 * 1024)} MB.",
            )
        chunks.append(chunk)
    content = b"".join(chunks)
    if not content:
        raise HTTPException(status_code=422, detail={
            "message": "Empty file", "errors": [{"rule_id": "FILE", "field": field_name, "message": "Uploaded file is empty"}],
        })

    kind = _detect_kind(content)
    if kind not in allowed:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"'{field_name}' must be a {' or '.join(sorted(k.upper() for k in allowed))} file (content signature check failed).",
        )
    declared = (upload.content_type or "").split(";")[0].strip().lower()
    accepted_mime = (PDF_MIME if kind == "pdf" else XML_MIME) | GENERIC_MIME
    if declared not in accepted_mime:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Declared content type '{declared}' does not match the {kind.upper()} content of '{field_name}'.",
        )
    ext = os.path.splitext(upload.filename or "")[1].lower()
    if ext and ext != f".{kind}":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"File extension '{ext}' does not match the {kind.upper()} content of '{field_name}'.",
        )
    return kind, content


async def resolve_invoice_json(invoice_data: Optional[str], invoice_file: Optional[UploadFile]) -> str:
    """Invoice JSON comes either as the text field `invoice_data` (multipart text fields are capped at 1 MB by
    Starlette) or as the file part `invoice_file` for large invoices."""
    has_file = invoice_file is not None and bool(invoice_file.filename or invoice_file.size)
    if bool(invoice_data) == has_file:
        raise HTTPException(status_code=422, detail={
            "message": "Send the invoice JSON either as 'invoice_data' (text field) or as 'invoice_file' (file upload)",
            "errors": [{"rule_id": "FILE", "field": "invoice_data", "message": "Exactly one of invoice_data / invoice_file is required"}],
        })
    if invoice_data:
        return invoice_data
    limit = get_einvoice_settings().max_upload_bytes
    content = await invoice_file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(status_code=413, detail=f"'invoice_file' exceeds the maximum size of {limit // (1024 * 1024)} MB.")
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail={
            "message": "invoice_file must be UTF-8 encoded JSON",
            "errors": [{"rule_id": "JSON", "field": "invoice_file", "message": str(exc)}],
        }) from exc


def decode_xml_text(xml: bytes) -> str:
    """Decodes XML using the encoding from its declaration (e.g. ISO-8859-1), defaulting to UTF-8."""
    match = re.match(rb"^\s*<\?xml[^>]*encoding=[\"']([A-Za-z0-9._-]+)[\"']", xml.lstrip(b"\xef\xbb\xbf"))
    encoding = match.group(1).decode("ascii") if match else "utf-8"
    try:
        return xml.decode(encoding, errors="replace").lstrip("\ufeff")
    except LookupError:
        return xml.decode("utf-8", errors="replace")


def _file_stem(invoice_number: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", invoice_number)[:80] or "invoice"


async def _archive(files: list, operation: str, invoice_number: Optional[str], client: AuthenticatedClient) -> Optional[ArchiveInfo]:
    try:
        return await EInvoiceArchive.store(files, operation=operation, invoice_number=invoice_number, owner_id=client.key_hash)
    except ArchiveUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Tamper-proof archive unavailable; nothing was issued: {exc}",
        ) from exc


def _file_headers(filename: str, content: bytes, report: ValidationReport, archive: Optional[ArchiveInfo]) -> Dict[str, str]:
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Content-SHA256": hashlib.sha256(content).hexdigest(),
        "X-Validation-Valid": str(report.valid).lower(),
        "X-Validation-Warnings": str(len(report.warnings)),
        "X-Einvoice-Profile": report.profile or "",
    }
    if archive:
        headers["X-Archive-Id"] = archive.archive_id
    return headers


ErrorMapper = Optional[Callable[[EInvoiceInputError], EInvoiceInputError]]


async def _zugferd_response(invoice: EInvoiceData, source_pdf: Optional[bytes], raw_input: bytes, output: str,
                            client: AuthenticatedClient, operation: str, error_mapper: ErrorMapper = None,
                            source_name: Optional[str] = None, template: PdfTemplate = PdfTemplate.CLASSIC):
    try:
        result = await service.create_zugferd(invoice, source_pdf, template)
    except EInvoiceInputError as exc:
        raise _unprocessable(error_mapper(exc) if error_mapper else exc) from exc
    filename = f"{_file_stem(invoice.invoice_number)}_zugferd.pdf"
    files = [ArchiveFile("input_json", "invoice_data.json", raw_input, "application/json")]
    if source_pdf is not None:
        files.append(ArchiveFile("original_pdf", source_name or "original.pdf", source_pdf, "application/pdf"))
    files.append(ArchiveFile("generated_pdf", filename, result.pdf, "application/pdf"))
    archive = await _archive(files, operation, invoice.invoice_number, client)
    if output == "json":
        return CreateZugferdResponse(
            filename=filename, pdf_base64=base64.b64encode(result.pdf).decode("ascii"),
            xml=result.xml.decode("utf-8"), validation=result.report, archive=archive,
        )
    return Response(content=result.pdf, media_type="application/pdf", headers=_file_headers(filename, result.pdf, result.report, archive))


async def _xrechnung_response(invoice: EInvoiceData, raw_input: bytes, output: str, client: AuthenticatedClient,
                              operation: str, error_mapper: ErrorMapper = None):
    try:
        result = await service.create_xrechnung(invoice)
    except EInvoiceInputError as exc:
        raise _unprocessable(error_mapper(exc) if error_mapper else exc) from exc
    filename = f"{_file_stem(invoice.invoice_number)}_xrechnung.xml"
    archive = await _archive([
        ArchiveFile("input_json", "invoice_data.json", raw_input, "application/json"),
        ArchiveFile("generated_xml", filename, result.xml, "application/xml"),
    ], operation, invoice.invoice_number, client)
    if output == "json":
        return CreateXRechnungResponse(filename=filename, xml=result.xml.decode("utf-8"), validation=result.report, archive=archive)
    return Response(content=result.xml, media_type="application/xml", headers=_file_headers(filename, result.xml, result.report, archive))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/create-zugferd",
    summary="Create a ZUGFeRD 2.x invoice (PDF/A-3 + embedded EN 16931 CII XML)",
    description=(
        "Multipart form with `invoice_data` (invoice JSON as a string, see the `EInvoiceData` schema) and an optional "
        "`file` (your existing visual PDF invoice). Without `file` a PDF is rendered from the data.\n\n"
        "The server recalculates all line amounts, the VAT breakdown and totals and rejects mismatches (HTTP 422), "
        "generates CII XML for profile **EN 16931 (COMFORT)** (`urn:cen.eu:en16931:2017`), validates it (XSD + EN 16931 "
        "business rules), embeds it as `factur-x.xml` (AFRelationship=Data) with PDF/A-3 + Factur-X XMP metadata and "
        "validates the final PDF again.\n\n"
        "- `output=pdf` (default): returns the PDF; validation summary in `X-Validation-*` headers.\n"
        "- `output=json`: returns `{pdf_base64, xml, validation, archive}`.\n\n"
        "`template` picks the design of the rendered PDF (`classic`, `modern`, `minimal`); it is ignored when `file` is sent.\n\n"
        "Input JSON, uploaded PDF and generated PDF are archived unmodified with SHA-256 hashes (`X-Archive-Id`)."
    ),
    responses={
        200: {
            "description": "ZUGFeRD PDF (or JSON with ?output=json)",
            "content": {"application/pdf": {}, "application/json": {"schema": CreateZugferdResponse.model_json_schema()}},
        },
        **COMMON_ERRORS,
    },
)
async def create_zugferd(
    invoice_data: Optional[str] = Form(
        default=None, description="Invoice JSON (EInvoiceData schema) as a string (max 1 MB; use invoice_file above)",
        examples=[json.dumps(SAMPLE_INVOICE_471102, ensure_ascii=False)],
    ),
    invoice_file: Optional[UploadFile] = File(default=None, description="Alternative to invoice_data: the invoice JSON as a file"),
    file: Optional[UploadFile] = File(default=None, description="Optional visual invoice PDF (max 10 MB)"),
    output: Literal["pdf", "json"] = Query(default="pdf", description="Response format"),
    template: PdfTemplate = Query(default=PdfTemplate.CLASSIC, description="Design of the rendered PDF (ignored when a PDF is uploaded)"),
    client: AuthenticatedClient = Depends(verify_api_key),
):
    source_pdf = None
    if file is not None and (file.filename or file.size):
        _, source_pdf = await read_upload(file, {"pdf"})
    raw_json = await resolve_invoice_json(invoice_data, invoice_file)
    try:
        invoice = service.parse_invoice_json(raw_json)
    except EInvoiceInputError as exc:
        raise _unprocessable(exc) from exc
    return await _zugferd_response(invoice, source_pdf, raw_json.encode("utf-8"), output, client, "create-zugferd",
                                   source_name=file.filename if source_pdf is not None else None, template=template)


@router.post(
    "/create-xrechnung",
    summary="Create an XRechnung 3.x invoice (pure CII XML, B2G)",
    description=(
        "JSON body with the same `EInvoiceData` schema. XRechnung additionally requires the Leitweg-ID in "
        "`buyer.buyer_reference` (BR-DE-15), seller contact name/phone/email (BR-DE-2/5/6/7), payment instructions "
        "(BR-DE-1), and seller/buyer electronic addresses (BT-34/BT-49).\n\n"
        "Validation: XSD, EN 16931 + BR-DE business rules, and the **KoSIT validator** when configured "
        "(`EINVOICE_KOSIT_JAR`, `EINVOICE_KOSIT_SCENARIOS`).\n\n"
        "- `output=xml` (default): returns the XML file.\n- `output=json`: returns `{xml, validation, archive}`."
    ),
    responses={
        200: {
            "description": "XRechnung XML (or JSON with ?output=json)",
            "content": {"application/xml": {}, "application/json": {"schema": CreateXRechnungResponse.model_json_schema()}},
        },
        **COMMON_ERRORS,
    },
)
async def create_xrechnung(
    request: Request,
    invoice: EInvoiceData = Body(..., openapi_examples={"sample_471102": {"summary": "Sample invoice 471102 (XRechnung)", "value": SAMPLE_XRECHNUNG_INVOICE}}),
    output: Literal["xml", "json"] = Query(default="xml", description="Response format"),
    client: AuthenticatedClient = Depends(verify_api_key),
):
    return await _xrechnung_response(invoice, await request.body(), output, client, "create-xrechnung")


@router.post(
    "/extract",
    response_model=ExtractResponse,
    response_model_exclude_none=True,
    summary="Extract structured data from a ZUGFeRD PDF or e-invoice XML",
    description=(
        "Upload a ZUGFeRD/Factur-X PDF or a CII/UBL XML (ZUGFeRD, XRechnung). Returns the parsed invoice in the same "
        "JSON shape the create endpoints accept, the raw XML, the detected profile (MINIMUM … EN 16931, XRECHNUNG) and "
        "version (from the attachment name and XMP metadata). The upload is archived unmodified."
    ),
    responses=COMMON_ERRORS,
)
async def extract(
    file: UploadFile = File(..., description="ZUGFeRD PDF or XML (max 10 MB)"),
    archive: bool = Query(default=True, description="Archive the received original (GoBD)"),
    client: AuthenticatedClient = Depends(verify_api_key),
):
    kind, content = await read_upload(file, {"pdf", "xml"})
    analysis = await analyse_upload(content, kind)
    if analysis.raw_xml is None or analysis.info is None or analysis.info.syntax == "UNKNOWN":
        report = analysis.report()
        raise HTTPException(status_code=422, detail={
            "message": "No e-invoice XML could be extracted", "errors": [e.model_dump() for e in report.errors],
        })
    report = analysis.report()
    archive_info = None
    if archive:
        archive_info = await _archive(
            [ArchiveFile("uploaded_document", file.filename or f"upload.{kind}", content, "application/pdf" if kind == "pdf" else "application/xml")],
            "extract", (analysis.invoice or {}).get("invoice_number"), client,
        )
    invoice = analysis.model.model_dump(mode="json") if analysis.model else analysis.invoice
    return ExtractResponse(
        filename=file.filename or "",
        source_type=kind,
        syntax=report.syntax,
        profile=report.profile,
        profile_urn=report.profile_urn,
        version=report.version,
        embedded_filename=analysis.embedded_filename,
        xmp=analysis.xmp,
        invoice=invoice,
        raw_xml=decode_xml_text(analysis.raw_xml),
        warnings=report.errors + report.warnings,
        archive=archive_info,
    )


@router.post(
    "/validate",
    response_model=ValidationReport,
    summary="Validate a ZUGFeRD PDF or e-invoice XML",
    description=(
        "Checks: XML well-formedness (DTD/entities rejected), syntax/profile detection, XSD for the detected profile, "
        "EN 16931 business rules incl. server-side recalculation (BR-CO-10 … BR-CO-16, BR-S-08/09), XRechnung BR-DE rules, "
        "the KoSIT validator for XRechnung (when configured) and, for PDFs, embedded XML / PDF/A-3 / XMP checks.\n\n"
        "Always returns HTTP 200 with `valid` true/false and lists of errors and warnings (rule ID, field, message)."
    ),
    responses={200: {"content": {"application/json": {"example": _REPORT_EXAMPLE}}}, **COMMON_ERRORS},
)
async def validate(
    file: UploadFile = File(..., description="ZUGFeRD PDF or XML (max 10 MB)"),
    archive: bool = Query(default=False, description="Also archive the uploaded original"),
    client: AuthenticatedClient = Depends(verify_api_key),
) -> ValidationReport:
    kind, content = await read_upload(file, {"pdf", "xml"})
    analysis = await analyse_upload(content, kind)
    if archive:
        await _archive(
            [ArchiveFile("uploaded_document", file.filename or f"upload.{kind}", content, "application/pdf" if kind == "pdf" else "application/xml")],
            "validate", (analysis.invoice or {}).get("invoice_number"), client,
        )
    return analysis.report()


@router.get(
    "/archive/{archive_id}",
    response_model=ArchiveVerification,
    summary="Verify an archived record (hashes + ledger chain)",
    description="Recomputes the SHA-256 of every archived file, the manifest and its hash-chained ledger entry. Only the API key that created the record can read it.",
    responses={404: {"description": "Unknown archive ID or not owned by this API key"}},
)
async def verify_archive(archive_id: str, client: AuthenticatedClient = Depends(verify_api_key)) -> ArchiveVerification:
    record = await EInvoiceArchive.verify(archive_id, client.key_hash)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Archive record '{archive_id}' not found.")
    return record


@convert_router.post(
    "/convert",
    summary="Convert a PDF + flat form data into ZUGFeRD (PDF) or XRechnung (XML)",
    description=(
        "Simplified form endpoint: multipart `file` (existing PDF invoice; optional for XRechnung, and for ZUGFeRD a PDF "
        "is rendered when omitted) and `invoice_data` (JSON string, `InvoiceDataSchema`). `format_type` selects the "
        "output. Addresses are given as 'Street No, Postcode City, Country'. For a valid XRechnung also send "
        "`routing_id`, `seller_contact_name`, `seller_phone`, `seller_email`, `buyer_email` and `iban`. `template` picks the design of a rendered ZUGFeRD PDF."
    ),
    responses={200: {"content": {"application/pdf": {}, "application/xml": {}}}, **COMMON_ERRORS},
)
async def convert(
    invoice_data: Optional[str] = Form(default=None, description="Flat invoice JSON (InvoiceDataSchema) as a string",
                                       examples=[json.dumps(SAMPLE_FLAT_INVOICE, ensure_ascii=False)]),
    invoice_file: Optional[UploadFile] = File(default=None, description="Alternative to invoice_data: the JSON as a file"),
    file: Optional[UploadFile] = File(default=None, description="Visual invoice PDF (max 10 MB)"),
    template: PdfTemplate = Query(default=PdfTemplate.CLASSIC, description="Design of the rendered PDF (ignored when a PDF is uploaded)"),
    client: AuthenticatedClient = Depends(verify_api_key),
):
    source_pdf = None
    if file is not None and (file.filename or file.size):
        _, source_pdf = await read_upload(file, {"pdf"})
    invoice_data = await resolve_invoice_json(invoice_data, invoice_file)
    try:
        flat = InvoiceDataSchema.model_validate_json(invoice_data)
    except ValidationError as exc:
        raise _unprocessable(EInvoiceInputError("Invoice data validation failed", pydantic_errors_to_issues(exc, prefix="invoice_data"))) from exc
    try:
        invoice = service.flat_to_invoice(flat)
    except EInvoiceInputError as exc:
        raise _unprocessable(exc) from exc

    raw = invoice_data.encode("utf-8")
    if flat.format_type == FormatType.XRECHNUNG:
        return await _xrechnung_response(invoice, raw, "xml", client, "convert-xrechnung", service.remap_flat_errors)
    return await _zugferd_response(invoice, source_pdf, raw, "pdf", client, "convert-zugferd", service.remap_flat_errors,
                                   source_name=file.filename if source_pdf is not None else None, template=template)
