# AI OCR Advance API

A lightweight, high-performance, stateless AI-powered OCR & Document Extraction API built with **FastAPI**, **PyMuPDF**, **PaddleOCR**, **OpenCV**, and the **DeepSeek API**.

The API accepts PDF documents and images (bank statements, receipts, invoices, general documents), extracts text, cleans/normalizes OCR artifacts, and returns validated 3-layer structured JSON with unique request tracking.

---

## ✨ Key Features

- **Stateless & Secure**: No database, no Mongo, no Cloudinary, no permanent file retention. Temporary files are safely cleaned up in `finally` blocks.
- **Frontend Request ID Support**: Frontend can pass a custom `request_id` in form-data or via the `X-Request-ID` header; the API echoes this ID back in responses and error logs.
- **3-Layer Output Architecture**:
  - **Layer 1 (Raw OCR)**: Unmodified text extracted via PyMuPDF / PaddleOCR.
  - **Layer 2 (Cleaned Text)**: DeepSeek-corrected and normalized text with prompt-injection defense.
  - **Layer 3 (Structured JSON)**: Typed Pydantic models for bank statements, receipts, invoices, and general documents with financial arithmetic checks.
- **High Performance & Async**: Digital PDFs are extracted directly via PyMuPDF without heavy OCR rendering; scanned pages and images run through OpenCV preprocessing and PaddleOCR inside worker thread pools.

---

## 📁 Project Architecture

```
ocr.advance/
├── app/
│   ├── api/
│   │   └── v1/
│   │       ├── endpoints/
│   │       │   └── ocr.py          # POST /api/v1/ocr/extract endpoint
│   │       └── router.py           # API v1 routes & health checks
│   ├── core/
│   │   └── config.py               # Pydantic Settings & environment config
│   ├── schemas/
│   │   ├── bank_statement.py       # Bank statement transactions & balance models
│   │   ├── receipt.py              # Receipt items, taxes, and total models
│   │   ├── invoice.py              # Invoice line items, tax, supplier/buyer models
│   │   ├── general.py              # Key-value pairs and summary models
│   │   └── ocr.py                  # 3-layer response, page, and metadata schemas
│   ├── services/
│   │   ├── file_validator.py       # File size, extension, magic signature checks & temp cleanup
│   │   ├── pdf_service.py          # PyMuPDF digital text extraction & scanned page rendering
│   │   ├── image_service.py        # OpenCV & Pillow contrast/deskew preprocessing
│   │   ├── ocr_service.py          # PaddleOCR engine with thread pool execution
│   │   ├── deepseek_client.py      # Async DeepSeek client with retries & timeouts
│   │   ├── ai_cleaner.py           # DeepSeek OCR text cleaning & normalization
│   │   ├── classifier.py           # Rule-based & AI document classification
│   │   ├── extractor.py            # Structured extraction & financial arithmetic auditing
│   │   └── pipeline.py             # Master pipeline orchestrator
│   └── main.py                     # FastAPI entry point, CORS, and error handlers
├── Dockerfile                      # Production container build
├── gunicorn_conf.py                # Gunicorn multi-worker configuration
├── requirements.txt                # Project dependencies
├── tests/                          # Automated Pytest test suite
└── README.md
```

---

## 📡 API Specification

### `POST /api/v1/ocr/extract`

Extracts text and structured financial data from an uploaded file.

#### Multipart Form Parameters:
| Field | Type | Required | Description | Default |
|---|---|---|---|---|
| `file` | Binary File | **Yes** | PDF, JPG, PNG, WEBP, or TIFF | - |
| `document_type` | String | No | `auto`, `bank_statement`, `receipt`, `invoice`, `general` | `auto` |
| `language` | String | No | OCR language hint (`auto`, `en`, `hi`, `es`, `fr`, `de`, `ch`) | `en` |
| `clean_with_ai` | Boolean | No | Whether to perform DeepSeek cleaning and structured extraction | `true` |
| `request_id` | String | No | Custom ID provided by frontend to correlate documents | Auto UUID |

#### Example Response (`200 OK` — Bank Statement):
```json
{
  "id": "req_frontend_99182",
  "status": "success",
  "document_type": "bank_statement",
  "extraction": {
    "bank_name": "Standard Bank",
    "account_holder": "John Doe",
    "account_number_masked": "XXXX-4321",
    "currency": "USD",
    "statement_period": "2026-01-01 to 2026-01-31",
    "opening_balance": 5000.0,
    "closing_balance": 5400.0,
    "transactions": [
      {
        "date": "2026-01-05",
        "description": "Utility Bill Payment",
        "reference": "UPI982312",
        "debit": 100.0,
        "credit": null,
        "balance": 4900.0
      },
      {
        "date": "2026-01-10",
        "description": "Client Wire Deposit",
        "reference": "WIRE7712",
        "debit": null,
        "credit": 500.0,
        "balance": 5400.0
      }
    ]
  },
  "raw_text": "Standard Bank Account Statement\nAccount: XXXX-4321...",
  "cleaned_text": "Standard Bank Account Statement\nAccount: XXXX-4321...",
  "pages": [
    {
      "page_number": 1,
      "text": "Standard Bank...",
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
    "processing_time_ms": 1420,
    "stage_timings_ms": {
      "validation": 2,
      "ocr_extraction": 15,
      "ai_cleaning": 520,
      "classification": 1,
      "structured_extraction": 880
    }
  },
  "warnings": []
}
```

---

## 🛠️ Setup & Running

### 1. Environment Setup

```bash
# Clone repository and enter directory
cd ocr.advance

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment variables
cp .env.example .env
```

Edit `.env` and configure your `DEEPSEEK_API_KEY`:
```env
DEEPSEEK_API_KEY=your_actual_deepseek_api_key
DEEPSEEK_MODEL=deepseek-chat
MAX_UPLOAD_SIZE_MB=25
MAX_PDF_PAGES=50
```

### 2. Run Development Server

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 3. Run Production Server with Gunicorn

```bash
gunicorn -c gunicorn_conf.py app.main:app
```

### 4. Run with Docker

```bash
docker build -t ocr-advance-api .
docker run -p 8000:8000 --env-file .env ocr-advance-api
```

---

## 🧪 Testing

Run the full automated test suite:

```bash
pytest -v
```
