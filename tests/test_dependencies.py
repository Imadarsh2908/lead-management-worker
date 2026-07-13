import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_current_user_claims, RoleChecker
from app.core.security import create_access_token


def test_get_db():
    db_gen = get_db()
    db = next(db_gen)
    assert isinstance(db, Session)
    # Trigger finalization/close
    try:
        next(db_gen)
    except StopIteration:
        pass


def test_get_current_user_claims_success():
    # Create a valid token
    token = create_access_token(subject="user1", role="Admin")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    
    claims = get_current_user_claims(creds)
    assert claims["sub"] == "user1"
    assert claims["role"] == "Admin"


def test_get_current_user_claims_missing_sub():
    from jose import jwt
    from app.core.config import settings
    from datetime import datetime, timezone, timedelta

    expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    # Token missing 'sub'
    to_encode = {"exp": expire, "role": "Admin"}
    token = jwt.encode(to_encode, settings.SECRET_KEY.get_secret_value(), algorithm=settings.ALGORITHM)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with pytest.raises(HTTPException) as exc:
        get_current_user_claims(creds)
    assert exc.value.status_code == 401
    assert "missing subject claim" in exc.value.detail


def test_get_current_user_claims_invalid_token():
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="invalid-token-string")
    with pytest.raises(HTTPException) as exc:
        get_current_user_claims(creds)
    assert exc.value.status_code == 401
    assert "Could not validate credentials" in exc.value.detail


def test_role_checker_success():
    checker = RoleChecker(["Admin", "Sales"])
    claims = {"sub": "admin1", "role": "Admin"}
    result = checker(claims)
    assert result == claims


def test_role_checker_denied():
    checker = RoleChecker(["Admin"])
    claims = {"sub": "sales1", "role": "Sales"}
    with pytest.raises(HTTPException) as exc:
        checker(claims)
    assert exc.value.status_code == 403
    assert "Access denied" in exc.value.detail


def test_role_checker_missing_role():
    checker = RoleChecker(["Admin"])
    claims = {"sub": "sales1"}
    with pytest.raises(HTTPException) as exc:
        checker(claims)
    assert exc.value.status_code == 403
    assert "Your role: unknown" in exc.value.detail
