# 📚 AI OCR Advance API — Complete Developer & Frontend Integration Guide

Welcome to the **AI OCR Advance API** documentation. This guide details every available endpoint, exact request formats, expected responses, frontend integration snippets (Vue / React / Axios / Fetch), and a deep-dive explanation of **Async Webhooks** and **HMAC Callback Secrets**.

---

## 🔑 Global API Headers & Authentication

All requests to `/api/v1/ocr/*`, `/api/v1/documents/*`, and `/api/v1/export/*` require API key authentication.

| Header | Example Value | Description |
|---|---|---|
| `X-API-Key` | `ocr_dev_key_secret_2026` | API Key authentication header |
| `Authorization` | `Bearer ocr_dev_key_secret_2026` | Alternative Bearer token header |
| `X-Request-ID` | `req_frontend_user_123` | *(Optional)* Unique ID from frontend to track requests |

---

## 📖 Endpoint Reference Table

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/ocr/extract` | Extract text & structured financial data (Up to 200 pages / 100MB) |
| `POST` | `/api/v1/ocr/batch` | Batch process multiple files or `.zip` archives |
| `GET` | `/api/v1/documents` | List stored document extractions (Paginated & Filtered) |
| `GET` | `/api/v1/documents/{id}` | Retrieve complete extraction JSON by Document ID |
| `DELETE` | `/api/v1/documents/{id}` | Delete a stored document from database/cache |
| `GET` | `/api/v1/export/download/{id}` | Download file (`.xlsx`, `.pdf`, `.csv`, `.ofx`, `.qbo`, `.qif`) by ID |
| `POST` | `/api/v1/export/generate` | Convert an extraction JSON payload directly into a file download |
| `POST` | `/api/v1/export/consolidate` | Merge multiple monthly statements into a 12-Month P&L Report |
| `GET` | `/api/v1/health` | Service health status |

---

## 📡 Detailed API Endpoints & Responses

---

### 1. `POST /api/v1/ocr/extract`
Extracts text and structured financial data from a single document.

#### Request (`multipart/form-data`):
- `file` (*required*, File): PDF, JPG, PNG, WEBP, or TIFF.
- `document_type` (String, default: `auto`): `auto`, `bank_statement`, `receipt`, `invoice`, `general`.
- `language` (String, default: `en`): `auto`, `en`, `hi`, `es`, `fr`, `de`, `ch`.
- `clean_with_ai` (Boolean, default: `true`): DeepSeek cleaning & structured extraction.
- `request_id` (String, optional): Custom tracking ID.
- `password` (String, optional): Password for encrypted PDF files.
- `callback_url` (String, optional): If supplied, runs asynchronously and POSTs results to your webhook.
- `callback_secret` (String, optional): Secret key for HMAC-SHA256 signature validation on webhook POST.

#### Synchronous Response (`200 OK` — Bank Statement Example):
```json
{
  "id": "ocr_9a8b7c6d5e4f",
  "status": "success",
  "document_type": "bank_statement",
  "extraction": {
    "bank_name": "State Bank of India",
    "account_holder": "Mr. NAVNIT RAI",
    "account_number_masked": "XXXX-123456",
    "currency": "INR",
    "statement_period": "2026-01-01 to 2026-01-31",
    "opening_balance": 25000.00,
    "closing_balance": 34500.00,
    "transactions": [
      {
        "date": "2026-01-05",
        "description": "Salary Credit - TechCorp",
        "reference": "UPI982312",
        "debit": null,
        "credit": 15000.00,
        "balance": 40000.00
      },
      {
        "date": "2026-01-10",
        "description": "Amazon Online Shopping",
        "reference": "TXN44910",
        "debit": 5500.00,
        "credit": null,
        "balance": 34500.00
      }
    ]
  },
  "raw_text": "State Bank of India\nAccount Statement...",
  "cleaned_text": "State Bank of India\nAccount Statement...",
  "pages": [
    {
      "page_number": 1,
      "text": "State Bank of India...",
      "confidence": 1.0,
      "is_scanned": false,
      "lines": []
    }
  ],
  "metadata": {
    "pages": 1,
    "ocr_used": false,
    "ocr_engine": "pymupdf_digital",
    "ai_cleaned": true,
    "ai_model": "deepseek-chat",
    "processing_time_ms": 1150,
    "stage_timings_ms": {
      "validation": 2,
      "ocr_extraction": 12,
      "ai_cleaning": 480,
      "classification": 1,
      "structured_extraction": 655
    }
  },
  "warnings": []
}
```

#### Asynchronous Response (`202 Accepted` — When `callback_url` is provided):
```json
{
  "id": "ocr_9a8b7c6d5e4f",
  "status": "processing",
  "message": "Document extraction queued. Results will be delivered to callback_url upon completion.",
  "callback_url": "https://yourapp.com/api/webhooks/ocr"
}
```

---

### 2. `POST /api/v1/ocr/batch`
Concurrently extracts up to 50 documents or a `.zip` archive.

#### Request (`multipart/form-data`):
- `files` (*required*, List of Files or a single `.zip` file).
- `document_type` (String, default: `auto`).
- `language` (String, default: `en`).
- `clean_with_ai` (Boolean, default: `true`).

#### Response (`200 OK`):
```json
{
  "batch_id": "batch_9812a3b4",
  "total_files": 2,
  "successful_count": 2,
  "failed_count": 0,
  "total_processing_time_ms": 2340,
  "consolidated_inflow": 15000.00,
  "consolidated_outflow": 5500.00,
  "net_consolidated_savings": 9500.00,
  "items": [
    {
      "id": "ocr_file1_id",
      "filename": "january_statement.pdf",
      "status": "success",
      "document_type": "bank_statement",
      "extraction": { ... },
      "raw_text": "...",
      "processing_time_ms": 1120
    },
    {
      "id": "ocr_file2_id",
      "filename": "vendor_invoice.pdf",
      "status": "success",
      "document_type": "invoice",
      "extraction": { ... },
      "raw_text": "...",
      "processing_time_ms": 1220
    }
  ]
}
```

---

### 3. `GET /api/v1/documents`
Lists stored document extractions for the authenticated account with pagination and search.

#### Query Parameters:
- `page` (Integer, default `1`): Page number.
- `page_size` (Integer, default `20`, max `100`): Items per page.
- `document_type` (String, optional): Filter by `bank_statement`, `invoice`, `receipt`, `general`, or `all`.
- `search` (String, optional): Search keyword in document ID or raw text.

#### Response (`200 OK`):
```json
{
  "total": 42,
  "page": 1,
  "page_size": 20,
  "total_pages": 3,
  "items": [
    {
      "id": "ocr_9a8b7c6d5e4f",
      "document_type": "bank_statement",
      "status": "success",
      "created_at": "2026-10-01T12:00:00Z",
      "extraction": {
        "bank_name": "State Bank of India",
        "closing_balance": 34500.0
      },
      "metadata": { "pages": 1, "processing_time_ms": 1150 }
    }
  ]
}
```

---

### 4. `GET /api/v1/documents/{id}`
Retrieves complete extraction JSON details for a specific document ID.

#### Response (`200 OK`):
Returns the complete `ExtractionResponse` JSON object for that document.

---

### 5. `GET /api/v1/export/download/{id}?format=xlsx|pdf|csv|ofx|qbo|qif`
Direct binary file download stream for an extracted document.

#### Formats:
| Format | Extension | Content-Type | Application Target |
|---|---|---|---|
| `xlsx` | `.xlsx` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` | Microsoft Excel, Google Sheets |
| `pdf` | `.pdf` | `application/pdf` | Adobe Acrobat, PDF Readers |
| `csv` | `.csv` | `text/csv` | Universal Spreadsheets, Databases |
| `qbo` | `.qbo` | `application/vnd.intu.qbo` | QuickBooks Online (1-Click Import) |
| `ofx` | `.ofx` | `application/x-ofx` | Xero, Zoho Books, Tally, Sage |
| `qif` | `.qif` | `application/x-qif` | Quicken Desktop |

---

### 6. `POST /api/v1/export/consolidate?as_excel=true`
Merges multiple monthly statements into a single 12-Month Annual Cashflow & P&L report.

#### Request Body (`application/json`):
```json
{
  "title": "Fiscal Year 2026 Annual Audit",
  "request_ids": ["ocr_jan_id", "ocr_feb_id", "ocr_mar_id"]
}
```
- If `as_excel=true` (default): Returns the `.xlsx` multi-sheet Master Workbook file stream.
- If `as_excel=false`: Returns JSON analytics with month-by-month inflow/outflow breakdown.

---

## 💻 Frontend Integration Code (Vue 3 / React / Vanilla JS)

### 1. Upload Document & Extract (Axios with Progress Bar)

```javascript
import axios from 'axios';

const API_BASE_URL = 'http://localhost:8000/api/v1';
const API_KEY = 'ocr_dev_key_secret_2026';

export async function uploadDocument(file, onProgress) {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('document_type', 'auto');
  formData.append('clean_with_ai', 'true');

  const response = await axios.post(`${API_BASE_URL}/ocr/extract`, formData, {
    headers: {
      'X-API-Key': API_KEY,
      'Content-Type': 'multipart/form-data',
    },
    onUploadProgress: (progressEvent) => {
      if (onProgress && progressEvent.total) {
        const percentCompleted = Math.round((progressEvent.loaded * 100) / progressEvent.total);
        onProgress(percentCompleted);
      }
    },
  });

  return response.data; // Full extraction JSON
}
```

---

### 2. Download File Stream (Excel, PDF, CSV, QBO) in Browser

```javascript
export async function downloadReport(documentId, format = 'xlsx') {
  const response = await axios.get(`${API_BASE_URL}/export/download/${documentId}`, {
    params: { format },
    headers: {
      'X-API-Key': API_KEY,
    },
    responseType: 'blob', // IMPORTANT: Required for binary file downloads
  });

  // Create a temporary link and trigger native browser download
  const blob = new Blob([response.data], { type: response.headers['content-type'] });
  const downloadUrl = window.URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = downloadUrl;
  
  // Extract filename from Content-Disposition header if available, or fallback
  const contentDisposition = response.headers['content-disposition'];
  let filename = `document_${documentId}.${format}`;
  if (contentDisposition) {
    const match = contentDisposition.match(/filename="?([^"]+)"?/);
    if (match && match[1]) filename = match[1];
  }

  link.setAttribute('download', filename);
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(downloadUrl);
}
```

---

### 3. Fetch Stored History (Vue 3 / React Example)

```javascript
export async function fetchDocumentHistory(page = 1, docType = 'all', search = '') {
  const response = await axios.get(`${API_BASE_URL}/documents`, {
    params: {
      page,
      page_size: 20,
      document_type: docType,
      search: search || undefined,
    },
    headers: {
      'X-API-Key': API_KEY,
    },
  });

  return response.data; // { total, page, items: [...] }
}
```

---

## 🔔 Deep Dive: What is a Webhook and Callback Secret?

### 1. What is a Webhook?

When processing large documents (e.g. a **200-page bank statement** or **50 scanned invoices**), optical character recognition and AI cleaning can take **15 to 45 seconds**. 

- **Without Webhook (Synchronous)**: Your frontend or backend makes a `POST /extract` request and must keep the HTTP connection open, waiting 30+ seconds. Browsers, load balancers (like AWS ALB or Cloudflare), or mobile networks might timeout (`504 Gateway Timeout`).
- **With Webhook (Asynchronous)**:
  1. Your frontend sends the file to `POST /api/v1/ocr/extract` with `callback_url=https://yourapp.com/api/webhook/ocr`.
  2. The OCR API returns **`202 Accepted` immediately (in ~100ms)** with `{ "status": "processing", "id": "ocr_123" }`.
  3. The OCR API processes the 200 pages in the background.
  4. When done, the OCR API automatically makes an **HTTP `POST` request to your `callback_url`** containing the final extraction JSON payload!

```
[ Your Frontend / App ]                   [ AI OCR Advance API ]
         │                                          │
         │─── 1. POST /ocr/extract (with callback) ─▶│
         │◀── 2. 202 Accepted {"status": "proc"} ──│ (Immediate < 100ms)
         │                                          │
         │          (OCR Processes in Background)   │
         │                                          │
[ Your Webhook Endpoint ]                           │
         │◀── 3. POST /webhook (Final Extraction) ──│
         │─── 4. 200 OK ───────────────────────────▶│
```

---

### 2. What is `callback_secret` & Why is it Critical?

Anyone on the internet could theoretically discover your webhook URL (`https://yourapp.com/api/webhook/ocr`) and send fake fake bank statement data.

To prevent spoofing, **HMAC-SHA256 Signatures** are used:

1. When initiating extraction, you provide a private `callback_secret`:
   ```bash
   callback_secret="my_super_secret_shared_key_123"
   ```
2. When the OCR API posts the result to your webhook URL, it computes an **HMAC-SHA256 hash** of the request body using your secret key and attaches it in the HTTP header:
   ```http
   X-Webhook-Signature: sha256=a8f5c9e2b1034...
   ```
3. Your webhook server validates this signature before trusting the data!

---

### 3. How to Verify Webhook Signatures on Your Server

#### Node.js / Express Example:
```javascript
import crypto from 'crypto';
import express from 'express';

const app = express();
// Make sure to parse raw body for accurate HMAC signature calculation
app.use(express.json());

const CALLBACK_SECRET = 'my_super_secret_shared_key_123';

app.post('/api/webhook/ocr', (req, res) => {
  const signatureHeader = req.headers['x-webhook-signature']; // e.g. "sha256=abcdef123..."
  if (!signatureHeader) {
    return res.status(401).send('Missing signature header');
  }

  const expectedSignature = 'sha256=' + crypto
    .createHmac('sha256', CALLBACK_SECRET)
    .update(JSON.stringify(req.body))
    .digest('hex');

  // Constant-time comparison to prevent timing attacks
  const isValid = crypto.timingSafeEqual(
    Buffer.from(signatureHeader),
    Buffer.from(expectedSignature)
  );

  if (!isValid) {
    return res.status(403).send('Invalid webhook signature');
  }

  const extractionData = req.body;
  console.log('✅ Verified OCR Extraction Received:', extractionData.id);
  console.log('Document Type:', extractionData.document_type);
  console.log('Extracted Data:', extractionData.extraction);

  // Notify your frontend via WebSocket, SSE, or update your database
  res.status(200).send({ received: true });
});
```

#### Python / FastAPI Example:
```python
import hmac
import hashlib
from fastapi import FastAPI, Request, HTTPException, Header

app = FastAPI()
CALLBACK_SECRET = "my_super_secret_shared_key_123"

@app.post("/api/webhook/ocr")
async def handle_ocr_webhook(
    request: Request,
    x_webhook_signature: str = Header(None),
):
    body_bytes = await request.body()
    
    # Compute expected signature
    computed_sig = "sha256=" + hmac.new(
        CALLBACK_SECRET.encode("utf-8"),
        body_bytes,
        hashlib.sha256
    ).hexdigest()

    if not x_webhook_signature or not hmac.compare_digest(x_webhook_signature, computed_sig):
        raise HTTPException(status_code=403, detail="Invalid HMAC signature")

    payload = await request.json()
    print(f"✅ Verified OCR payload for Document {payload.get('id')}")
    return {"status": "received"}
```
