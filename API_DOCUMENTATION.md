# 📚 AI OCR Advance API — Complete Developer & Frontend Integration Guide

Welcome to the **AI OCR Advance API** documentation. This enterprise-grade, asynchronous document processing platform is built for heavy-duty financial OCR (handling **100–200 page bank statements**, invoices, receipts, multi-file archives, and annual statement consolidations).

---

## 🌟 What's New: 10-Page Sequential Chunking (100–200 Pages)

When processing large documents (e.g. **100–200 pages**), the API does **not** load all 200 pages at once. It automatically splits the document into **10-page parts** (e.g., 200 pages = 20 parts of 10 pages each):
- **Sequential Execution**: Each 10-page part is rendered, OCR-extracted, AI-cleaned, and structured sequentially one by one.
- **Flat Memory Profile**: Pixmap buffers are released immediately after each part with proactive garbage collection.
- **Granular Real-Time SSE Events**: The frontend receives fine-grained progress per part and per page:
  - `"Processing Part 1 of 20 (Pages 1-10)"`
  - `"Extracted page 7 of 200 (Part 1/20)"`
  - `"Completed Part 1 of 20 (10/200 pages processed)"`
  - Up to `"Completed Part 20 of 20 (200/200 pages processed)"`
- **Multi-Part Financial Consolidation**: Transactions across all parts are merged in chronological order, preserving `opening_balance` from Part 1, capturing `closing_balance` from Part 20, and running balance arithmetic verification.

---

## 🏗️ Asynchronous Architecture & Lifecycle

Large documents (50–200 pages) take time to extract, clean with AI, and structure. Keeping an HTTP connection open is prone to proxy/browser timeouts. The **AI OCR Advance API** uses an asynchronous background worker architecture:

```
┌─────────────────┐             ┌─────────────────────┐              ┌────────────────────────┐
│ Next.js Frontend│             │ FastAPI Backend API │              │ Celery/Async Workers   │
└────────┬────────┘             └──────────┬──────────┘              └───────────┬────────────┘
         │                                 │                                     │
         │─── 1. POST /jobs/upload ───────▶│ (Saves File to Storage)             │
         │◀── 2. 202 Accepted {job_id} ────│ (MongoDB status="queued")           │
         │                                 │─── 3. Enqueue Job Task ────────────▶│
         │                                 │                                     │─── 4. Chunk Pages (10 per Part)
         │─── 5. Connect SSE /jobs/{id}───▶│                                     │─── 5. Process Part 1..20 Sequentially
         │◀── 6. Stream Live Part/Page ────│◀── Broadcast Event (Part/Stage) ───│─── 6. Consolidate Multi-Part JSON
         │                                 │                                     │
         │                                 │◀── 7. Save Result in MongoDB ───────│─── 7. Job Completed (100%)
         │◀── 8. Push "completed" Event ───│                                     │
         │                                 │                                     │─── 8. Dispatch HMAC Webhook ──▶ [Client Webhook]
         │─── 9. GET /documents/{id} ─────▶│                                     │
         │◀── 10. Display Complete JSON ───│                                     │
```

---

## 🔑 Global API Headers & Authentication

All API requests require authentication using either the `X-API-Key` or `Authorization: Bearer <key>` header:

| Header | Example Value | Description |
|---|---|---|
| `X-API-Key` | `ocr_dev_key_secret_2026` | Standard API Key authentication header |
| `Authorization` | `Bearer ocr_dev_key_secret_2026` | Standard Bearer token authentication header |
| `X-Request-ID` | `req_client_user_123` | *(Optional)* Custom request / tracking ID |

---

## 📖 Complete API Endpoints Summary

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/jobs/upload` | **Async Upload**: Upload document (100–200 pages), queues job, returns `202 Accepted` immediately |
| `GET` | `/api/v1/jobs/{job_id}` | **Job Status**: Get real-time progress %, page counters, current part, stage, and completion status |
| `GET` | `/api/v1/jobs/{job_id}/events` | **Live SSE Stream**: Real-time Server-Sent Events push stream for live progress UI |
| `GET` | `/api/v1/jobs` | **List Jobs**: List authenticated user's jobs with status filter and pagination |
| `POST` | `/api/v1/jobs/{job_id}/cancel` | **Cancel Job**: Abort a queued or in-progress background job |
| `POST` | `/api/v1/jobs/{job_id}/retry` | **Retry Job**: Re-enqueue a failed job for background processing |
| `POST` | `/api/v1/ocr/extract-async` | **Async Alias**: Upload & queue asynchronous OCR extraction (`202 Accepted`) |
| `POST` | `/api/v1/ocr/extract` | **Universal OCR**: Synchronous mode or async mode (`async_mode=true` or `callback_url`) |
| `POST` | `/api/v1/ocr/batch` | **Batch OCR**: Process up to 50 files or `.zip` archives concurrently |
| `GET` | `/api/v1/documents` | **List Documents**: Query stored document extractions in MongoDB with search & filters |
| `GET` | `/api/v1/documents/{id}` | **Get Document**: Fetch complete 3-layer extraction JSON for a document ID |
| `DELETE` | `/api/v1/documents/{id}` | **Delete Document**: Permanently delete document from database and storage |
| `GET` | `/api/v1/export/download/{id}` | **Download Export**: Generate `.xlsx`, `.pdf`, `.csv`, `.ofx`, `.qbo`, `.qif` by document ID |
| `POST` | `/api/v1/export/generate` | **Direct Export**: Convert arbitrary extraction JSON payload into binary file stream |
| `POST` | `/api/v1/export/consolidate` | **Annual Consolidation**: Merge multiple statements into a 12-Month P&L Workbook |
| `GET` | `/api/v1/admin/stats` | **Admin Metrics**: Database metrics, job status counts, page totals, worker health |
| `GET` | `/api/v1/admin/jobs` | **Admin Jobs**: Inspect and search all system jobs across all users |
| `GET` | `/api/v1/admin/events` | **Admin Live Stream**: Global SSE stream for real-time operations dashboard |
| `GET` | `/api/v1/health` | **Health Check**: System readiness, MongoDB connectivity, and worker status |

---

## 📡 Detailed API Endpoints & Request/Response Formats

---

### 1. `POST /api/v1/jobs/upload` (or `/api/v1/ocr/extract-async`)
Uploads a document (up to 200 pages / 100MB) for non-blocking asynchronous processing.

#### Request (`multipart/form-data`):
- `file` (*required*, File): PDF (up to 200 pages), JPG, PNG, WEBP, or TIFF.
- `document_type` (String, default: `auto`): `auto`, `bank_statement`, `receipt`, `invoice`, `general`.
- `language` (String, default: `en`): `auto`, `en`, `hi`, `es`, `fr`, `de`, `ch`.
- `clean_with_ai` (Boolean, default: `true`): DeepSeek cleaning & structured entity extraction.
- `request_id` (String, optional): Custom tracking document ID.
- `password` (String, optional): Password for encrypted PDF files.
- `user_email` (String, optional): Logged-in user's email address (for page quota credit tracking & MongoDB Vault sync).
- `callback_url` (String, optional): Webhook URL to receive signed event notifications.
- `callback_secret` (String, optional): Secret key for HMAC-SHA256 signature verification.

#### Immediate Response (`202 Accepted`):
```json
{
  "job_id": "job_a1b2c3d4e5f6",
  "document_id": "doc_9a8b7c6d5e4f",
  "status": "queued",
  "message": "Document uploaded successfully. Processing has started.",
  "status_url": "/api/v1/jobs/job_a1b2c3d4e5f6"
}
```

---

### 2. `GET /api/v1/jobs/{job_id}`
Retrieves current processing progress, stage, page counters, and final results.

#### In-Progress Response (`200 OK`):
```json
{
  "job_id": "job_a1b2c3d4e5f6",
  "document_id": "doc_9a8b7c6d5e4f",
  "status": "processing",
  "progress": 45,
  "total_pages": 200,
  "processed_pages": 90,
  "current_stage": "ocr_extraction",
  "message": "Completed Part 9 of 20 (90/200 pages processed)",
  "created_at": "2026-10-02T12:00:00Z",
  "updated_at": "2026-10-02T12:00:18Z",
  "started_at": "2026-10-02T12:00:01Z",
  "completed_at": null,
  "result": null,
  "result_url": null,
  "error": null,
  "retry_count": 0,
  "metadata": {
    "filename": "200_page_bank_statement_2026.pdf",
    "file_size_bytes": 18450000
  }
}
```

#### Completed Response (`200 OK`):
```json
{
  "job_id": "job_a1b2c3d4e5f6",
  "document_id": "doc_9a8b7c6d5e4f",
  "status": "completed",
  "progress": 100,
  "total_pages": 200,
  "processed_pages": 200,
  "current_stage": "completed",
  "message": "Processing completed successfully",
  "created_at": "2026-10-02T12:00:00Z",
  "updated_at": "2026-10-02T12:00:45Z",
  "started_at": "2026-10-02T12:00:01Z",
  "completed_at": "2026-10-02T12:00:45Z",
  "result_url": "/api/v1/documents/doc_9a8b7c6d5e4f",
  "result": {
    "id": "doc_9a8b7c6d5e4f",
    "status": "success",
    "document_type": "bank_statement",
    "extraction": {
      "bank_name": "State Bank of India",
      "account_holder": "Mr. NAVNIT RAI",
      "account_number_masked": "XXXX-123456",
      "currency": "INR",
      "statement_period": "2026-01-01 to 2026-12-31",
      "opening_balance": 250000.0,
      "closing_balance": 845000.0,
      "transactions": [
        {
          "date": "2026-01-05",
          "description": "Enterprise Consulting Fee",
          "reference": "NEFT982312",
          "debit": null,
          "credit": 150000.0,
          "balance": 400000.0
        }
      ]
    },
    "raw_text": "State Bank of India...",
    "cleaned_text": "State Bank of India...",
    "metadata": {
      "pages": 200,
      "ocr_used": true,
      "ocr_engine": "paddleocr",
      "ai_cleaned": true,
      "processing_time_ms": 44200
    }
  },
  "error": null,
  "retry_count": 0
}
```

---

### 3. `GET /api/v1/jobs/{job_id}/events` (Server-Sent Events)
Connects to a real-time event stream (`text/event-stream`). Pushes live updates whenever progress changes.

#### SSE Stream Events:
```http
event: connected
data: {"event": "connected", "job_id": "job_a1b2c3d4e5f6", "message": "Connected to live event stream"}

event: ocr.job.started
data: {"job_id": "job_a1b2c3d4e5f6", "status": "processing", "current_stage": "ocr_extraction", "progress": 5}

event: ocr.job.progress
data: {"job_id": "job_a1b2c3d4e5f6", "status": "processing", "progress": 35, "processed_pages": 70, "total_pages": 200, "message": "Completed Part 7 of 20 (70/200 pages processed)"}

event: ocr.job.completed
data: {"job_id": "job_a1b2c3d4e5f6", "status": "completed", "progress": 100, "result_url": "/api/v1/documents/doc_9a8b7c6d5e4f"}
```

---

### 4. `GET /api/v1/documents`
Lists stored document extraction records with keyword search, type filter, and pagination.

#### Query Parameters:
- `document_type` (String, optional): `bank_statement`, `invoice`, `receipt`, `general`, or `all`.
- `search` (String, optional): Search keyword matching filename, document ID, or extracted text.
- `page` (Integer, default: 1): Page number.
- `page_size` (Integer, default: 20, max: 100): Records per page.

#### Response (`200 OK`):
```json
{
  "total": 45,
  "page": 1,
  "page_size": 20,
  "total_pages": 3,
  "items": [
    {
      "id": "doc_9a8b7c6d5e4f",
      "job_id": "job_a1b2c3d4e5f6",
      "filename": "annual_bank_statement_2026.pdf",
      "content_type": "application/pdf",
      "file_size_bytes": 18450000,
      "document_type": "bank_statement",
      "status": "success",
      "pages_count": 200,
      "summary": {
        "bank_name": "State Bank of India",
        "account_holder": "Mr. NAVNIT RAI",
        "opening_balance": 250000.0,
        "closing_balance": 845000.0,
        "transactions_count": 1850
      },
      "created_at": "2026-10-02T12:00:00Z"
    }
  ]
}
```

---

### 5. `GET /api/v1/documents/{document_id}`
Retrieves complete extraction JSON by document ID. Enforces ownership matching to prevent IDOR.

#### Response (`200 OK`):
Returns the full 3-layer `ExtractionResponse` (Extraction, Raw Text, Cleaned Text, Pages breakdown, Metadata, Warnings).

---

### 6. `DELETE /api/v1/documents/{document_id}`
Permanently deletes a document from MongoDB and storage.

#### Response (`200 OK`):
```json
{
  "success": true,
  "message": "Document doc_9a8b7c6d5e4f deleted successfully"
}
```

---

### 7. `GET /api/v1/export/download/{document_id}`
Generates and downloads financial export files directly in the browser.

#### Query Parameters:
- `format` (*required*, String): `xlsx`, `pdf`, `csv`, `ofx`, `qbo`, `qif`.

#### Response:
Binary stream with proper `Content-Disposition` header attachment filename.

---

### 8. `POST /api/v1/export/consolidate`
Consolidates multiple bank statements (e.g. 12 monthly statements) into a comprehensive **Annual 12-Month P&L Consolidation Workbook (`.xlsx`)**.

#### Request Body (`application/json`):
```json
{
  "document_ids": [
    "doc_jan_2026",
    "doc_feb_2026",
    "doc_mar_2026",
    "doc_apr_2026"
  ],
  "year": 2026,
  "base_currency": "INR"
}
```

#### Response:
Binary stream (`.xlsx`) containing:
- 📊 Executive 12-Month Dashboard & KPI Cards
- 📅 Monthly Breakdown (Inflow, Outflow, Net Savings)
- 🏷️ Categorized Expense Matrix & Subscription Tracker
- 📋 Master Chronological Transactions Ledger

---

## 💻 Complete Frontend TypeScript Implementation Guide

---

### 1. `types/ocr.ts` — Complete TypeScript Types

```typescript
export type DocumentType = 'auto' | 'bank_statement' | 'invoice' | 'receipt' | 'general';
export type JobStatus = 'idle' | 'uploading' | 'queued' | 'processing' | 'completed' | 'failed' | 'cancelled';
export type ExportFormat = 'xlsx' | 'pdf' | 'csv' | 'ofx' | 'qbo' | 'qif';

export interface BankTransaction {
  date: string | null;
  description: string;
  reference: string | null;
  debit: number | null;
  credit: number | null;
  balance: number | null;
}

export interface BankStatementExtraction {
  bank_name: string | null;
  account_holder: string | null;
  account_number_masked: string | null;
  currency: string | null;
  statement_period: string | null;
  opening_balance: number | null;
  closing_balance: number | null;
  transactions: BankTransaction[];
}

export interface InvoiceLineItem {
  description: string;
  quantity: number | null;
  unit_price: number | null;
  tax_rate: number | null;
  amount: number | null;
}

export interface InvoiceExtraction {
  invoice_number: string | null;
  invoice_date: string | null;
  due_date: string | null;
  supplier: {
    name: string | null;
    address: string | null;
    tax_id: string | null;
    email: string | null;
    phone: string | null;
  };
  customer: {
    name: string | null;
    address: string | null;
    tax_id: string | null;
    email: string | null;
    phone: string | null;
  };
  currency: string | null;
  subtotal: number | null;
  tax: number | null;
  total: number | null;
  line_items: InvoiceLineItem[];
}

export interface ProcessingMetadata {
  pages: number;
  ocr_used: boolean;
  ocr_engine: string;
  ai_cleaned: boolean;
  ai_model: string | null;
  processing_time_ms: number;
  stage_timings_ms?: Record<string, number>;
}

export interface ExtractionResponse {
  id: string;
  status: 'success' | 'partial_success' | 'failed';
  document_type: string;
  extraction: BankStatementExtraction | InvoiceExtraction | any;
  raw_text: string;
  cleaned_text: string;
  metadata: ProcessingMetadata;
  warnings: Array<{ code: string; message: string; severity?: string }>;
}

export interface JobStatusResponse {
  job_id: string;
  document_id: string | null;
  status: JobStatus;
  progress: number;
  total_pages: number;
  processed_pages: number;
  current_stage: string;
  message: string;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  completed_at: string | null;
  result: ExtractionResponse | null;
  result_url: string | null;
  error: string | null;
  retry_count: number;
  metadata: Record<string, any>;
}

export interface DocumentListItem {
  id: string;
  job_id: string | null;
  filename: string;
  content_type: string;
  file_size_bytes: number;
  document_type: string;
  status: string;
  pages_count: number;
  summary: any;
  created_at: string;
}

export interface DocumentListResponse {
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  items: DocumentListItem[];
}
```

---

### 2. `services/ocrApi.ts` — Unified Frontend API Client

```typescript
import axios from 'axios';
import {
  DocumentListResponse,
  DocumentType,
  ExportFormat,
  ExtractionResponse,
  JobStatusResponse,
} from '@/types/ocr';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1';
const API_KEY = process.env.NEXT_PUBLIC_API_KEY || 'ocr_dev_key_secret_2026';

const client = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'X-API-Key': API_KEY,
  },
});

export const ocrApi = {
  // 1. Upload document for asynchronous processing (10-page chunked pipeline)
  uploadAsync: async (
    file: File,
    options?: {
      documentType?: DocumentType;
      language?: string;
      cleanWithAi?: boolean;
      password?: string;
      onUploadProgress?: (percent: number) => void;
    }
  ): Promise<{ job_id: string; document_id: string; status_url: string }> => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('document_type', options?.documentType || 'auto');
    formData.append('language', options?.language || 'en');
    formData.append('clean_with_ai', options?.cleanWithAi !== false ? 'true' : 'false');
    if (options?.password) formData.append('password', options.password);

    const res = await client.post('/jobs/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: (progressEvent) => {
        if (progressEvent.total && options?.onUploadProgress) {
          const percent = Math.round((progressEvent.loaded * 100) / progressEvent.total);
          options.onUploadProgress(percent);
        }
      },
    });
    return res.data;
  },

  // 2. Poll job status
  getJobStatus: async (jobId: string): Promise<JobStatusResponse> => {
    const res = await client.get(`/jobs/${jobId}`);
    return res.data;
  },

  // 3. Cancel an ongoing job
  cancelJob: async (jobId: string): Promise<void> => {
    await client.post(`/jobs/${jobId}/cancel`);
  },

  // 4. Retry a failed job
  retryJob: async (jobId: string): Promise<void> => {
    await client.post(`/jobs/${jobId}/retry`);
  },

  // 5. Get document extraction details
  getDocument: async (documentId: string): Promise<ExtractionResponse> => {
    const res = await client.get(`/documents/${documentId}`);
    return res.data;
  },

  // 6. List documents with search & pagination
  listDocuments: async (params?: {
    document_type?: string;
    search?: string;
    page?: number;
    page_size?: number;
  }): Promise<DocumentListResponse> => {
    const res = await client.get('/documents', { params });
    return res.data;
  },

  // 7. Delete document
  deleteDocument: async (documentId: string): Promise<void> => {
    await client.delete(`/documents/${documentId}`);
  },

  // 8. Download exported file (.xlsx, .pdf, .csv, .ofx, .qbo, .qif)
  downloadExport: async (documentId: string, format: ExportFormat, filename?: string) => {
    const res = await client.get(`/export/download/${documentId}`, {
      params: { format },
      responseType: 'blob',
    });
    const url = window.URL.createObjectURL(new Blob([res.data]));
    const link = document.createElement('a');
    link.href = url;
    link.setAttribute('download', filename || `extraction_${documentId}.${format}`);
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(url);
  },

  // 9. Consolidate multiple bank statements into annual P&L workbook
  consolidateAnnual: async (documentIds: string[], year = 2026, currency = 'USD') => {
    const res = await client.post(
      '/export/consolidate',
      { document_ids: documentIds, year, base_currency: currency },
      { responseType: 'blob' }
    );
    const url = window.URL.createObjectURL(new Blob([res.data]));
    const link = document.createElement('a');
    link.href = url;
    link.setAttribute('download', `Annual_Consolidation_${year}.xlsx`);
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(url);
  },
};
```

---

### 3. `hooks/useOCRJob.ts` — React Hook with SSE & 10-Page Part Streaming

```typescript
import { useState, useEffect, useRef, useCallback } from 'react';
import { ocrApi } from '@/services/ocrApi';
import { DocumentType, ExtractionResponse, JobStatus } from '@/types/ocr';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1';

export interface OCRJobState {
  jobId: string | null;
  documentId: string | null;
  status: JobStatus;
  uploadProgress: number;
  processingProgress: number;
  currentStage: string;
  totalPages: number;
  processedPages: number;
  message: string;
  result: ExtractionResponse | null;
  error: string | null;
}

export function useOCRJob() {
  const [jobState, setJobState] = useState<OCRJobState>({
    jobId: null,
    documentId: null,
    status: 'idle',
    uploadProgress: 0,
    processingProgress: 0,
    currentStage: 'idle',
    totalPages: 0,
    processedPages: 0,
    message: '',
    result: null,
    error: null,
  });

  const eventSourceRef = useRef<EventSource | null>(null);
  const pollingTimerRef = useRef<NodeJS.Timeout | null>(null);

  const cleanup = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    if (pollingTimerRef.current) {
      clearInterval(pollingTimerRef.current);
      pollingTimerRef.current = null;
    }
  }, []);

  const fetchResult = async (docId: string) => {
    try {
      const data = await ocrApi.getDocument(docId);
      setJobState((prev) => ({
        ...prev,
        status: 'completed',
        processingProgress: 100,
        result: data,
      }));
    } catch (e: any) {
      setJobState((prev) => ({
        ...prev,
        status: 'failed',
        error: 'Failed to retrieve final extraction document.',
      }));
    }
  };

  const startPolling = useCallback((jobId: string, docId: string) => {
    if (pollingTimerRef.current) return;
    pollingTimerRef.current = setInterval(async () => {
      try {
        const job = await ocrApi.getJobStatus(jobId);
        setJobState((prev) => ({
          ...prev,
          status: job.status,
          processingProgress: job.progress,
          currentStage: job.current_stage,
          totalPages: job.total_pages,
          processedPages: job.processed_pages,
          message: job.message,
        }));

        if (job.status === 'completed') {
          cleanup();
          if (job.result) {
            setJobState((prev) => ({ ...prev, result: job.result, status: 'completed' }));
          } else {
            fetchResult(docId);
          }
        } else if (job.status === 'failed' || job.status === 'cancelled') {
          cleanup();
          setJobState((prev) => ({ ...prev, status: job.status, error: job.error }));
        }
      } catch (err) {
        console.error('Polling error:', err);
      }
    }, 2000);
  }, [cleanup]);

  const connectSSE = useCallback((jobId: string, docId: string) => {
    cleanup();
    const url = `${API_BASE_URL}/jobs/${jobId}/events`;
    const es = new EventSource(url);
    eventSourceRef.current = es;

    es.addEventListener('ocr.job.progress', (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      setJobState((prev) => ({
        ...prev,
        status: 'processing',
        processingProgress: data.progress,
        currentStage: data.current_stage,
        totalPages: data.total_pages,
        processedPages: data.processed_pages,
        message: data.message,
      }));
    });

    es.addEventListener('ocr.job.completed', () => {
      cleanup();
      fetchResult(docId);
    });

    es.addEventListener('ocr.job.failed', (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      cleanup();
      setJobState((prev) => ({ ...prev, status: 'failed', error: data.error }));
    });

    es.onerror = () => {
      es.close();
      startPolling(jobId, docId);
    };
  }, [cleanup, startPolling]);

  const uploadAndProcess = async (file: File, docType: DocumentType = 'auto', cleanWithAi = true) => {
    cleanup();
    setJobState({
      jobId: null,
      documentId: null,
      status: 'uploading',
      uploadProgress: 0,
      processingProgress: 0,
      currentStage: 'file_upload',
      totalPages: 0,
      processedPages: 0,
      message: 'Uploading document to server...',
      result: null,
      error: null,
    });

    try {
      const data = await ocrApi.uploadAsync(file, {
        documentType: docType,
        cleanWithAi,
        onUploadProgress: (pct) => setJobState((prev) => ({ ...prev, uploadProgress: pct })),
      });

      setJobState((prev) => ({
        ...prev,
        jobId: data.job_id,
        documentId: data.document_id,
        status: 'queued',
        currentStage: 'queued',
        message: 'Document queued. Starting 10-page chunked processing...',
      }));

      connectSSE(data.job_id, data.document_id);
    } catch (err: any) {
      setJobState((prev) => ({
        ...prev,
        status: 'failed',
        error: err.response?.data?.error || 'Failed to upload document.',
      }));
    }
  };

  const cancelJob = async () => {
    if (!jobState.jobId) return;
    try {
      await ocrApi.cancelJob(jobState.jobId);
      cleanup();
      setJobState((prev) => ({ ...prev, status: 'cancelled', message: 'Job was cancelled.' }));
    } catch (e) {
      console.error(e);
    }
  };

  const retryJob = async () => {
    if (!jobState.jobId) return;
    try {
      await ocrApi.retryJob(jobState.jobId);
      setJobState((prev) => ({ ...prev, status: 'queued', error: null, message: 'Retrying job...' }));
      if (jobState.documentId) {
        connectSSE(jobState.jobId, jobState.documentId);
      }
    } catch (e) {
      console.error(e);
    }
  };

  useEffect(() => {
    return () => cleanup();
  }, [cleanup]);

  return { jobState, uploadAndProcess, cancelJob, retryJob };
}
```

---

### 4. `components/AsyncDocumentProcessor.tsx` — Production UI Component

```tsx
'use client';

import React, { useState } from 'react';
import { useOCRJob } from '@/hooks/useOCRJob';
import { ocrApi } from '@/services/ocrApi';
import { DocumentType, ExportFormat } from '@/types/ocr';

export default function AsyncDocumentProcessor() {
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [docType, setDocType] = useState<DocumentType>('bank_statement');
  const { jobState, uploadAndProcess, cancelJob, retryJob } = useOCRJob();

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      setSelectedFile(e.target.files[0]);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (selectedFile) {
      uploadAndProcess(selectedFile, docType, true);
    }
  };

  const handleDownload = (format: ExportFormat) => {
    if (jobState.documentId) {
      ocrApi.downloadExport(jobState.documentId, format);
    }
  };

  return (
    <div className="max-w-4xl mx-auto p-6 bg-white dark:bg-zinc-900 rounded-2xl shadow-xl border border-zinc-200 dark:border-zinc-800">
      <div className="mb-6">
        <h2 className="text-2xl font-bold text-zinc-900 dark:text-white">
          ⚡ 100–200 Page Financial OCR Engine
        </h2>
        <p className="text-sm text-zinc-500 mt-1">
          Processes 100–200 page bank statements in 10-page parts with zero browser timeouts and live SSE updates.
        </p>
      </div>

      {/* 1. Upload Form */}
      {jobState.status === 'idle' && (
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="border-2 border-dashed border-zinc-300 dark:border-zinc-700 rounded-xl p-8 text-center hover:border-indigo-500 transition">
            <input
              type="file"
              accept=".pdf,.jpg,.jpeg,.png,.webp,.tiff"
              onChange={handleFileChange}
              className="block w-full text-sm text-zinc-500 file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-semibold file:bg-indigo-50 file:text-indigo-700 hover:file:bg-indigo-100"
            />
            {selectedFile && (
              <p className="mt-2 text-sm text-indigo-600 font-medium">
                Selected: {selectedFile.name} ({(selectedFile.size / (1024 * 1024)).toFixed(2)} MB)
              </p>
            )}
          </div>

          <div className="flex gap-4">
            <select
              value={docType}
              onChange={(e) => setDocType(e.target.value as DocumentType)}
              className="px-4 py-2 rounded-lg border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-800 text-sm font-medium"
            >
              <option value="auto">Auto-Detect Document Type</option>
              <option value="bank_statement">Bank Statement</option>
              <option value="invoice">Invoice</option>
              <option value="receipt">Receipt</option>
              <option value="general">General PDF / Document</option>
            </select>

            <button
              type="submit"
              disabled={!selectedFile}
              className="flex-1 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold py-2 px-6 rounded-lg transition disabled:opacity-50"
            >
              Start 10-Page Chunked Extraction
            </button>
          </div>
        </form>
      )}

      {/* 2. Processing & Live Progress Screen */}
      {(jobState.status === 'uploading' || jobState.status === 'queued' || jobState.status === 'processing') && (
        <div className="space-y-6 py-6">
          <div className="flex justify-between items-center">
            <div>
              <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold bg-indigo-100 text-indigo-800 uppercase tracking-wider">
                {jobState.status}
              </span>
              <h3 className="text-lg font-bold mt-2 text-zinc-900 dark:text-white">
                {jobState.message || 'Processing in background...'}
              </h3>
              {jobState.totalPages > 0 && (
                <p className="text-sm text-zinc-500 mt-1">
                  Overall Page Progress: <span className="font-bold text-indigo-600">{jobState.processedPages}</span> / {jobState.totalPages} pages
                </p>
              )}
            </div>
            <button
              onClick={cancelJob}
              className="px-4 py-2 text-xs font-semibold text-red-600 hover:bg-red-50 rounded-lg border border-red-200 transition"
            >
              Cancel Job
            </button>
          </div>

          {/* Progress Bar */}
          <div className="w-full bg-zinc-100 dark:bg-zinc-800 rounded-full h-3 overflow-hidden">
            <div
              className="bg-indigo-600 h-full rounded-full transition-all duration-300 ease-out"
              style={{
                width: `${jobState.status === 'uploading' ? jobState.uploadProgress : jobState.processingProgress}%`,
              }}
            />
          </div>

          <div className="flex justify-between text-xs text-zinc-400">
            <span>Stage: {jobState.currentStage}</span>
            <span>{jobState.status === 'uploading' ? `${jobState.uploadProgress}% Uploaded` : `${jobState.processingProgress}% Completed`}</span>
          </div>
        </div>
      )}

      {/* 3. Completion View & One-Click Downloads */}
      {jobState.status === 'completed' && jobState.result && (
        <div className="space-y-6">
          <div className="p-5 bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800 rounded-xl flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
            <div>
              <h4 className="font-bold text-emerald-800 dark:text-emerald-400">
                ✅ Extraction Completed Successfully!
              </h4>
              <p className="text-xs text-emerald-600 dark:text-emerald-500 mt-0.5">
                Document ID: {jobState.documentId} ({jobState.result.metadata?.pages} Pages processed in {(jobState.result.metadata?.processing_time_ms / 1000).toFixed(1)}s)
              </p>
            </div>

            {/* Export Buttons */}
            <div className="flex flex-wrap gap-2">
              <button
                onClick={() => handleDownload('xlsx')}
                className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold rounded-lg shadow transition"
              >
                Excel (.xlsx)
              </button>
              <button
                onClick={() => handleDownload('pdf')}
                className="px-3 py-1.5 bg-rose-600 hover:bg-rose-700 text-white text-xs font-semibold rounded-lg shadow transition"
              >
                Audit PDF
              </button>
              <button
                onClick={() => handleDownload('ofx')}
                className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold rounded-lg shadow transition"
              >
                OFX
              </button>
              <button
                onClick={() => handleDownload('qbo')}
                className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold rounded-lg shadow transition"
              >
                QuickBooks (.qbo)
              </button>
              <button
                onClick={() => handleDownload('csv')}
                className="px-3 py-1.5 bg-zinc-700 hover:bg-zinc-800 text-white text-xs font-semibold rounded-lg shadow transition"
              >
                CSV
              </button>
            </div>
          </div>

          {/* Structured Summary Preview */}
          <div className="p-4 bg-zinc-50 dark:bg-zinc-800/50 rounded-xl">
            <h5 className="font-bold text-sm mb-3">Structured Extraction Preview</h5>
            <pre className="text-xs font-mono bg-zinc-900 text-zinc-100 p-4 rounded-lg overflow-x-auto max-h-96">
              {JSON.stringify(jobState.result.extraction, null, 2)}
            </pre>
          </div>
        </div>
      )}

      {/* 4. Error View */}
      {jobState.status === 'failed' && (
        <div className="p-4 bg-rose-50 border border-rose-200 rounded-xl text-center space-y-3">
          <p className="text-sm font-semibold text-rose-700">❌ Processing Failed: {jobState.error}</p>
          <button
            onClick={retryJob}
            className="px-4 py-2 bg-rose-600 hover:bg-rose-700 text-white text-xs font-semibold rounded-lg transition"
          >
            Retry Processing
          </button>
        </div>
      )}
    </div>
  );
}
```

---

## 🔒 Security & Best Practices

1. **Keep Secrets in Environment Variables**: Store `NEXT_PUBLIC_API_KEY` in `.env.local` for frontend calls.
2. **Handle IDOR Protection**: The API automatically scopes document retrieval and job listing by API key hash. Users can never view or delete each other's documents.
3. **Use SSE Stream with Fallback**: The provided `useOCRJob` hook automatically connects to SSE and smoothly falls back to 2-second polling if proxy/firewall closes the SSE stream.
4. **Formula Injection Sanitization**: All exported `.csv` and `.xlsx` files sanitize leading dangerous formula characters (`=`, `+`, `-`, `@`) automatically.

---

## 🚀 Health Check & Readiness

Before making requests, test your connection with:

```bash
curl -X GET http://localhost:8000/api/v1/health
```

Expected Response (`200 OK`):
```json
{
  "status": "healthy",
  "app_name": "AI OCR Advance API",
  "version": "1.0.0",
  "database": "connected",
  "storage_accessible": true,
  "worker_status": "active",
  "timestamp": "2026-10-02T12:00:00Z"
}
```
