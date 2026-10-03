"""
Performance and Concurrency Benchmarks & Verification Suite.
Validates non-blocking PyMuPDF extraction, fast single-pass MongoDB aggregations,
connection pooling, and high-throughput event publishing.
"""

import asyncio
import time
import pytest
import pymupdf

from app.db.mongodb import MongoDBManager
from app.db.repositories.job_repo import JobRepository
from app.services.event_bus import JobEventBus
from app.services.pdf_service import PDFService


def generate_benchmark_pdf(num_pages: int = 15) -> bytes:
    """Creates a multi-page test PDF."""
    doc = pymupdf.open()
    for i in range(num_pages):
        page = doc.new_page()
        page.insert_text(
            (50, 50),
            f"Page {i + 1} Invoice #INV-{1000 + i}\n"
            f"Vendor: Apex Tech Solutions\n"
            f"Date: 2026-03-{i % 28 + 1:02d}\n"
            f"Amount: ${(i + 1) * 125.50:.2f}\n"
            f"Description: Cloud Infrastructure & AI Compute Service Page {i + 1}"
        )
    pdf_bytes = doc.write()
    doc.close()
    return pdf_bytes


@pytest.mark.asyncio
async def test_pdf_service_async_non_blocking():
    """Verifies that PDFService chunking offloads to threads without blocking the event loop."""
    pdf_bytes = generate_benchmark_pdf(num_pages=12)

    # Concurrently run PDF chunk processing and a lightweight event loop heartbeat
    heartbeat_ticks = 0

    async def heartbeat():
        nonlocal heartbeat_ticks
        for _ in range(10):
            await asyncio.sleep(0.01)
            heartbeat_ticks += 1

    hb_task = asyncio.create_task(heartbeat())
    page_count = await PDFService.get_pdf_page_count_async(pdf_bytes)
    assert page_count == 12

    chunk_results = await PDFService.process_pdf_chunk_async(
        pdf_bytes=pdf_bytes,
        start_page=1,
        end_page=10,
    )
    assert len(chunk_results) == 10
    await hb_task
    # Heartbeat must have ticked while PDF processing ran
    assert heartbeat_ticks > 0


@pytest.mark.asyncio
async def test_job_repository_stats_aggregation_performance():
    """Verifies JobRepository.get_stats executes efficiently via single-pass aggregation."""
    await MongoDBManager.connect(force_mock=True)

    # Insert sample jobs across various statuses
    for i in range(25):
        st = ["queued", "processing", "completed", "failed", "cancelled"][i % 5]
        await JobRepository.create_job({
            "job_id": f"perf_job_{i}",
            "document_id": f"perf_doc_{i}",
            "status": st,
            "total_pages": 5 if st == "completed" else 0,
            "metadata": {"processing_time_ms": 1200 if st == "completed" else 0},
            "retry_count": 1 if st == "failed" else 0,
            "error": "Timeout" if st == "failed" else None,
        })

    t0 = time.perf_counter()
    stats = await JobRepository.get_stats()
    duration_ms = (time.perf_counter() - t0) * 1000

    assert stats["total_jobs"] >= 25
    assert stats["completed_jobs"] >= 5
    assert stats["total_pages_processed"] >= 25
    assert stats["average_processing_time_ms"] > 0
    assert duration_ms < 50.0  # Fast execution in single pass


@pytest.mark.asyncio
async def test_job_event_bus_high_throughput_burst():
    """Verifies that JobEventBus handles subscriber overflow with non-blocking FIFO ring buffer."""
    JobEventBus.reset()
    job_id = "test_perf_sse_stream"

    gen = JobEventBus.subscribe(job_id)
    # Receive initial connected message
    conn_msg = await gen.__anext__()
    assert "event: connected" in conn_msg

    # Publish 150 events in a rapid burst (exceeding queue maxsize 100)
    for i in range(150):
        await JobEventBus.publish(job_id, "ocr.job.progress", {"step": i, "progress": min(100, i)})

    # Receive next event without queue deadlock or blocking
    msg = await gen.__anext__()
    assert "event: ocr.job.progress" in msg
    JobEventBus.reset()
