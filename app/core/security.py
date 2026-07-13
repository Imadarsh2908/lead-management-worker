"""
app/core/security.py
---------------------
Handles all cryptographic operations:
  - Password hashing using bcrypt (via passlib)
  - JWT Access Token creation (short-lived: 15 minutes)
  - JWT Refresh Token creation (long-lived: 7 days)

IMPORTANT: Access and Refresh tokens use DIFFERENT secret keys.
This means a stolen Refresh token cannot be used to forge an Access token
if the attacker doesn't know the second secret.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional, Union, Any

import bcrypt
from jose import jwt

from app.core.config import settings


# ── Password Hashing ───────────────────────────────────────
# bcrypt is the industry standard — it's deliberately slow to resist brute-force.


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Compares a plaintext password against a stored bcrypt hash."""
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False


def get_password_hash(password: str) -> str:
    """Hashes a plaintext password using bcrypt. Store this hash — never the plain text."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


# ── Token Creation ─────────────────────────────────────────

def create_access_token(
    subject: Union[str, Any],
    role: str,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    Creates a short-lived JWT Access Token.
    
    The 'sub' (subject) claim = user ID or username.
    The 'role' claim = the user's RBAC role (Admin, Sales, Operator).
    The 'exp' claim = expiration timestamp.
    """
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload = {
        "exp": expire,
        "sub": str(subject),
        "role": role,
        "type": "access",
    }
    # Use .get_secret_value() to extract the plain string from SecretStr
    return jwt.encode(payload, settings.SECRET_KEY.get_secret_value(), algorithm=settings.ALGORITHM)


def create_refresh_token(
    subject: Union[str, Any],
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    Creates a long-lived JWT Refresh Token.
    
    Deliberately does NOT include 'role' — the role is always re-fetched from
    the database on the /refresh endpoint so revoked roles take effect immediately.
    Uses a SEPARATE secret key from the access token.
    """
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    )
    payload = {
        "exp": expire,
        "sub": str(subject),
        "type": "refresh",
    }
    return jwt.encode(payload, settings.REFRESH_SECRET_KEY.get_secret_value(), algorithm=settings.ALGORITHM)
