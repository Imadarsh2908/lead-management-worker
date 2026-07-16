"""
app/api/dependencies.py
------------------------
FastAPI dependency injection providers.

Using Depends() for DB sessions and JWT validation means:
  1. DB sessions are always properly closed (no connection leaks)
  2. Authentication is handled in ONE place — not copied into each endpoint
  3. Test overrides are clean: app.dependency_overrides[get_db] = mock_db
  4. Role checking is reusable across many endpoints (DRY principle)
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.user import User


# ─────────────────────────────────────────────────────────────
# DATABASE SESSION DEPENDENCY
# ─────────────────────────────────────────────────────────────

def get_db():
    """
    Yields a SQLAlchemy database session for the duration of an HTTP request.
    
    The try/finally block guarantees the session is ALWAYS closed — even if the
    endpoint raises an exception. This prevents connection pool exhaustion.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────
# JWT AUTHENTICATION
# ─────────────────────────────────────────────────────────────

# HTTPBearer automatically:
# - Requires the Authorization: Bearer <token> header
# - Shows a lock icon on the Swagger UI /docs page
# - Returns 403 if the header is missing (before our code even runs)
security = HTTPBearer()


def get_current_user_claims(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> dict:
    """
    Dependency: Decodes the JWT access token and returns the payload.

    Also checks the DB on every request so a revoked user is locked out
    IMMEDIATELY — not just after their (short-lived) access token naturally
    expires. This is a deliberate trade-off: one indexed lookup by username
    per authenticated request, in exchange for revocation actually meaning
    "revoked right now" rather than "revoked in up to 15 minutes".

    Raises 401 if:
      - Token is missing (handled by HTTPBearer above)
      - Token is expired
      - Token signature is invalid (tampered or wrong secret)
      - The user no longer exists or their access has been revoked
    """
    token = credentials.credentials
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY.get_secret_value(),
            algorithms=[settings.ALGORITHM],
        )
        # 'sub' (subject) claim is required — it holds the username
        username = payload.get("sub")
        if username is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing subject claim",
            )

        user = db.query(User).filter(User.username == username).first()
        if not user or user.is_deleted:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="This account's access has been revoked.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return payload

    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Could not validate credentials: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ─────────────────────────────────────────────────────────────
# ROLE-BASED ACCESS CONTROL (RBAC)
# ─────────────────────────────────────────────────────────────

class RoleChecker:
    """
    A callable class dependency that enforces Role-Based Access Control.
    
    Usage on an endpoint:
        @router.delete("/leads/{id}")
        def delete_lead(claims: dict = Depends(RoleChecker(["Admin"]))):
            ...

    Supported roles: "Admin", "Sales", "Operator"
    Role hierarchy (documented, not enforced automatically):
      Admin > Sales > Operator
    """

    def __init__(self, allowed_roles: list[str]):
        self.allowed_roles = allowed_roles

    def __call__(
        self,
        claims: dict = Depends(get_current_user_claims),
    ) -> dict:
        """
        Checks the 'role' claim in the JWT token against the allowed roles.
        Returns the full claims dict so the endpoint can access user info (e.g., user ID).
        """
        user_role = claims.get("role")
        if not user_role or user_role not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Access denied. This endpoint requires one of: "
                    f"{', '.join(self.allowed_roles)}. "
                    f"Your role: {user_role or 'unknown'}."
                ),
            )
        return claims


# ── Pre-built role checker instances (import these in routers) ──
# Using instances avoids re-instantiating the class on every request.
allow_admin_only = RoleChecker(["Admin"])
allow_sales_or_admin = RoleChecker(["Admin", "Sales"])
allow_all_roles = RoleChecker(["Admin", "Sales", "Operator"])
