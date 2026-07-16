"""
app/api/v1/users.py
---------------------
Admin-only user management: see who has access to the app, and revoke or
restore that access.

Revocation reuses the User model's soft-delete columns (is_deleted /
deleted_at) — see app/models/user.py. Combined with the DB check added to
get_current_user_claims (app/api/dependencies.py), a revoked user is locked
out on their VERY NEXT request, not just once their current access token
naturally expires.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.dependencies import allow_admin_only, get_db
from app.core.security import get_password_hash
from app.models.user import User
from app.schemas.user import UserCreateRequest, UserResponse
from app.utils.audit import log_user_access_restored, log_user_access_revoked, log_user_created

router = APIRouter(prefix="/v1/users", tags=["User Management"])


def _to_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        is_active=not user.is_deleted,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


@router.post(
    "/",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user account and grant them access",
    responses={409: {"description": "Username already taken"}},
)
def create_user(
    payload: UserCreateRequest,
    claims: dict = Depends(allow_admin_only),
    db: Session = Depends(get_db),
):
    existing = db.query(User).filter(User.username == payload.username).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{payload.username}' is already taken.",
        )

    new_user = User(
        username=payload.username,
        hashed_password=get_password_hash(payload.password),
        role=payload.role,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    log_user_created(admin_username=claims["sub"], new_username=new_user.username, role=new_user.role)
    return _to_response(new_user)


@router.get(
    "/",
    response_model=list[UserResponse],
    summary="List every user account and whether their access is active or revoked",
)
def list_users(
    claims: dict = Depends(allow_admin_only),
    db: Session = Depends(get_db),
):
    users = db.query(User).order_by(User.created_at.asc()).all()
    return [_to_response(u) for u in users]


@router.patch(
    "/{user_id}/revoke",
    response_model=UserResponse,
    summary="Revoke a user's access — they are logged out on their next request",
    responses={
        400: {"description": "Cannot revoke your own access, or the last active Admin"},
        404: {"description": "User not found"},
    },
)
def revoke_user(
    user_id: uuid.UUID,
    claims: dict = Depends(allow_admin_only),
    db: Session = Depends(get_db),
):
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    if target.username == claims.get("sub"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot revoke your own access.",
        )

    if target.role == "Admin" and not target.is_deleted:
        remaining_admins = (
            db.query(User)
            .filter(User.role == "Admin", User.is_deleted == False)  # noqa: E712
            .count()
        )
        if remaining_admins <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot revoke the last remaining active Admin.",
            )

    target.soft_delete()
    db.commit()
    db.refresh(target)
    log_user_access_revoked(admin_username=claims["sub"], target_username=target.username)
    return _to_response(target)


@router.patch(
    "/{user_id}/restore",
    response_model=UserResponse,
    summary="Restore a previously revoked user's access",
    responses={404: {"description": "User not found"}},
)
def restore_user(
    user_id: uuid.UUID,
    claims: dict = Depends(allow_admin_only),
    db: Session = Depends(get_db),
):
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    target.is_deleted = False
    target.deleted_at = None
    db.commit()
    db.refresh(target)
    log_user_access_restored(admin_username=claims["sub"], target_username=target.username)
    return _to_response(target)
