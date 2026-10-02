"""
Unit Tests for MongoDB Repositories: JobRepository, DocumentRepository, and WebhookRepository.
"""

from datetime import datetime, timezone
import pytest
import pytest_asyncio
from app.db.mongodb import MongoDBManager
from app.db.repositories.job_repo import JobRepository
from app.db.repositories.document_repo import DocumentRepository
from app.db.repositories.webhook_repo import WebhookRepository


@pytest_asyncio.fixture(autouse=True)
async def init_db():
    """Ensure database connection with in-memory mock before tests."""
    await MongoDBManager.connect(force_mock=True)
    yield
    await MongoDBManager.disconnect()


@pytest.mark.asyncio
async def test_job_repository_lifecycle():
    """Tests full lifecycle of JobRepository."""
    job_id = "job_test_123"
    user_id = "hash_owner_1"

    # 1. Create Job
    job = await JobRepository.create_job({
        "job_id": job_id,
        "document_id": "doc_test_123",
        "user_id": user_id,
        "status": "queued",
        "total_pages": 10,
        "file_reference": "/tmp/test.pdf",
    })
    assert job["job_id"] == job_id
    assert job["status"] == "queued"

    # 2. Get Job
    retrieved = await JobRepository.get_job(job_id, owner_hash=user_id)
    assert retrieved is not None
    assert retrieved["job_id"] == job_id

    # Test IDOR protection: other user cannot get job
    unauthorized = await JobRepository.get_job(job_id, owner_hash="other_user_hash")
    assert unauthorized is None

    # 3. Start Job
    await JobRepository.start_job(job_id, total_pages=10)
    started = await JobRepository.get_job(job_id)
    assert started["status"] == "processing"
    assert started["current_stage"] == "ocr_extraction"
    assert started["started_at"] is not None

    # 4. Update Progress
    await JobRepository.update_progress(
        job_id=job_id,
        stage="ocr_extraction",
        progress=50,
        processed_pages=5,
        total_pages=10,
        message="Processed 5 of 10 pages",
    )
    progress_job = await JobRepository.get_job(job_id)
    assert progress_job["progress"] == 50
    assert progress_job["processed_pages"] == 5

    # 5. Complete Job
    mock_result = {"id": "doc_test_123", "raw_text": "Extracted text"}
    await JobRepository.complete_job(
        job_id=job_id,
        result_dict=mock_result,
        document_id="doc_test_123",
        metadata_dict={"pages": 10, "processing_time_ms": 1500},
    )
    completed = await JobRepository.get_job(job_id)
    assert completed["status"] == "completed"
    assert completed["progress"] == 100
    assert completed["completed_at"] is not None
    assert completed["result"]["raw_text"] == "Extracted text"


@pytest.mark.asyncio
async def test_job_repository_failure_and_retry():
    """Tests job failure, retry increment, and cancellation."""
    job_id = "job_fail_test"
    user_id = "hash_owner_fail"

    await JobRepository.create_job({
        "job_id": job_id,
        "user_id": user_id,
        "status": "processing",
    })

    # Fail
    await JobRepository.fail_job(job_id, error_message="Network timeout during DeepSeek call")
    failed = await JobRepository.get_job(job_id)
    assert failed["status"] == "failed"
    assert "Network timeout" in failed["error"]

    # Increment retry
    new_retry = await JobRepository.increment_retry(job_id)
    assert new_retry == 1
    retried = await JobRepository.get_job(job_id)
    assert retried["status"] == "queued"
    assert retried["retry_count"] == 1

    # Cancel
    cancelled = await JobRepository.cancel_job(job_id, owner_hash=user_id)
    assert cancelled is True
    cancelled_job = await JobRepository.get_job(job_id)
    assert cancelled_job["status"] == "cancelled"


@pytest.mark.asyncio
async def test_document_repository():
    """Tests DocumentRepository save, get, search, list, delete."""
    doc_id = "doc_bank_stmt_999"
    user_id = "hash_user_abc"

    # Save
    await DocumentRepository.save_document({
        "document_id": doc_id,
        "user_id": user_id,
        "filename": "january_bank_statement.pdf",
        "document_type": "bank_statement",
        "status": "success",
        "pages_count": 3,
        "raw_text": "State Bank of India Statement Account Balance 50000",
        "extraction": {"bank_name": "State Bank of India", "closing_balance": 50000.0},
    })

    # Get
    doc = await DocumentRepository.get_document(doc_id, owner_hash=user_id)
    assert doc is not None
    assert doc["document_id"] == doc_id
    assert doc["extraction"]["bank_name"] == "State Bank of India"

    # IDOR check
    assert await DocumentRepository.get_document(doc_id, owner_hash="wrong_hash") is None

    # List & Search
    items, total = await DocumentRepository.list_documents(
        owner_hash=user_id,
        search="State Bank",
        document_type="bank_statement",
    )
    assert total >= 1
    assert any(i["id"] == doc_id for i in items)

    # Delete
    deleted = await DocumentRepository.delete_document(doc_id, owner_hash=user_id)
    assert deleted is True
    assert await DocumentRepository.get_document(doc_id) is None


@pytest.mark.asyncio
async def test_webhook_repository():
    """Tests WebhookRepository record and retrieval."""
    job_id = "job_webhook_audit"

    # Record delivery
    rec = await WebhookRepository.record_delivery(
        job_id=job_id,
        event="ocr.job.completed",
        url="https://api.example.com/webhook",
        status_code=200,
        success=True,
        attempts=1,
    )
    assert rec["job_id"] == job_id
    assert rec["success"] is True

    # Retrieve deliveries
    deliveries = await WebhookRepository.get_deliveries_for_job(job_id)
    assert len(deliveries) >= 1
    assert deliveries[0]["event"] == "ocr.job.completed"
