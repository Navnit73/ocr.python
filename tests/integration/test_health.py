"""
Integration tests for Health check, Readiness, and Liveness endpoints.
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_root_endpoint(async_client: AsyncClient):
    """Tests GET / returns API metadata and online status."""
    response = await async_client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "online"
    assert "version" in data
    assert data["api_v1"] == "/api/v1"


@pytest.mark.asyncio
async def test_health_ping_endpoint(async_client: AsyncClient):
    """Tests GET /api/v1/health/ping."""
    response = await async_client.get("/api/v1/health/ping")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "timestamp" in data


@pytest.mark.asyncio
async def test_health_liveness_endpoint(async_client: AsyncClient):
    """Tests GET /api/v1/health/liveness."""
    response = await async_client.get("/api/v1/health/liveness")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "alive"


@pytest.mark.asyncio
async def test_health_readiness_endpoint(async_client: AsyncClient):
    """Tests GET /api/v1/health/readiness."""
    response = await async_client.get("/api/v1/health/readiness")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert "checks" in data
    assert data["checks"]["mongodb"] == "connected"
    assert data["checks"]["redis"] == "connected"


@pytest.mark.asyncio
async def test_comprehensive_health_endpoint(async_client: AsyncClient):
    """Tests GET /api/v1/health."""
    response = await async_client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "services" in data
    assert "mongodb" in data["services"]
    assert "redis" in data["services"]
    assert "storage" in data["services"]
    assert data["services"]["mongodb"]["status"] == "connected"
    assert data["services"]["redis"]["status"] == "connected"


@pytest.mark.asyncio
async def test_router_status_stubs(async_client: AsyncClient):
    """Tests that auth, documents, and jobs status endpoints are responding."""
    endpoints = [
        "/api/v1/auth/status",
        "/api/v1/documents/status",
        "/api/v1/jobs/status",
    ]
    for ep in endpoints:
        resp = await async_client.get(ep)
        assert resp.status_code == 200
        json_data = resp.json()
        assert json_data["success"] is True
