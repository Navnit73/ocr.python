"""
Export API Endpoints for Excel (.xlsx), CSV, PDF, OFX (QuickBooks), QBO, QIF, and Consolidation.
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query, Response, status

from app.schemas.batch import ConsolidationRequest, ConsolidationResponse
from app.schemas.export import DirectExportRequest, ExportFormatEnum
from app.services.consolidation_service import ConsolidationService
from app.services.export_service import ExportService
from app.services.result_cache import ResultCache

router = APIRouter(prefix="/export", tags=["Export & Accounting Downloads"])


@router.get(
    "/download/{id}",
    summary="Download Extracted Data by Document ID",
    description=(
        "Download previously extracted document data by its unique Request ID.\n\n"
        "**Supported Formats**:\n"
        "- `xlsx`: Executive multi-sheet Excel with Dashboard & Ledger\n"
        "- `pdf`: Fintech-styled executive PDF report with KPI cards & category progress bars\n"
        "- `csv`: Standard CSV with categorised transactions\n"
        "- `ofx`: Open Financial Exchange format for QuickBooks, Xero, Tally, Zoho Books\n"
        "- `qbo`: QuickBooks Online 1-click import format\n"
        "- `qif`: Quicken Interchange Format"
    ),
    responses={
        200: {
            "description": "File stream for download",
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {},
                "application/pdf": {},
                "text/csv": {},
                "application/x-ofx": {},
                "application/vnd.intu.qbo": {},
                "application/x-qif": {},
            },
        },
        404: {"description": "Document ID not found or expired"},
    },
)
async def download_by_id(
    id: str,
    format: ExportFormatEnum = Query(
        default=ExportFormatEnum.XLSX,
        description="Desired format: xlsx, pdf, csv, ofx, qbo, or qif",
    ),
):
    """
    Retrieves cached document extraction by ID and generates the selected file format.
    """
    cached = ResultCache.get(id)
    if not cached:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Extraction result with ID '{id}' was not found or has expired from temporary memory. "
                "You can re-upload the document or use POST /api/v1/export/generate with the extraction payload."
            ),
        )

    doc_type = cached.get("document_type", "general")
    extraction = cached.get("extraction", {})
    raw_text = cached.get("raw_text", "")

    file_bytes, media_type, filename = ExportService.generate_export(
        doc_id=id,
        document_type=doc_type,
        extraction=extraction,
        export_format=format.value,
        raw_text=raw_text,
    )

    return Response(
        content=file_bytes,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )


@router.post(
    "/generate",
    summary="Directly Generate and Download Export from JSON Payload",
    description=(
        "Generate an Excel (.xlsx), PDF (.pdf), CSV (.csv), OFX (.ofx), QBO (.qbo), or QIF (.qif) directly from an extraction JSON payload."
    ),
    responses={
        200: {
            "description": "File stream for download",
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {},
                "application/pdf": {},
                "text/csv": {},
                "application/x-ofx": {},
                "application/vnd.intu.qbo": {},
                "application/x-qif": {},
            },
        }
    },
)
async def generate_direct_export(
    payload: DirectExportRequest,
    format: ExportFormatEnum = Query(
        default=ExportFormatEnum.XLSX,
        description="Desired format: xlsx, pdf, csv, ofx, qbo, or qif",
    ),
):
    """
    Converts a JSON extraction payload directly into a downloadable file.
    """
    file_bytes, media_type, filename = ExportService.generate_export(
        doc_id=payload.id,
        document_type=payload.document_type,
        extraction=payload.extraction,
        export_format=format.value,
        raw_text=payload.raw_text or "",
    )

    return Response(
        content=file_bytes,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )


@router.post(
    "/consolidate",
    summary="Consolidate Multiple Statements into 12-Month Annual Report",
    description=(
        "Merges multiple monthly bank statements or receipts into a consolidated annual financial summary.\n\n"
        "- If `as_excel=true` (default): Returns a downloadable `.xlsx` workbook with Monthly P&L and Master Ledger.\n"
        "- If `as_excel=false`: Returns consolidated JSON analytics with monthly trends."
    ),
)
async def consolidate_statements(
    payload: ConsolidationRequest,
    as_excel: bool = Query(default=True, description="Whether to return a downloadable Excel spreadsheet or JSON response"),
):
    """
    Consolidates multiple extractions into an Annual / Multi-Month report.
    """
    extractions = []

    # Gather extractions from cache IDs if provided
    if payload.request_ids:
        for rid in payload.request_ids:
            c = ResultCache.get(rid)
            if c and c.get("extraction"):
                extractions.append(c["extraction"])

    # Gather direct extractions if provided
    if payload.extractions:
        extractions.extend(payload.extractions)

    if not extractions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No valid statements found to consolidate. Provide 'request_ids' or 'extractions'.",
        )

    cons_id = f"consolidation_{len(extractions)}_statements"
    summary = ConsolidationService.consolidate(
        extractions=extractions,
        consolidation_id=cons_id,
        title=payload.title or "Annual Financial Consolidation",
    )

    if not as_excel:
        return summary

    file_bytes = ConsolidationService.generate_consolidated_excel(summary, extractions)
    filename = f"Annual_Consolidation_{cons_id}.xlsx"
    return Response(
        content=file_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )
