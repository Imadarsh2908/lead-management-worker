"""
app/api/v1/auth.py
-------------------
Authentication endpoints:
  POST /v1/auth/login   — accepts username/password, returns access + refresh tokens
  POST /v1/auth/refresh — accepts a valid refresh token, returns a new access token

Demo users (hardcoded for evaluation — replace with DB users in production):
  admin_user / password123 → role: Admin
  sales_user / password123 → role: Sales
  operator_user / password123 → role: Operator
"""
from fastapi import APIRouter, HTTPException, status
from jose import jwt, JWTError

from app.core.config import settings
from app.core.security import create_access_token, create_refresh_token
from app.schemas.lead import LoginRequest, RefreshTokenRequest, TokenResponse


router = APIRouter(prefix="/v1/auth", tags=["Authentication"])


# ─────────────────────────────────────────────────────────────
# DEMO USER STORE
# In production: query the users table via get_db() dependency
# and use verify_password(plain, hashed) from core/security.py
# ─────────────────────────────────────────────────────────────

# Password hash for "password123" using bcrypt
_HASHED_PASSWORD = "$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW"

DEMO_USERS = {
    "admin_user":    {"hashed_password": _HASHED_PASSWORD, "role": "Admin"},
    "sales_user":    {"hashed_password": _HASHED_PASSWORD, "role": "Sales"},
    "operator_user": {"hashed_password": _HASHED_PASSWORD, "role": "Operator"},
}


# ─────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────

@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login and get access + refresh tokens",
    responses={
        401: {"description": "Invalid username or password"},
    },
)
def login(payload: LoginRequest):
    """
    Authenticates a user and returns a short-lived access token (15 min)
    and a long-lived refresh token (7 days).
    
    The access token includes the user's role — used by RoleChecker on protected endpoints.
    The refresh token does NOT include the role (re-fetched from DB on refresh for security).
    """
    user = DEMO_USERS.get(payload.username)

    # IMPORTANT: In production, use verify_password() from core/security.py
    # to compare against the bcrypt hash. Hardcoded check here is for demo only.
    if not user or payload.password != "password123":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
        )

    access_token = create_access_token(
        subject=payload.username,
        role=user["role"],
    )
    refresh_token = create_refresh_token(subject=payload.username)

    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Use a refresh token to get a new access token",
    responses={
        401: {"description": "Invalid or expired refresh token"},
    },
)
def refresh(payload: RefreshTokenRequest):
    """
    Validates the refresh token and issues a brand-new access token.
    
    Security notes:
    - Uses the REFRESH_SECRET_KEY (different from ACCESS SECRET_KEY).
    - Re-fetches the user's role from the DB to reflect any role changes since login.
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
    user = DEMO_USERS.get(username)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User associated with this token no longer exists.",
        )

    # Issue a new access token with the CURRENT role (may have changed since original login)
    new_access_token = create_access_token(subject=username, role=user["role"])

    return TokenResponse(
        access_token=new_access_token,
        refresh_token=payload.refresh_token,  # Return same refresh token (add rotation in prod)
    )
