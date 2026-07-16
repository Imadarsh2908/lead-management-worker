import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_current_user_claims, RoleChecker
from app.core.security import create_access_token, get_password_hash
from app.models.user import User


def test_get_db():
    db_gen = get_db()
    db = next(db_gen)
    assert isinstance(db, Session)
    # Trigger finalization/close
    try:
        next(db_gen)
    except StopIteration:
        pass


def test_get_current_user_claims_success(db_session):
    db_session.add(User(username="user1", hashed_password=get_password_hash("x"), role="Admin"))
    db_session.commit()

    # Create a valid token
    token = create_access_token(subject="user1", role="Admin")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    claims = get_current_user_claims(creds, db=db_session)
    assert claims["sub"] == "user1"
    assert claims["role"] == "Admin"


def test_get_current_user_claims_revoked_user_denied(db_session):
    """A soft-deleted (revoked) user must be rejected even with a still-valid token."""
    user = User(username="revoked1", hashed_password=get_password_hash("x"), role="Admin")
    user.soft_delete()
    db_session.add(user)
    db_session.commit()

    token = create_access_token(subject="revoked1", role="Admin")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with pytest.raises(HTTPException) as exc:
        get_current_user_claims(creds, db=db_session)
    assert exc.value.status_code == 401
    assert "revoked" in exc.value.detail.lower()


def test_get_current_user_claims_unknown_user_denied(db_session):
    """A token for a username that doesn't exist in the DB must be rejected."""
    token = create_access_token(subject="ghost_user", role="Admin")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with pytest.raises(HTTPException) as exc:
        get_current_user_claims(creds, db=db_session)
    assert exc.value.status_code == 401


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
