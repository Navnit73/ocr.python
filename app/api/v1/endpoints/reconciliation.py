"""
Bank Reconciliation API Endpoints.
Upload a bank statement and a business ledger, then poll one reconciliation ID for status,
the saved report, paginated transaction details, and XLSX/CSV exports.
Works for API-key callers (owner-scoped) and guests (secret access token, no registration).
"""

import asyncio
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, Response, Security, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel

from app.core.rate_limiter import check_rate_limit
from app.core.security import api_key_header_scheme, http_bearer_scheme, verify_api_key
from app.db.repositories.reconciliation_repo import ReconciliationRepository
from app.schemas.reconciliation import (
    ReconciliationCategoryEnum,
    ReconciliationExportFormatEnum,
    ReconciliationStatusResponse,
    ReconciliationTransactionsResponse,
    ReconciliationUploadResponse,
)
from app.services.reconciliation_engine import ReconciliationOptions
from app.services.reconciliation_service import (
    RESULT_SECTIONS,
    ROLES,
    ReconciliationService,
    as_utc,
    hash_access_token,
)

router = APIRouter(prefix="/reconciliation", tags=["Bank Reconciliation"])

RECONCILIATION_ID_PATTERN = re.compile(r"^rec_[a-f0-9]{8,32}$")
TOKEN_HEADER = "X-Reconciliation-Token"


class ReconciliationCaller(BaseModel):
    """Either an authenticated API client (owner_id set) or a guest presenting an access token."""
    owner_id: Optional[str] = None
    guest_token: Optional[str] = None


async def resolve_caller(
    request: Request,
    header_api_key: Optional[str] = Security(api_key_header_scheme),
    bearer_credentials: Optional[HTTPAuthorizationCredentials] = Security(http_bearer_scheme),
    reconciliation_token: Optional[str] = Header(
        default=None,
        alias=TOKEN_HEADER,
        description="Guest access token returned once by the upload endpoint",
    ),
) -> ReconciliationCaller:
    """API keys are optional here: a supplied key is fully validated (401 if invalid); no key means guest."""
    if (header_api_key and header_api_key.strip()) or (bearer_credentials and bearer_credentials.credentials.strip()):
        client = await verify_api_key(request, header_api_key, bearer_credentials, None)
        return ReconciliationCaller(owner_id=client.key_hash)
    token = (reconciliation_token or "").strip() or None
    return ReconciliationCaller(guest_token=token)


async def load_authorized_record(
    reconciliation_id: str,
    caller: ReconciliationCaller,
    include_report_sections: bool = True,
) -> Dict[str, Any]:
    """Enforces access on every read. Unknown IDs and wrong credentials both return 404 to prevent enumeration."""
    not_found = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Reconciliation '{reconciliation_id}' was not found or access is unauthorized.",
    )
    if not RECONCILIATION_ID_PATTERN.match(reconciliation_id):
        raise not_found
    projection: Dict[str, Any] = {"_id": 0, "webhook.secret": 0}
    if not include_report_sections:
        projection.update({f"result.{section}": 0 for section in RESULT_SECTIONS.values()})
    record = await ReconciliationRepository.get(reconciliation_id, projection)
    if not record:
        raise not_found

    if record.get("owner_id"):
        if not caller.owner_id or not secrets.compare_digest(caller.owner_id, record["owner_id"]):
            raise not_found
    else:
        stored_hash = record.get("access_token_hash")
        if not caller.guest_token or not stored_hash or not secrets.compare_digest(
            hash_access_token(caller.guest_token), stored_hash
        ):
            raise not_found
        expires_at = as_utc(record.get("expires_at"))
        if expires_at and expires_at <= datetime.now(timezone.utc):
            raise HTTPException(status_code=status.HTTP_410_GONE, detail="Guest access to this reconciliation has expired.")

    record.pop("access_token_hash", None)
    return record


def require_completed(record: Dict[str, Any]) -> None:
    if record.get("status") != "completed" or not record.get("result"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Reconciliation is not completed yet (status: {record.get('status')}).",
        )


@router.post(
    "/upload",
    response_model=ReconciliationUploadResponse,
    response_model_exclude_none=True,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload bank statement + business ledger and start reconciliation (HTTP 202)",
    description=(
        "Accepts a bank statement and a business ledger (PDF, JPG, PNG, WEBP, TIFF, CSV or XLSX), queues OCR "
        "extraction for each document on the background workers, and returns a `reconciliation_id` immediately.\n\n"
        "- **Authenticated** (`X-API-Key` / `Authorization: Bearer`): the reconciliation is owned by your key.\n"
        "- **Guest** (no key): an `access_token` is returned **once**; send it as `X-Reconciliation-Token` on later calls.\n\n"
        "Optional `webhook_url` (HTTPS, public hosts only) receives `reconciliation.completed` / `reconciliation.failed`, "
        "signed with `X-Webhook-Signature: sha256=<hmac>` when `webhook_secret` is set."
    ),
    dependencies=[Depends(check_rate_limit)],
)
async def upload_reconciliation(
    bank_statement: UploadFile = File(..., description="Bank statement: PDF (up to 200 pages), image, CSV or XLSX"),
    business_ledger: UploadFile = File(..., description="Business ledger / cash book: PDF, image, CSV or XLSX"),
    webhook_url: Optional[str] = Form(default=None, description="HTTPS endpoint for completion/failure notifications"),
    webhook_secret: Optional[str] = Form(default=None, description="Secret for HMAC-SHA256 payload signatures"),
    date_tolerance_days: int = Form(default=2, ge=0, le=31, description="Max days between matching bank and ledger dates"),
    description_matching: bool = Form(default=True, description="Use description similarity when scoring matches"),
    user_email: Optional[str] = Form(default=None, description="Logged-in user email (authenticated callers only)"),
    caller: ReconciliationCaller = Depends(resolve_caller),
) -> ReconciliationUploadResponse:
    validated_webhook: Optional[str] = None
    if webhook_url and webhook_url.strip():
        try:
            validated_webhook = await asyncio.to_thread(ReconciliationService.validate_webhook_url, webhook_url)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if webhook_secret and len(webhook_secret) > 256:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="webhook_secret must be at most 256 characters.")

    documents = {}
    for role, upload in zip(ROLES, (bank_statement, business_ledger)):
        kind, content = await ReconciliationService.validate_document(upload, role)
        documents[role] = (kind, upload.filename, content, upload.content_type)

    record, access_token = await ReconciliationService.create_reconciliation(
        documents=documents,
        options=ReconciliationOptions(date_tolerance_days=date_tolerance_days, description_matching=description_matching),
        owner_id=caller.owner_id,
        owner_email=user_email if caller.owner_id else None,
        webhook_url=validated_webhook,
        webhook_secret=webhook_secret if validated_webhook else None,
    )
    reconciliation_id = record["reconciliation_id"]
    return ReconciliationUploadResponse(
        reconciliation_id=reconciliation_id,
        message="Documents uploaded. Reconciliation has been queued.",
        status_url=f"/api/v1/reconciliation/{reconciliation_id}",
        access_token=access_token,
        access_expires_at=record.get("expires_at") if access_token else None,
    )


@router.get(
    "/{reconciliation_id}",
    response_model=ReconciliationStatusResponse,
    summary="Get reconciliation status, progress and the saved report",
    description="Poll this endpoint until `status` is `completed` or `failed`. Results are read from MongoDB; nothing is reprocessed.",
    dependencies=[Depends(check_rate_limit)],
)
async def get_reconciliation(
    reconciliation_id: str,
    caller: ReconciliationCaller = Depends(resolve_caller),
) -> ReconciliationStatusResponse:
    record = await load_authorized_record(reconciliation_id, caller)
    return ReconciliationStatusResponse(**await ReconciliationService.build_status(record))


@router.get(
    "/{reconciliation_id}/transactions",
    response_model=ReconciliationTransactionsResponse,
    response_model_exclude_none=True,
    summary="Get paginated reconciliation transaction details",
    description=(
        "Filter with `status` = matched, unmatched_bank, unmatched_ledger, possible_duplicates, or amount_mismatches. "
        "Without a filter, all categories are returned in report order, each item tagged with `category`."
    ),
    dependencies=[Depends(check_rate_limit)],
)
async def get_reconciliation_transactions(
    reconciliation_id: str,
    category: Optional[ReconciliationCategoryEnum] = Query(default=None, alias="status", description="Category filter"),
    page: int = Query(default=1, ge=1, le=100000),
    page_size: int = Query(default=50, ge=1, le=500),
    caller: ReconciliationCaller = Depends(resolve_caller),
) -> ReconciliationTransactionsResponse:
    record = await load_authorized_record(reconciliation_id, caller, include_report_sections=False)
    require_completed(record)
    total, items = await ReconciliationService.get_transactions(
        record, category.value if category else None, page, page_size
    )
    return ReconciliationTransactionsResponse(
        reconciliation_id=reconciliation_id,
        status=category,
        total=total,
        page=page,
        page_size=page_size,
        transactions=items,
    )


@router.get(
    "/{reconciliation_id}/export",
    summary="Download the reconciliation report (XLSX or CSV)",
    description="Builds the report from the saved result: summary, balance verification, every discrepancy category and warnings.",
    responses={
        200: {
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {},
                "text/csv": {},
            }
        }
    },
    dependencies=[Depends(check_rate_limit)],
)
async def export_reconciliation(
    reconciliation_id: str,
    fmt: ReconciliationExportFormatEnum = Query(default=ReconciliationExportFormatEnum.XLSX, alias="format"),
    caller: ReconciliationCaller = Depends(resolve_caller),
) -> Response:
    record = await load_authorized_record(reconciliation_id, caller)
    require_completed(record)
    content, media_type, filename = await asyncio.to_thread(ReconciliationService.build_export, record, fmt.value)
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
