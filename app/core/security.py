"""
API Key Security & Authentication Layer with Constant-Time Comparison and OpenAPI Integration.
"""

import hashlib
import secrets
from typing import Optional
from fastapi import Header, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader, HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from app.core.config import get_settings

# Swagger UI Security Schemes
api_key_header_scheme = APIKeyHeader(name="X-API-Key", auto_error=False, description="API Key via X-API-Key Header")
http_bearer_scheme = HTTPBearer(auto_error=False, description="API Key via Bearer Token Header")


class AuthenticatedClient(BaseModel):
    """Represents an authenticated API caller with scoped key hash."""
    api_key_masked: str
    key_hash: str
    client_ip: Optional[str] = None


def hash_key(key: str) -> str:
    """Computes a SHA-256 hash of the API key for safe scoped lookup without storing raw keys."""
    return hashlib.sha256(key.strip().encode("utf-8")).hexdigest()


async def verify_api_key(
    request: Request,
    header_api_key: Optional[str] = Security(api_key_header_scheme),
    bearer_credentials: Optional[HTTPAuthorizationCredentials] = Security(http_bearer_scheme),
    custom_x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> AuthenticatedClient:
    """
    Validates API key provided in X-API-Key header or Authorization: Bearer token.
    Throws 401 Unauthorized if invalid or missing.
    """
    settings = get_settings()

    # Extract provided key from header or bearer token
    provided_key: Optional[str] = None
    if header_api_key and header_api_key.strip():
        provided_key = header_api_key.strip()
    elif bearer_credentials and bearer_credentials.credentials.strip():
        provided_key = bearer_credentials.credentials.strip()
    elif custom_x_api_key and custom_x_api_key.strip():
        provided_key = custom_x_api_key.strip()

    req_id = request.headers.get("X-Request-ID", "req_auth")

    # If API key requirement is disabled in settings, allow with anonymous hash
    if not settings.require_api_key:
        client_ip = request.client.host if request.client else "127.0.0.1"
        return AuthenticatedClient(
            api_key_masked="NO_AUTH_REQUIRED",
            key_hash=hash_key(client_ip),
            client_ip=client_ip,
        )

    if not provided_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API Key. Provide a valid key in 'X-API-Key' header or 'Authorization: Bearer <key>'.",
            headers={"WWW-Authenticate": "ApiKey, Bearer"},
        )

    # Constant-time comparison against configured API keys to prevent timing attacks
    valid = False
    for expected_key in settings.api_keys:
        if secrets.compare_digest(provided_key, expected_key.strip()):
            valid = True
            break

    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API Key. Access denied.",
            headers={"WWW-Authenticate": "ApiKey, Bearer"},
        )

    client_ip = request.client.host if request.client else "127.0.0.1"
    masked = f"{provided_key[:4]}...{provided_key[-4:]}" if len(provided_key) >= 8 else "***"

    return AuthenticatedClient(
        api_key_masked=masked,
        key_hash=hash_key(provided_key),
        client_ip=client_ip,
    )


async def verify_admin_key(
    request: Request,
    header_api_key: Optional[str] = Security(api_key_header_scheme),
    bearer_credentials: Optional[HTTPAuthorizationCredentials] = Security(http_bearer_scheme),
    custom_x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> AuthenticatedClient:
    """
    Validates that the provided API key belongs to the authorized admin keys list.
    Throws 403 Forbidden if the key is valid as normal API key but not admin, or 401 if invalid.
    """
    settings = get_settings()

    provided_key: Optional[str] = None
    if header_api_key and header_api_key.strip():
        provided_key = header_api_key.strip()
    elif bearer_credentials and bearer_credentials.credentials.strip():
        provided_key = bearer_credentials.credentials.strip()
    elif custom_x_api_key and custom_x_api_key.strip():
        provided_key = custom_x_api_key.strip()

    if not provided_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Admin API Key. Provide a valid admin key in 'X-API-Key' header.",
            headers={"WWW-Authenticate": "ApiKey, Bearer"},
        )

    # Check against admin API keys
    is_admin = False
    for admin_key in settings.admin_api_keys:
        if secrets.compare_digest(provided_key, admin_key.strip()):
            is_admin = True
            break

    if not is_admin:
        # Check if it's at least a valid user key to provide a 403 Forbidden instead of 401
        is_user = any(secrets.compare_digest(provided_key, k.strip()) for k in settings.api_keys)
        if is_user:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin privileges required to access this resource.",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Admin API Key.",
            headers={"WWW-Authenticate": "ApiKey, Bearer"},
        )

    client_ip = request.client.host if request.client else "127.0.0.1"
    masked = f"{provided_key[:4]}...{provided_key[-4:]}" if len(provided_key) >= 8 else "***"

    return AuthenticatedClient(
        api_key_masked=masked,
        key_hash=hash_key(provided_key),
        client_ip=client_ip,
    )

