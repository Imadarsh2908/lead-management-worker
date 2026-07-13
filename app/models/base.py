"""
app/models/base.py
-------------------
Abstract base SQLAlchemy model providing:
  - UUID primary key (avoids sequential integer ID enumeration attacks)
  - Automatic timestamps (created_at, updated_at)
  - Soft Delete (is_deleted, deleted_at) — records are never physically removed,
    allowing audit trails and potential data recovery.

All domain models (Lead, WorkflowState, AuditLog) inherit from BaseModel.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, func, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BaseModel(Base):
    """
    Abstract base class — not a real table, just shared column definitions.
    SQLAlchemy uses __abstract__ = True to prevent creating a 'basemodel' table.
    """
    __abstract__ = True

    # UUID primary key — globally unique, avoids predictable integer sequences
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
        index=True,
        comment="Globally unique identifier",
    )

    # Automatically set to NOW when the record is first created
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        comment="Record creation timestamp (UTC)",
    )

    # Automatically updated to NOW whenever the record is modified
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
        comment="Last modification timestamp (UTC)",
    )

    # ── Soft Delete ────────────────────────────────────────
    # Instead of DELETE FROM ... we set is_deleted=True.
    # All queries should filter WHERE is_deleted = FALSE.
    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        index=True,
        comment="Soft delete flag — True means the record is logically deleted",
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp when the record was soft-deleted",
    )

    def soft_delete(self) -> None:
        """Marks this record as deleted without removing it from the database."""
        self.is_deleted = True
        self.deleted_at = datetime.now(timezone.utc)
