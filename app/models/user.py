"""
User Document Data Model.
"""

from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, EmailStr, Field
from app.core.constants import UserRole


class UserModel(BaseModel):
    """User representation for MongoDB and internal services."""
    id: Optional[str] = Field(default=None, description="MongoDB Document ID")
    email: EmailStr
    full_name: Optional[str] = None
    hashed_password: str
    role: UserRole = Field(default=UserRole.USER)
    is_active: bool = True
    is_verified: bool = False
    usage_quota_pages: int = Field(default=100, description="Monthly OCR page quota")
    pages_processed: int = Field(default=0, description="Pages processed in current cycle")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
