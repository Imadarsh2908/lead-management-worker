from datetime import timedelta
from jose import jwt

from app.core.security import (
    get_password_hash,
    verify_password,
    create_access_token,
    create_refresh_token,
)
from app.core.config import settings


def test_password_hashing():
    password = "MySuperSecretPassword123"
    hashed = get_password_hash(password)
    assert hashed != password
    assert verify_password(password, hashed) is True
    assert verify_password("WrongPassword", hashed) is False


def test_tokens_with_custom_expiry():
    subject = "user123"
    role = "Sales"
    
    custom_delta = timedelta(minutes=5)
    access_token = create_access_token(subject, role, expires_delta=custom_delta)
    
    # Decode and verify
    payload = jwt.decode(
        access_token,
        settings.SECRET_KEY.get_secret_value(),
        algorithms=[settings.ALGORITHM]
    )
    assert payload["sub"] == subject
    assert payload["role"] == role
    assert payload["type"] == "access"

    refresh_token = create_refresh_token(subject, expires_delta=custom_delta)
    payload_refresh = jwt.decode(
        refresh_token,
        settings.REFRESH_SECRET_KEY.get_secret_value(),
        algorithms=[settings.ALGORITHM]
    )
    assert payload_refresh["sub"] == subject
    assert payload_refresh["type"] == "refresh"
