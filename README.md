# Clean FastAPI + Uvicorn & Gunicorn Setup

A clean, modular, production-ready FastAPI starter project configured with **Uvicorn** and **Gunicorn**.

---

## 📁 Project Structure

```
ocr.advance/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI application entry point, CORS, root & health endpoints
│   ├── api/
│   │   ├── __init__.py
│   │   └── v1/
│   │       ├── __init__.py
│   │       └── router.py    # API v1 routes (/health, /ping)
│   └── core/
│       ├── __init__.py
│       └── config.py        # Pydantic Settings configuration
├── gunicorn_conf.py         # Gunicorn configuration with Uvicorn workers
├── tests/
│   ├── __init__.py
│   └── test_main.py         # Async endpoint tests
├── .env.example             # Environment variable template
├── .env                     # Local environment settings
├── .gitignore
├── requirements.txt         # Dependencies (FastAPI, Uvicorn, Gunicorn, Pydantic)
└── README.md
```

---

## 🚀 Quickstart Guide

### 1. Setup Virtual Environment & Install Dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Run with Uvicorn (Development Mode)

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 3. Run with Gunicorn (Production Mode)

```bash
source .venv/bin/activate
gunicorn -c gunicorn_conf.py app.main:app
```

---

## 🔗 Endpoints & Documentation

Once the server is running, visit:
- **Interactive Swagger Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc Documentation**: [http://localhost:8000/redoc](http://localhost:8000/redoc)
- **Root API Info**: [http://localhost:8000/](http://localhost:8000/)
- **Health Check**: [http://localhost:8000/health](http://localhost:8000/health)
- **API v1 Health**: [http://localhost:8000/api/v1/health](http://localhost:8000/api/v1/health)
- **API v1 Ping**: [http://localhost:8000/api/v1/ping](http://localhost:8000/api/v1/ping)

---

## 🧪 Running Tests

```bash
source .venv/bin/activate
pytest -v
```
