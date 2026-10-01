"""
Health and Diagnostic Check Schemas.
"""

from datetime import datetime
from typing import Dict, Optional
from pydantic import BaseModel, Field


class ServiceHealth(BaseModel):
    """Health status of an individual external dependency."""
    status: str = Field(description="'connected', 'disconnected', or 'configured'")
    latency_ms: Optional[float] = Field(default=None, description="Check latency in milliseconds")
    details: Optional[str] = Field(default=None, description="Additional context or version info")


class HealthCheckResponse(BaseModel):
    """Detailed System Health Check Response."""
    status: str = Field(description="Overall health status ('healthy', 'degraded', 'unhealthy')")
    app_name: str
    version: str
    environment: str
    timestamp: datetime
    services: Dict[str, ServiceHealth]


class SimplePingResponse(BaseModel):
    """Lightweight ping response for load balancers."""
    status: str = "ok"
    timestamp: datetime
