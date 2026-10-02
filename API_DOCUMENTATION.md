# 📚 AI OCR Advance API — Complete Developer & Frontend Integration Guide

Welcome to the **AI OCR Advance API** documentation. This enterprise-grade, asynchronous document processing platform is built for heavy-duty financial OCR (handling **100–200 page bank statements**, invoices, receipts, and multi-file archives).

---

## 🏗️ Asynchronous Processing Architecture

Large documents (50–200 pages) can take 15–60 seconds to extract, clean with AI, and structure. Keeping an HTTP connection open is prone to proxy/browser timeouts. The **AI OCR Advance API** uses an asynchronous background worker architecture:

```
┌─────────────────┐             ┌─────────────────────┐              ┌────────────────────────┐
│ Next.js Frontend│             │ FastAPI Backend API │              │ Celery/Async Workers   │
└────────┬────────┘             └──────────┬──────────┘              └───────────┬────────────┘
         │                                 │                                     │
         │─── 1. POST /jobs/upload ───────▶│ (Saves File to Storage)             │
         │◀── 2. 202 Accepted {job_id} ────│ (MongoDB status="queued")           │
         │                                 │─── 3. Enqueue Job Task ────────────▶│
         │                                 │                                     │─── 4. Extract Pages (1..200)
         │─── 5. Connect SSE /jobs/{id}───▶│                                     │─── 5. Run OCR & AI Cleaning
         │◀── 6. Stream Live Progress ─────│◀── Broadcast Event (Stage/Page) ────│─── 6. Validate Balance & Tax
         │                                 │                                     │
         │                                 │◀── 7. Save Result in MongoDB ───────│─── 7. Job Completed (100%)
         │◀── 8. Push "completed" Event ───│                                     │
         │                                 │                                     │─── 8. Dispatch HMAC Webhook ──▶ [Client Webhook]
         │─── 9. GET /documents/{id} ─────▶│                                     │
         │◀── 10. Display Complete JSON ───│                                     │
```

---

## 🔑 Global API Headers & Authentication

All requests to `/api/v1/jobs/*`, `/api/v1/ocr/*`, `/api/v1/documents/*`, and `/api/v1/export/*` require API key authentication.

| Header | Example Value | Description |
|---|---|---|
| `X-API-Key` | `ocr_dev_key_secret_2026` | API Key authentication header |
| `Authorization` | `Bearer ocr_dev_key_secret_2026` | Alternative Bearer token header |
| `X-Request-ID` | `req_frontend_user_123` | *(Optional)* Unique custom request ID |

---

## 📖 Complete Endpoint Reference Table

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/jobs/upload` | **Async Upload**: Upload document (100–200 pages), queues job, returns `202 Accepted` immediately |
| `GET` | `/api/v1/jobs/{job_id}` | **Job Status**: Get real-time progress %, page counters, stage, and completion status |
| `GET` | `/api/v1/jobs/{job_id}/events` | **Live SSE Stream**: Real-time Server-Sent Events push stream (no polling needed) |
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
| `GET` | `/api/v1/admin/stats` | **Admin Metrics**: Real database metrics, job status counts, page totals, worker health |
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
  "total_pages": 120,
  "processed_pages": 54,
  "current_stage": "ocr_extraction",
  "message": "Extracted page 54 of 120",
  "created_at": "2026-10-02T12:00:00Z",
  "updated_at": "2026-10-02T12:00:15Z",
  "started_at": "2026-10-02T12:00:01Z",
  "completed_at": null,
  "result": null,
  "result_url": null,
  "error": null,
  "retry_count": 0,
  "metadata": {
    "filename": "annual_bank_statement_2026.pdf",
    "file_size_bytes": 14285714
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
  "total_pages": 120,
  "processed_pages": 120,
  "current_stage": "completed",
  "message": "Processing completed successfully",
  "created_at": "2026-10-02T12:00:00Z",
  "updated_at": "2026-10-02T12:00:42Z",
  "started_at": "2026-10-02T12:00:01Z",
  "completed_at": "2026-10-02T12:00:42Z",
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
      "pages": 120,
      "ocr_used": true,
      "ocr_engine": "paddleocr",
      "ai_cleaned": true,
      "processing_time_ms": 41200
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
data: {"job_id": "job_a1b2c3d4e5f6", "status": "processing", "progress": 45, "processed_pages": 54, "total_pages": 120, "message": "Extracted page 54 of 120"}

event: ocr.job.completed
data: {"job_id": "job_a1b2c3d4e5f6", "status": "completed", "progress": 100, "result_url": "/api/v1/documents/doc_9a8b7c6d5e4f"}
```

---

### 4. `GET /api/v1/admin/stats`
Admin dashboard statistics calculated directly from persistent database records.

#### Response (`200 OK`):
```json
{
  "total_jobs": 1540,
  "queued_jobs": 2,
  "processing_jobs": 4,
  "completed_jobs": 1510,
  "failed_jobs": 20,
  "cancelled_jobs": 4,
  "total_pages_processed": 45890,
  "average_processing_time_ms": 12450.5,
  "retry_count_total": 35,
  "worker_health": {
    "mode": "async_worker_pool",
    "active_workers": 4,
    "concurrency_limit": 4,
    "current_active_jobs": 4,
    "queue_size": 2,
    "celery_connected": false,
    "redis_connected": true,
    "mongodb_connected": true,
    "uptime_seconds": 86400.0,
    "cpu_percent": 24.5,
    "memory_percent": 41.2
  },
  "recent_errors": [
    {
      "job_id": "job_failed_99",
      "error": "Corrupted PDF header signature",
      "timestamp": "2026-10-02T11:45:00Z"
    }
  ]
}
```

---

## 🔔 Webhook Event Protocol & HMAC-SHA256 Verification

When background processing reaches key stages, the system automatically posts signed JSON events to your configured `callback_url`.

### Supported Webhook Events:
- `ocr.job.started`
- `ocr.job.progress`
- `ocr.job.completed`
- `ocr.job.failed`

### Webhook Completion Payload:
```json
{
  "event": "ocr.job.completed",
  "event_id": "evt_9876543210abcdef",
  "job_id": "job_a1b2c3d4e5f6",
  "document_id": "doc_9a8b7c6d5e4f",
  "status": "completed",
  "timestamp": "2026-10-02T12:05:00Z",
  "result_url": "/api/v1/documents/doc_9a8b7c6d5e4f",
  "metadata": {
    "total_pages": 120,
    "processed_pages": 120,
    "processing_time_ms": 41200
  }
}
```

### Webhook HMAC-SHA256 Signature Verification (Next.js / Node.js):
```typescript
import crypto from 'crypto';
import { NextRequest, NextResponse } from 'next/server';

const WEBHOOK_SECRET = process.env.OCR_WEBHOOK_SECRET || 'test_secret_key_123';

export async function POST(req: NextRequest) {
  const signatureHeader = req.headers.get('x-webhook-signature'); // e.g. "sha256=abcdef..."
  const rawBody = await req.text();

  if (!signatureHeader) {
    return NextResponse.json({ error: 'Missing signature header' }, { status: 401 });
  }

  const expectedSignature = 'sha256=' + crypto
    .createHmac('sha256', WEBHOOK_SECRET)
    .update(rawBody)
    .digest('hex');

  const isValid = crypto.timingSafeEqual(
    Buffer.from(signatureHeader),
    Buffer.from(expectedSignature)
  );

  if (!isValid) {
    return NextResponse.json({ error: 'Invalid HMAC signature' }, { status: 403 });
  }

  const eventPayload = JSON.parse(rawBody);
  console.log(`✅ Webhook verified: ${eventPayload.event} for job ${eventPayload.job_id}`);

  if (eventPayload.event === 'ocr.job.completed') {
    // Retrieve full extraction from result_url
    console.log(`Document ready at: ${eventPayload.result_url}`);
  }

  return NextResponse.json({ received: true });
}
```

---

## 💻 Next.js & React Frontend Integration

Below is the production-grade **Custom React Hook (`useOCRJob`)** and **UI Component** that:
- Uploads documents with real upload progress.
- Connects to the **Server-Sent Events (SSE)** endpoint.
- Automatically falls back to **polling** if SSE disconnects.
- Shows live multi-stage progress (e.g. `Extracting page 45 of 120`).
- Fetches and displays final results automatically upon completion without requiring page refresh!

### 1. `hooks/useOCRJob.ts` (React Hook)

```typescript
import { useState, useEffect, useRef, useCallback } from 'react';
import axios from 'axios';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1';
const API_KEY = process.env.NEXT_PUBLIC_API_KEY || 'ocr_dev_key_secret_2026';

export interface OCRJobState {
  jobId: string | null;
  documentId: string | null;
  status: 'idle' | 'uploading' | 'queued' | 'processing' | 'completed' | 'failed' | 'cancelled';
  uploadProgress: number;
  processingProgress: number;
  currentStage: string;
  totalPages: number;
  processedPages: number;
  message: string;
  result: any | null;
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

  const cleanupListeners = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    if (pollingTimerRef.current) {
      clearInterval(pollingTimerRef.current);
      pollingTimerRef.current = null;
    }
  }, []);

  const fetchFinalResult = async (docId: string) => {
    try {
      const res = await axios.get(`${API_BASE_URL}/documents/${docId}`, {
        headers: { 'X-API-Key': API_KEY },
      });
      setJobState((prev) => ({
        ...prev,
        status: 'completed',
        processingProgress: 100,
        result: res.data,
      }));
    } catch (err: any) {
      setJobState((prev) => ({
        ...prev,
        status: 'failed',
        error: 'Failed to fetch final extraction result.',
      }));
    }
  };

  const startPolling = useCallback((jobId: string, docId: string) => {
    if (pollingTimerRef.current) return;
    pollingTimerRef.current = setInterval(async () => {
      try {
        const res = await axios.get(`${API_BASE_URL}/jobs/${jobId}`, {
          headers: { 'X-API-Key': API_KEY },
        });
        const job = res.data;
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
          cleanupListeners();
          if (job.result) {
            setJobState((prev) => ({ ...prev, result: job.result, status: 'completed' }));
          } else {
            fetchFinalResult(docId);
          }
        } else if (job.status === 'failed' || job.status === 'cancelled') {
          cleanupListeners();
          setJobState((prev) => ({ ...prev, status: job.status, error: job.error }));
        }
      } catch (e) {
        console.error('Polling error:', e);
      }
    }, 2000);
  }, [cleanupListeners]);

  const connectSSE = useCallback((jobId: string, docId: string) => {
    cleanupListeners();

    // EventSource does not support custom headers natively; pass key via query parameter if needed or use fetch
    const url = `${API_BASE_URL}/jobs/${jobId}/events`;
    const es = new EventSource(url);
    eventSourceRef.current = es;

    es.addEventListener('ocr.job.progress', (event: MessageEvent) => {
      const data = JSON.parse(event.data);
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

    es.addEventListener('ocr.job.completed', (event: MessageEvent) => {
      cleanupListeners();
      fetchFinalResult(docId);
    });

    es.addEventListener('ocr.job.failed', (event: MessageEvent) => {
      const data = JSON.parse(event.data);
      cleanupListeners();
      setJobState((prev) => ({ ...prev, status: 'failed', error: data.error }));
    });

    es.onerror = () => {
      // Fallback to polling on SSE disconnection
      es.close();
      startPolling(jobId, docId);
    };
  }, [cleanupListeners, startPolling]);

  const uploadAndProcess = async (file: File, documentType = 'auto', cleanWithAi = true) => {
    cleanupListeners();
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

    const formData = new FormData();
    formData.append('file', file);
    formData.append('document_type', documentType);
    formData.append('clean_with_ai', cleanWithAi ? 'true' : 'false');

    try {
      const res = await axios.post(`${API_BASE_URL}/jobs/upload`, formData, {
        headers: {
          'X-API-Key': API_KEY,
          'Content-Type': 'multipart/form-data',
        },
        onUploadProgress: (progressEvent) => {
          if (progressEvent.total) {
            const percent = Math.round((progressEvent.loaded * 100) / progressEvent.total);
            setJobState((prev) => ({ ...prev, uploadProgress: percent }));
          }
        },
      });

      const { job_id, document_id } = res.data;
      setJobState((prev) => ({
        ...prev,
        jobId: job_id,
        documentId: document_id,
        status: 'queued',
        currentStage: 'queued',
        message: 'Document queued for background processing...',
      }));

      // Connect SSE stream
      connectSSE(job_id, document_id);
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
      await axios.post(`${API_BASE_URL}/jobs/${jobState.jobId}/cancel`, {}, {
        headers: { 'X-API-Key': API_KEY },
      });
      cleanupListeners();
      setJobState((prev) => ({ ...prev, status: 'cancelled', message: 'Job was cancelled.' }));
    } catch (e: any) {
      console.error('Cancel failed:', e);
    }
  };

  const retryJob = async () => {
    if (!jobState.jobId) return;
    try {
      await axios.post(`${API_BASE_URL}/jobs/${jobState.jobId}/retry`, {}, {
        headers: { 'X-API-Key': API_KEY },
      });
      setJobState((prev) => ({ ...prev, status: 'queued', error: null, message: 'Retrying job...' }));
      if (jobState.documentId) {
        connectSSE(jobState.jobId, jobState.documentId);
      }
    } catch (e: any) {
      console.error('Retry failed:', e);
    }
  };

  useEffect(() => {
    return () => cleanupListeners();
  }, [cleanupListeners]);

  return { jobState, uploadAndProcess, cancelJob, retryJob };
}
```

---

### 2. `components/AsyncDocumentProcessor.tsx` (React Component)

```tsx
'use client';

import React, { useState } from 'react';
import { useOCRJob } from '@/hooks/useOCRJob';

export default function AsyncDocumentProcessor() {
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [docType, setDocType] = useState('bank_statement');
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

  return (
    <div className="max-w-4xl mx-auto p-6 bg-white dark:bg-zinc-900 rounded-2xl shadow-xl border border-zinc-200 dark:border-zinc-800">
      <h2 className="text-2xl font-bold text-zinc-900 dark:text-white mb-2">
        ⚡ Asynchronous Financial OCR Engine
      </h2>
      <p className="text-sm text-zinc-500 mb-6">
        Processes 100–200 page bank statements and invoices independently in the background.
      </p>

      {/* Upload Form */}
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
              onChange={(e) => setDocType(e.target.value)}
              className="px-4 py-2 rounded-lg border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-800 text-sm"
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
              Start Asynchronous Processing
            </button>
          </div>
        </form>
      )}

      {/* Processing & Progress Screen */}
      {(jobState.status === 'uploading' || jobState.status === 'queued' || jobState.status === 'processing') && (
        <div className="space-y-6 py-6">
          <div className="flex justify-between items-center">
            <div>
              <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-medium bg-indigo-100 text-indigo-800 uppercase tracking-wider">
                {jobState.status}
              </span>
              <h3 className="text-lg font-semibold mt-2 text-zinc-900 dark:text-white">
                {jobState.message || 'Processing in background...'}
              </h3>
              {jobState.totalPages > 0 && (
                <p className="text-sm text-zinc-500">
                  Page Progress: <span className="font-semibold text-indigo-600">{jobState.processedPages}</span> / {jobState.totalPages} pages
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

      {/* Completion View */}
      {jobState.status === 'completed' && jobState.result && (
        <div className="space-y-6">
          <div className="p-4 bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800 rounded-xl flex justify-between items-center">
            <div>
              <h4 className="font-semibold text-emerald-800 dark:text-emerald-400">
                ✅ Extraction Completed Successfully!
              </h4>
              <p className="text-xs text-emerald-600 dark:text-emerald-500">
                Document ID: {jobState.documentId} ({jobState.result.metadata?.pages} Pages processed in {(jobState.result.metadata?.processing_time_ms / 1000).toFixed(1)}s)
              </p>
            </div>
            <a
              href={`${API_BASE_URL}/export/download/${jobState.documentId}?format=xlsx`}
              className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold rounded-lg shadow transition"
            >
              Download Excel (.xlsx)
            </a>
          </div>

          {/* Structured Summary Preview */}
          <div className="p-4 bg-zinc-50 dark:bg-zinc-800/50 rounded-xl">
            <h5 className="font-bold text-sm mb-3">Structured Financial Extraction</h5>
            <pre className="text-xs font-mono bg-zinc-900 text-zinc-100 p-4 rounded-lg overflow-x-auto max-h-96">
              {JSON.stringify(jobState.result.extraction, null, 2)}
            </pre>
          </div>
        </div>
      )}

      {/* Error View */}
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

## 🚀 Docker & Production Celery Worker Deployment

To run the complete asynchronous system in production with MongoDB, Redis, and distributed Celery workers:

```bash
# 1. Start all services in detached mode
docker compose up -d

# 2. View logs for background OCR workers
docker compose logs -f worker

# 3. Check health of the API
curl http://localhost:8000/health
```

The system automatically handles:
- **Crash Recovery**: If a worker terminates, orphaned jobs are safely resumed or marked for retry upon restart.
- **SSRF Defense**: Prevents arbitrary loopback callbacks in production.
- **IDOR Protection**: Documents and jobs are scoped by API key ownership.
- **Failover**: Runs gracefully with Celery/Redis or internal asynchronous worker pools without external dependencies.
