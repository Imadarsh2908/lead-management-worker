"""
app/api/v1/auth.py
-------------------
Authentication endpoints:
  POST /v1/auth/login   — accepts username/password, returns access + refresh tokens
  POST /v1/auth/refresh — accepts a valid refresh token, returns a new access token

Users are DB-backed (app/models/user.py). The three demo accounts
(admin_user / sales_user / operator_user, all password "password123") are
seeded automatically on startup — see app/core/seed.py.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from jose import jwt, JWTError
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.core.config import settings
from app.core.security import create_access_token, create_refresh_token, verify_password
from app.models.user import User
from app.schemas.lead import LoginRequest, RefreshTokenRequest, TokenResponse

router = APIRouter(prefix="/v1/auth", tags=["Authentication"])


# ─────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────

@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login and get access + refresh tokens",
    responses={
        401: {"description": "Invalid username or password, or access has been revoked"},
    },
)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    """
    Authenticates a user and returns a short-lived access token (15 min)
    and a long-lived refresh token (7 days).

    The access token includes the user's role — used by RoleChecker on protected endpoints.
    The refresh token does NOT include the role (re-fetched from DB on refresh for security).
    """
    user = db.query(User).filter(User.username == payload.username).first()

    if not user or user.is_deleted or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password, or this account's access has been revoked.",
        )

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()

    access_token = create_access_token(subject=user.username, role=user.role)
    refresh_token = create_refresh_token(subject=user.username)

    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Use a refresh token to get a new access token",
    responses={
        401: {"description": "Invalid/expired refresh token, or access has been revoked"},
    },
)
def refresh(payload: RefreshTokenRequest, db: Session = Depends(get_db)):
    """
    Validates the refresh token and issues a brand-new access token.

    Security notes:
    - Uses the REFRESH_SECRET_KEY (different from ACCESS SECRET_KEY).
    - Re-fetches the user's role AND revocation status from the DB, so a
      revoked account can no longer mint new access tokens even with a
      still-valid refresh token.
    - The same refresh token is returned (no rotation for simplicity — add rotation in prod).
    """
    try:
        token_data = jwt.decode(
            payload.refresh_token,
            settings.REFRESH_SECRET_KEY.get_secret_value(),
            algorithms=[settings.ALGORITHM],
        )
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired refresh token: {str(e)}",
        )

    username = token_data.get("sub")
    user = db.query(User).filter(User.username == username).first()

    if not user or user.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User associated with this token no longer exists or has been revoked.",
        )

    # Issue a new access token with the CURRENT role (may have changed since original login)
    new_access_token = create_access_token(subject=username, role=user.role)

    return TokenResponse(
        access_token=new_access_token,
        refresh_token=payload.refresh_token,  # Return same refresh token (add rotation in prod)
    )
