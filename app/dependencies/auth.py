"""
Authentication and Authorization Dependencies.
"""

from typing import List, Optional
from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.constants import UserRole
from app.core.exceptions import AuthenticationException, AuthorizationException
from app.core.security import decode_jwt_token
from app.db.mongo import get_db
from app.models.user import UserModel

http_bearer = HTTPBearer(auto_error=False)


async def get_current_user_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(http_bearer),
) -> dict:
    """Extracts and verifies JWT token from Authorization header."""
    if not credentials:
        raise AuthenticationException("Authorization header is missing or invalid.")
    
    payload = decode_jwt_token(credentials.credentials)
    if not payload.get("sub"):
        raise AuthenticationException("Invalid token subject.")
    return payload


async def get_current_user(
    token_payload: dict = Depends(get_current_user_token),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> UserModel:
    """Fetches the user document corresponding to the JWT sub."""
    user_id = token_payload.get("sub")
    doc = await db["users"].find_one({"$or": [{"id": user_id}, {"_id": user_id}]})
    if not doc:
        raise AuthenticationException("User corresponding to this token does not exist.")
    
    doc["id"] = str(doc.pop("_id", doc.get("id")))
    user = UserModel.model_validate(doc)
    if not user.is_active:
        raise AuthorizationException("User account has been deactivated.")
    return user


class RoleChecker:
    """Enforces specific role requirements on endpoints."""

    def __init__(self, allowed_roles: List[UserRole]) -> None:
        self.allowed_roles = allowed_roles

    def __call__(self, user: UserModel = Depends(get_current_user)) -> UserModel:
        if user.role not in self.allowed_roles:
            raise AuthorizationException(
                f"User role '{user.role}' is not authorized to access this resource."
            )
        return user


require_admin = RoleChecker([UserRole.ADMIN, UserRole.SUPERADMIN])
require_superadmin = RoleChecker([UserRole.SUPERADMIN])
