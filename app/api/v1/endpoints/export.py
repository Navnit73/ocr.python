"""
Export API Endpoints for Excel (.xlsx), CSV, and PDF downloads.
"""

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.schemas.export import DirectExportRequest, ExportFormatEnum
from app.services.export_service import ExportService
from app.services.result_cache import ResultCache

router = APIRouter(prefix="/export", tags=["Export & Download"])


@router.get(
    "/download/{id}",
    summary="Download Extracted Data by Document ID",
    description=(
        "Download previously extracted document data by its unique Request ID. "
        "Choose between beautiful Excel (.xlsx), CSV (.csv), or PDF (.pdf) formats."
    ),
    responses={
        200: {
            "description": "File stream for download",
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {},
                "text/csv": {},
                "application/pdf": {},
            },
        },
        404: {"description": "Document ID not found or expired"},
    },
)
async def download_by_id(
    id: str,
    format: ExportFormatEnum = Query(
        default=ExportFormatEnum.XLSX,
        description="Desired download format: xlsx, csv, or pdf",
    ),
):
    """
    Retrieves cached document extraction by ID and generates a styled Excel, CSV, or PDF file.
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
        "Generate a styled Excel (.xlsx), CSV (.csv), or PDF (.pdf) directly from an extraction JSON payload."
    ),
    responses={
        200: {
            "description": "File stream for download",
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {},
                "text/csv": {},
                "application/pdf": {},
            },
        }
    },
)
async def generate_direct_export(
    payload: DirectExportRequest,
    format: ExportFormatEnum = Query(
        default=ExportFormatEnum.XLSX,
        description="Desired download format: xlsx, csv, or pdf",
    ),
):
    """
    Converts a JSON extraction payload directly into a downloadable Excel, CSV, or PDF file.
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
