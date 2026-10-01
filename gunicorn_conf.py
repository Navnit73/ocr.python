"""
Gunicorn Configuration for production deployment with Uvicorn workers.
"""

import multiprocessing
import os

# Server socket
bind = os.getenv("GUNICORN_BIND", "0.0.0.0:8000")
backlog = 2048

# Worker processes
# Recommended: (2 x num_cores) + 1
default_workers = (multiprocessing.cpu_count() * 2) + 1
workers = int(os.getenv("WORKERS", min(default_workers, 4)))
worker_class = "uvicorn.workers.UvicornWorker"
timeout = int(os.getenv("TIMEOUT", "180"))
graceful_timeout = 30
keepalive = 5

# Process naming
proc_name = "fastapi_gunicorn_app"

# Logging
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info").lower()
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s µs'
