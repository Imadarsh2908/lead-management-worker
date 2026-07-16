"""
app/schemas/user.py
--------------------
Pydantic schemas for the Admin-only user management API (create, list users,
revoke / restore access). Kept separate from schemas/lead.py — a different domain.
"""
import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class UserCreateRequest(BaseModel):
    """Payload for POST /v1/users/ — grants a new person access."""
    username: str = Field(..., min_length=3, max_length=100, description="Must be unique.")
    password: str = Field(..., min_length=8, max_length=128)
    role: Literal["Admin", "Sales", "Operator"]


class UserResponse(BaseModel):
    """Outbound view of a user account. Never includes hashed_password."""
    id: uuid.UUID
    username: str
    role: str
    is_active: bool
    last_login_at: Optional[datetime] = None
    created_at: datetime
