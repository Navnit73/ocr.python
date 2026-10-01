"""
Common API Request & Response Schemas.
"""

from typing import Generic, List, Optional, TypeVar
from pydantic import BaseModel, Field

T = TypeVar("T")


class APIResponse(BaseModel, Generic[T]):
    """Standardized API Response Envelope."""
    success: bool = Field(default=True, description="Indicates if the request was successful")
    message: Optional[str] = Field(default=None, description="Optional informational message")
    data: Optional[T] = Field(default=None, description="Response payload data")
    error: Optional[dict] = Field(default=None, description="Error details if success is False")


class PaginationMeta(BaseModel):
    """Pagination metadata model."""
    page: int = Field(default=1, ge=1, description="Current page number (1-indexed)")
    page_size: int = Field(default=20, ge=1, le=100, description="Number of items per page")
    total_items: int = Field(default=0, ge=0, description="Total count of items matching criteria")
    total_pages: int = Field(default=0, ge=0, description="Total count of available pages")


class PaginatedResponse(BaseModel, Generic[T]):
    """Standardized paginated list response."""
    items: List[T] = Field(default_factory=list, description="List of items on current page")
    pagination: PaginationMeta = Field(description="Pagination details")
