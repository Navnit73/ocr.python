# AI OCR Advance API

A lightweight, enterprise-grade, high-performance, stateless AI-powered OCR & Financial Document Extraction API built with **FastAPI**, **IBM Docling**, **PyMuPDF**, **PaddleOCR**, **OpenCV**, and the **DeepSeek API**.

The API accepts large multi-page PDF documents (up to **200 pages** and **100MB**) and images, extracts text and complex table grids via IBM Docling / PyMuPDF / PaddleOCR, cleans and normalizes OCR artifacts using DeepSeek, structures financial entities into validated Pydantic models, and generates executive downloads for Excel, PDF, CSV, and accounting software (**QuickBooks QBO**, **OFX**, **Quicken QIF**).

---

## 🌟 What's New: IBM Docling Integration & Multi-Engine Architecture

- **🤖 IBM Docling Engine**: Native multi-modal document layout recognition, complex table structure parsing (accurate TableFormer), heading/paragraph extraction, Markdown export, and JSON exports.
- **⚡ Intelligent Extraction Router (`extraction_router.py`)**:
  - Deterministic automatic routing (`auto`) selecting the best engine based on document properties (digital vs scanned, table complexity, page count).
  - Explicit selection via `extraction_engine`: `auto`, `docling`, `pymupdf`, `paddleocr`.
  - Automatic fallback mechanism if a primary engine fails (e.g. OOM or initialization issue), recording recovery metadata.
- **📊 Table Extraction & Markdown Export**:
  - High-precision table extraction returning structured 2D grids, header detection, and Markdown tables.
  - Tables and Markdown are fed to DeepSeek and financial heuristics to guarantee row alignment in bank statements, receipts, and invoices.
- **🛡️ 100% Backward Compatibility**: All legacy requests without new parameters default to `auto` routing and return standard 3-layer schemas seamlessly.

---

## ✨ Key Features & Capabilities

- **🔑 Enterprise API Key Auth**: Required on all extraction and export routes via `X-API-Key` header or `Authorization: Bearer <key>`. Constant-time verification prevents timing attacks.
- **🛡️ In-Memory Sliding Window Rate Limiting**: Protects against flood attacks & API abuse with automatic HTTP 429 responses and `Retry-After` headers.
- **🔒 IDOR & Cross-Tenant Defense**: Document IDs and cached extraction results are cryptographically scoped to the caller's API key. Access attempts across API keys are blocked with `403 Forbidden`.
- **🧪 Formula Injection Sanitization**: All exported CSV, Excel, and accounting files automatically neutralize spreadsheet formula injection (`=`, `@`, `+`, `-`, `\t`, `\r`).
- **🚀 High Capacity**: Supports documents up to **200 pages** per PDF and up to **100MB** payload size.
- **⚡ Password-Protected PDFs**: Decrypt and extract password-protected statements and invoices on-the-fly.
- **🧠 3-Layer Output Architecture**:
  - **Layer 1 (Raw OCR / Text / Markdown / Tables)**: Exact text, full Markdown layout, and structured table grids.
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

## 🏛️ Extraction Engine Architecture

```
                               ┌────────────────────────────────────────────────┐
                               │             POST /api/v1/ocr/extract           │
                               └───────────────────────┬────────────────────────┘
                                                       │
                                            ┌──────────▼──────────┐
                                            │  ExtractionRouter   │
                                            └──────────┬──────────┘
                                                       │
                     ┌─────────────────────────────────┼─────────────────────────────────┐
                     │                                 │                                 │
           ┌─────────▼─────────┐             ┌─────────▼─────────┐             ┌─────────▼─────────┐
           │   DoclingEngine   │             │   PyMuPDFEngine   │             │  PaddleOCREngine  │
           │ (IBM Docling 2.x) │             │ (Fast Digital Text│             │(Scanned Geometry &│
           │ Tables / Markdown │             │  & Native Tables) │             │  Bounding Boxes)  │
           └─────────┬─────────┘             └─────────┬─────────┘             └─────────┬─────────┘
                     │                                 │                                 │
                     └─────────────────────────────────┼─────────────────────────────────┘
                                                       │
                                            ┌──────────▼──────────┐
                                            │ DocumentNormalizer  │
                                            │ (Standardized Result│
                                            │  & Tables / MD)     │
                                            └──────────┬──────────┘
                                                       │
                                            ┌──────────▼──────────┐
                                            │ StructuredExtractor │
                                            │ (DeepSeek + Audits) │
                                            └──────────┬──────────┘
                                                       │
                                            ┌──────────▼──────────┐
                                            │ ExtractionResponse  │
                                            └─────────────────────┘
```

---

## 🌐 Interactive Swagger UI & API Docs

FastAPI provides an interactive OpenAPI / Swagger UI testbed out of the box:

- **Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc UI**: [http://localhost:8000/redoc](http://localhost:8000/redoc)
- **OpenAPI JSON**: [http://localhost:8000/openapi.json](http://localhost:8000/openapi.json)

---

## 📡 API Reference & Endpoints

All functional endpoints require API key authentication via header:
- `X-API-Key: <YOUR_API_KEY>` or
- `Authorization: Bearer <YOUR_API_KEY>`

### 1. Document OCR & Extraction

#### `POST /api/v1/ocr/extract`
Extracts text, tables, Markdown, and structured financial data from a single document.

- **Parameters (`multipart/form-data`)**:
  - `file` (*required*, file): PDF (up to 200 pages), JPG, PNG, WEBP, or TIFF.
  - `document_type` (string, default `auto`): `auto`, `bank_statement`, `receipt`, `invoice`, `general`.
  - `extraction_engine` (string, default `auto`): `auto`, `docling`, `pymupdf`, `paddleocr`.
  - `enable_ocr` (boolean, default `true`): Enable OCR for scanned content and images.
  - `extract_tables` (boolean, default `true`): Extract structured table grids and layout.
  - `output_format` (string, default `json`): `json`, `markdown`.
  - `language` (string, default `en`): `auto`, `en`, `hi`, `es`, `fr`, `de`, `ch`.
  - `clean_with_ai` (boolean, default `true`): DeepSeek cleaning & structured extraction.
  - `request_id` (string, optional): Client-provided request tracking ID.
  - `password` (string, optional): Password for encrypted PDF files.
  - `callback_url` (string, optional): Webhook URL for asynchronous delivery (`202 Accepted`).
  - `callback_secret` (string, optional): HMAC secret for `X-Webhook-Signature` validation.

- **Example curl (Docling with Table Extraction)**:
```bash
curl -X POST "http://localhost:8000/api/v1/ocr/extract" \
  -H "X-API-Key: ocr_dev_key_secret_2026" \
  -F "file=@bank_statement.pdf" \
  -F "extraction_engine=docling" \
  -F "document_type=bank_statement" \
  -F "extract_tables=true" \
  -F "enable_ocr=true" \
  -F "clean_with_ai=true"
```

- **Example JSON Response**:
```json
{
  "id": "ocr_123456789abc",
  "status": "success",
  "document_type": "bank_statement",
  "extraction": {
    "bank_name": "Chase Bank",
    "account_holder": "Acme Corp",
    "account_number_masked": "XXXX-1234",
    "currency": "USD",
    "statement_period": "2026-01-01 to 2026-01-31",
    "opening_balance": 5000.0,
    "closing_balance": 5750.0,
    "transactions": [
      {
        "date": "2026-01-05",
        "description": "Client Payment Ref 001",
        "reference": "REF001",
        "debit": null,
        "credit": 1000.0,
        "balance": 6000.0
      },
      {
        "date": "2026-01-15",
        "description": "Server Hosting Fees",
        "reference": null,
        "debit": 250.0,
        "credit": null,
        "balance": 5750.0
      }
    ]
  },
  "raw_text": "...",
  "cleaned_text": "...",
  "markdown": "# Chase Bank Statement\n\n| Date | Description | Debit | Credit | Balance |\n|---|---|---|---|---|\n| 2026-01-05 | Client Payment | | 1000.00 | 6000.00 |",
  "tables": [
    {
      "page_number": 1,
      "table_index": 0,
      "num_rows": 3,
      "num_cols": 5,
      "headers": ["Date", "Description", "Debit", "Credit", "Balance"],
      "grid": [
        ["Date", "Description", "Debit", "Credit", "Balance"],
        ["2026-01-05", "Client Payment", "", "1000.00", "6000.00"],
        ["2026-01-15", "Server Hosting", "250.00", "", "5750.00"]
      ],
      "markdown": "| Date | Description | Debit | Credit | Balance | ...",
      "confidence": 0.95
    }
  ],
  "pages": [
    {
      "page_number": 1,
      "text": "...",
      "confidence": 0.95,
      "lines": [],
      "is_scanned": false
    }
  ],
  "metadata": {
    "pages": 1,
    "ocr_used": true,
    "ocr_engine": "docling",
    "extraction_engine": "docling",
    "fallback_used": false,
    "tables_extracted": 1,
    "docling_version": "2.132.0",
    "ai_cleaned": true,
    "ai_model": "deepseek-chat",
    "processing_time_ms": 1420
  },
  "warnings": []
}
```

---

## 🛠️ Installation & Setup

### Prerequisites
- Python 3.11+
- Virtual Environment

### 1. Clone & Setup Virtualenv
```bash
git clone https://github.com/Navnit73/ocr.python.git
cd ocr.python
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment (.env)
```bash
cp .env.example .env
# Edit .env with your DEEPSEEK_API_KEY and settings
```

### 3. Run Development Server
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 4. Run Test Suite
```bash
pytest
```

---

## 🐳 Docker Deployment

```bash
docker build -t ocr-advance-api .
docker run -p 8000:8000 --env-file .env ocr-advance-api
```

Or with Docker Compose:
```bash
docker-compose up -d
```
