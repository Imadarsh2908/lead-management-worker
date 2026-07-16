"""
app/models/user.py
--------------------
The User account model backing authentication and RBAC.

Revocation reuses BaseModel's existing soft-delete columns (is_deleted /
deleted_at / soft_delete()) rather than adding a parallel "is_active" flag —
"access revoked" and "soft-deleted" are the same state here: the account
still exists (for audit/history) but can no longer authenticate.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import String, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class User(BaseModel):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    # RBAC role: "Admin" | "Sales" | "Operator" (see app/api/dependencies.py RoleChecker).
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
