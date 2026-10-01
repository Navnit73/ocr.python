# AI OCR Advance API

A lightweight, enterprise-grade, high-performance, stateless AI-powered OCR & Financial Document Extraction API built with **FastAPI**, **PyMuPDF**, **PaddleOCR**, **OpenCV**, and the **DeepSeek API**.

The API accepts large multi-page PDF documents (up to **200 pages** and **100MB**) and images, extracts text via OCR, cleans and normalizes OCR artifacts using DeepSeek, structures financial entities into validated Pydantic models, and generates executive downloads for Excel, PDF, CSV, and accounting software (**QuickBooks QBO**, **OFX**, **Quicken QIF**).

---

## ✨ Key Features & Capabilities

- **🔑 Enterprise API Key Auth**: Required on all extraction and export routes via `X-API-Key` header or `Authorization: Bearer <key>`. Constant-time verification prevents timing attacks.
- **🛡️ In-Memory Sliding Window Rate Limiting**: Protects against flood attacks & API abuse with automatic HTTP 429 responses and `Retry-After` headers.
- **🔒 IDOR & Cross-Tenant Defense**: Document IDs and cached extraction results are cryptographically scoped to the caller's API key. Access attempts across API keys are blocked with `403 Forbidden`.
- **🧪 Formula Injection Sanitization**: All exported CSV, Excel, and accounting files automatically neutralize spreadsheet formula injection (`=`, `@`, `+`, `-`, `\t`, `\r`).
- **🚀 High Capacity**: Supports documents up to **200 pages** per PDF and up to **100MB** payload size.
- **⚡ Password-Protected PDFs**: Decrypt and extract password-protected statements and invoices on-the-fly.
- **🧠 3-Layer Output Architecture**:
  - **Layer 1 (Raw OCR)**: Exact, unmodified text extracted via PyMuPDF (digital) or PaddleOCR (scanned).
  - **Layer 2 (Cleaned Text)**: DeepSeek-corrected and normalized text with prompt-injection defense.
  - **Layer 3 (Structured JSON)**: Typed Pydantic models for bank statements, receipts, invoices, and general documents with financial balance audits.
- **💼 Accounting Direct Exports**:
  - **QuickBooks Online (`.qbo`)**: 1-click import into QuickBooks with `<INTU.BID>3000`.
  - **Open Financial Exchange (`.ofx`)**: Compatible with Xero, Zoho Books, Tally, Sage.
  - **Quicken Interchange (`.qif`)**: Standard desktop Quicken import.
  - **Executive Excel (`.xlsx`)**: Multi-sheet workbook with KPI Dashboard, Expense Breakdown, and Transaction Ledger.
  - **Fintech PDF (`.pdf`)**: Corporate executive report with KPI cards, category progress bars, and audit summary.
  - **Standard CSV (`.csv`)**: RFC 4180 compliant CSV.
- **📦 Batch Processing & Zip Archives (`POST /api/v1/ocr/batch`)**:
  - Upload up to 50 files or a single `.zip` file in a single request.
  - Concurrently processes all documents with worker thread isolation.
- **🔔 Async Webhook Callbacks**:
  - Pass `callback_url` to return `202 Accepted` immediately.
  - Asynchronously dispatches HMAC-SHA256 signed JSON payloads (`X-Webhook-Signature`).
- **📈 Multi-Statement Annual Consolidation (`POST /api/v1/export/consolidate`)**:
  - Merge up to 12+ monthly statements into a single annual P&L cashflow workbook with transaction deduplication.

---

## 🌐 Interactive Swagger UI & API Docs

FastAPI provides an interactive OpenAPI / Swagger UI testbed out of the box:

- **Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc UI**: [http://localhost:8000/redoc](http://localhost:8000/redoc)
- **OpenAPI JSON**: [http://localhost:8000/openapi.json](http://localhost:8000/openapi.json)

### How to Authenticate & Test in Swagger UI (`/docs`)
1. Open [http://localhost:8000/docs](http://localhost:8000/docs) in your browser.
2. Click the green **"Authorize"** button (top-right of Swagger UI).
3. Enter your API Key in either `api_key_header_scheme` (`X-API-Key`) or `http_bearer_scheme` (Bearer token) (e.g. `ocr_dev_key_secret_2026`).
4. Click **"Authorize"**, then click **"Close"**.
5. Click on any endpoint (e.g., `POST /api/v1/ocr/extract`) and click **"Try it out"**.
6. Upload your file and click **"Execute"**!

---

## 📡 API Reference & Endpoints

All functional endpoints require API key authentication via header:
- `X-API-Key: <YOUR_API_KEY>` or
- `Authorization: Bearer <YOUR_API_KEY>`

### 1. Document OCR & Extraction

#### `POST /api/v1/ocr/extract`
Extracts text and structured financial data from a single document.

- **Parameters (`multipart/form-data`)**:
  - `file` (*required*, file): PDF (up to 200 pages), JPG, PNG, WEBP, or TIFF.
  - `document_type` (string, default `auto`): `auto`, `bank_statement`, `receipt`, `invoice`, `general`.
  - `language` (string, default `en`): `auto`, `en`, `hi`, `es`, `fr`, `de`, `ch`.
  - `clean_with_ai` (boolean, default `true`): DeepSeek cleaning & structured extraction.
  - `request_id` (string, optional): Client-provided request tracking ID.
  - `password` (string, optional): Password for encrypted PDF files.
  - `callback_url` (string, optional): Webhook URL for asynchronous delivery (`202 Accepted`).
  - `callback_secret` (string, optional): HMAC secret for `X-Webhook-Signature` validation.

- **Example curl**:
```bash
curl -X POST "http://localhost:8000/api/v1/ocr/extract" \
  -H "X-API-Key: ocr_dev_key_secret_2026" \
  -F "file=@statement_jan2026.pdf" \
  -F "document_type=bank_statement" \
  -F "clean_with_ai=true"
```

---

#### `POST /api/v1/ocr/batch`
Processes up to 50 documents or a `.zip` archive containing invoices, receipts, or statements in parallel.

- **Parameters (`multipart/form-data`)**:
  - `files` (*required*, list of files or `.zip`): Batch of documents.
  - `document_type` (string, default `auto`): Target document type hint.
  - `language` (string, default `en`): OCR language hint.
  - `clean_with_ai` (boolean, default `true`): DeepSeek processing.

- **Example curl**:
```bash
curl -X POST "http://localhost:8000/api/v1/ocr/batch" \
  -H "X-API-Key: ocr_dev_key_secret_2026" \
  -F "files=@invoices_q1.zip" \
  -F "document_type=invoice"
```

---

### 2. Export & Accounting Downloads

#### `GET /api/v1/export/download/{id}?format=xlsx|pdf|csv|ofx|qbo|qif`
Download an extracted document directly using its unique Request ID (protected with owner verification).

- **Query Parameters**:
  - `format` (default `xlsx`): `xlsx`, `pdf`, `csv`, `ofx`, `qbo`, `qif`.

- **Example curl**:
```bash
# Download Excel
curl -OJ "http://localhost:8000/api/v1/export/download/req_12345?format=xlsx" \
  -H "X-API-Key: ocr_dev_key_secret_2026"

# Download QuickBooks Online (.qbo)
curl -OJ "http://localhost:8000/api/v1/export/download/req_12345?format=qbo" \
  -H "X-API-Key: ocr_dev_key_secret_2026"

# Download OFX for Xero / Tally
curl -OJ "http://localhost:8000/api/v1/export/download/req_12345?format=ofx" \
  -H "X-API-Key: ocr_dev_key_secret_2026"
```

---

#### `POST /api/v1/export/generate?format=xlsx|pdf|csv|ofx|qbo|qif`
Generate a styled file stream directly from an extraction JSON payload.

- **Request Body**:
```json
{
  "id": "doc_991",
  "document_type": "bank_statement",
  "extraction": {
    "bank_name": "Chase Bank",
    "account_holder": "Jane Doe",
    "account_number_masked": "XXXX-1234",
    "currency": "USD",
    "statement_period": "2026-01-01 to 2026-01-31",
    "opening_balance": 10000.00,
    "closing_balance": 12500.00,
    "transactions": [
      {
        "date": "2026-01-15",
        "description": "Consulting Revenue",
        "credit": 3000.00,
        "debit": null,
        "balance": 13000.00
      },
      {
        "date": "2026-01-20",
        "description": "Software Subscription",
        "credit": null,
        "debit": 500.00,
        "balance": 12500.00
      }
    ]
  }
}
```

---

#### `POST /api/v1/export/consolidate?as_excel=true`
Consolidates multiple extractions into an Annual / Multi-Month report.

- **Request Body**:
```json
{
  "title": "Fiscal Year 2026 Consolidated P&L",
  "request_ids": ["req_jan", "req_feb", "req_mar"]
}
```

---

## 🛡️ Security Audit & Threat Hardening Matrix

| Attack Vector | Attacker Objective | Potential Damage | Implemented Remediation |
|---|---|---|---|
| **1. IDOR / ID Manipulation** | Guess/enumerate `id` in `/export/download/{id}` or `/export/consolidate` | Cross-tenant financial data theft | Scoped `owner_hash` tracking in `ResultCache`. Requests from a different API key are blocked with `403 Forbidden`. |
| **2. Auth Bypass / No Token** | Call endpoints without authentication or with expired/forged keys | Unauthorized extraction & resource drain | `verify_api_key` dependency with constant-time comparison (`secrets.compare_digest`). Rejects unauthenticated calls with `401 Unauthorized`. |
| **3. Privilege Escalation** | Forge roles in request headers or body | Unauthorized access | Stateless token-level API key authorization enforced server-side. No implicit elevated permissions. |
| **4. Feature & Resource Abuse** | Flood API with rapid requests or oversized uploads (DoS/DDoS) | Exhaust server CPU & DeepSeek API credits | In-memory sliding window rate limiter (60 req/min per key/IP) returning `429 Too Many Requests` with `Retry-After`. Strict 100MB and 200 page limits. |
| **5. Content & Formula Injection** | Inject `=cmd\|' /C calc'!A0` or XSS payloads in statements/descriptions | Remote Code Execution when opening downloaded Excel/CSV files | `sanitize_for_formula_injection` prepends `'` (apostrophe) to any formula characters (`=`, `@`, `+`, `-`, `\t`, `\r`). CRLF header injection prevented by sanitizing filenames. |
| **6. Internal System Exposure** | Inspect error responses, health checks, or direct paths for secrets | Leaking environment variables or stack traces | Sanitized global JSON exception handlers suppress internal Python tracebacks. Safe security response headers (`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `X-XSS-Protection: 1; mode=block`). |
| **7. Business Logic Flaws** | Submit conflicting amounts to fool validation | Corrupted accounting records | Mathematical balance audits verify `opening + credits - debits == closing` and attach warnings rather than fabricating or falsifying numbers. |

---

## 🛠️ Setup & Local Development

### 1. Requirements
- Python 3.11+
- Virtualenv

### 2. Installation

```bash
# Clone the repository
git clone <repo_url>
cd ocr.advance

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Setup environment variables
cp .env.example .env
```

### 3. Environment Configuration (`.env`)
```env
APP_NAME=AI OCR Advance API
VERSION=2.0.0
ENVIRONMENT=development
HOST=0.0.0.0
PORT=8000
WORKERS=1
RELOAD=true

# Security & API Keys (Comma-separated list of valid keys)
API_KEYS=ocr_dev_key_secret_2026,ocr_test_key_master
REQUIRE_API_KEY=true
ENABLE_DOCS=true

# Rate Limiting
RATE_LIMIT_ENABLED=true
RATE_LIMIT_REQUESTS_PER_MINUTE=60
RATE_LIMIT_BURST=15

# DeepSeek Configuration
DEEPSEEK_API_KEY=your_actual_deepseek_api_key
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat

# Processing Limits
MAX_UPLOAD_SIZE_MB=100
MAX_PDF_PAGES=200
MAX_BATCH_FILES=50

# Timeouts
OCR_TIMEOUT=120
AI_TIMEOUT=60
WEBHOOK_TIMEOUT=15
```

### 4. Running the Server

```bash
# Development mode with Hot Reload
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Production mode with Gunicorn
gunicorn -c gunicorn_conf.py app.main:app
```

### 5. Running Tests

```bash
pytest -v
```
