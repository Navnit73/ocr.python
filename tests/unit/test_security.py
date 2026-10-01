"""
Unit tests for Security & JWT functions.
"""

from datetime import timedelta
import pytest
from app.core.exceptions import AuthenticationException
from app.core.security import (
    create_jwt_token,
    decode_jwt_token,
    hash_password,
    verify_password,
)


def test_password_hashing_and_verification():
    """Tests bcrypt password hashing and checking."""
    password = "SuperSecretPassword123!"
    hashed = hash_password(password)

    assert hashed != password
    assert verify_password(password, hashed) is True
    assert verify_password("WrongPassword", hashed) is False


def test_jwt_token_generation_and_decoding():
    """Tests JWT creation, payload validation, and custom claims."""
    user_id = "user_67890"
    token = create_jwt_token(
        subject=user_id,
        token_type="access",
        additional_claims={"role": "admin", "email": "admin@example.com"},
    )
    assert isinstance(token, str)

    payload = decode_jwt_token(token)
    assert payload["sub"] == user_id
    assert payload["type"] == "access"
    assert payload["role"] == "admin"
    assert payload["email"] == "admin@example.com"


def test_expired_jwt_token_raises_exception():
    """Tests that expired JWT tokens raise an AuthenticationException."""
    token = create_jwt_token(
        subject="expired_user",
        expires_delta=timedelta(seconds=-10),  # expired in the past
    )
    with pytest.raises(AuthenticationException):
        decode_jwt_token(token)
