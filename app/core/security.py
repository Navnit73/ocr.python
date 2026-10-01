"""
Security, Password Hashing, and JWT Token Management.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
import jwt
import bcrypt

from app.core.config import get_settings
from app.core.exceptions import AuthenticationException


def hash_password(password: str) -> str:
    """Hashes a plain password using bcrypt."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plain password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8")
        )
    except Exception:
        return False


def create_jwt_token(
    subject: str,
    token_type: str = "access",
    expires_delta: Optional[timedelta] = None,
    additional_claims: Optional[Dict[str, Any]] = None,
) -> str:
    """Generates an encrypted JWT access or refresh token."""
    settings = get_settings()
    now = datetime.now(timezone.utc)

    if expires_delta:
        expire = now + expires_delta
    elif token_type == "access":
        expire = now + timedelta(minutes=settings.security.access_token_expire_minutes)
    else:  # refresh
        expire = now + timedelta(days=settings.security.refresh_token_expire_days)

    payload: Dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": expire,
        "nbf": now,
    }

    if additional_claims:
        payload.update(additional_claims)

    encoded_jwt = jwt.encode(
        payload,
        settings.security.jwt_secret_key,
        algorithm=settings.security.jwt_algorithm,
    )
    return encoded_jwt


def decode_jwt_token(token: str) -> Dict[str, Any]:
    """Decodes and validates a JWT token."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.security.jwt_secret_key,
            algorithms=[settings.security.jwt_algorithm],
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise AuthenticationException("Token has expired")
    except jwt.InvalidTokenError:
        raise AuthenticationException("Invalid token signature or payload")
