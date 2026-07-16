"""
tests/test_users_api.py
-------------------------
Integration tests for the Admin-only user management API:
  GET   /v1/users/            — list every account (active or revoked)
  PATCH /v1/users/{id}/revoke — revoke access
  PATCH /v1/users/{id}/restore — restore access

The `client` fixture overrides allow_admin_only to always resolve to
{"sub": "admin_user", "role": "Admin"} — matching the seeded "admin_user"
demo account, so these tests exercise the real DB-backed endpoint logic
(self-revoke guard, last-admin guard, soft-delete semantics) against a real
user row.
"""
from app.core.security import get_password_hash
from app.models.user import User


def _get_by_username(db_session, username):
    return db_session.query(User).filter(User.username == username).first()


def test_create_user_success_grants_login(client, db_session):
    response = client.post(
        "/v1/users/", json={"username": "new_hire", "password": "correct-horse", "role": "Sales"}
    )
    assert response.status_code == 201
    data = response.json()
    assert data["username"] == "new_hire"
    assert data["role"] == "Sales"
    assert data["is_active"] is True

    login_response = client.post(
        "/v1/auth/login", json={"username": "new_hire", "password": "correct-horse"}
    )
    assert login_response.status_code == 200


def test_create_user_duplicate_username_conflict(client, db_session):
    response = client.post(
        "/v1/users/", json={"username": "admin_user", "password": "correct-horse", "role": "Sales"}
    )
    assert response.status_code == 409


def test_create_user_invalid_role_rejected(client, db_session):
    response = client.post(
        "/v1/users/", json={"username": "someone", "password": "correct-horse", "role": "Superuser"}
    )
    assert response.status_code == 422


def test_create_user_short_password_rejected(client, db_session):
    response = client.post(
        "/v1/users/", json={"username": "someone", "password": "short", "role": "Sales"}
    )
    assert response.status_code == 422


def test_list_users_returns_seeded_accounts(client, db_session):
    response = client.get("/v1/users/")
    assert response.status_code == 200
    data = response.json()
    usernames = {u["username"] for u in data}
    assert usernames == {"admin_user", "sales_user", "operator_user"}
    assert all(u["is_active"] for u in data)


def test_revoke_user_success_blocks_login(client, db_session):
    target = _get_by_username(db_session, "sales_user")

    response = client.patch(f"/v1/users/{target.id}/revoke")
    assert response.status_code == 200
    data = response.json()
    assert data["is_active"] is False

    # The revoked account can no longer authenticate.
    login_response = client.post(
        "/v1/auth/login", json={"username": "sales_user", "password": "password123"}
    )
    assert login_response.status_code == 401


def test_revoke_own_access_blocked(client, db_session):
    self_user = _get_by_username(db_session, "admin_user")
    response = client.patch(f"/v1/users/{self_user.id}/revoke")
    assert response.status_code == 400
    assert "cannot revoke your own access" in response.json()["detail"].lower()


def test_revoke_last_active_admin_blocked(client, db_session):
    # Simulate a state with exactly one active Admin: "second_admin".
    # (admin_user is soft-deleted directly here purely to isolate the
    # last-admin counting logic from the separate self-revoke guard.)
    admin_user = _get_by_username(db_session, "admin_user")
    admin_user.soft_delete()
    second_admin = User(username="second_admin", hashed_password=get_password_hash("x"), role="Admin")
    db_session.add(second_admin)
    db_session.commit()
    db_session.refresh(second_admin)

    response = client.patch(f"/v1/users/{second_admin.id}/revoke")
    assert response.status_code == 400
    assert "last remaining active admin" in response.json()["detail"].lower()


def test_restore_user_success(client, db_session):
    target = _get_by_username(db_session, "operator_user")
    client.patch(f"/v1/users/{target.id}/revoke")

    response = client.patch(f"/v1/users/{target.id}/restore")
    assert response.status_code == 200
    data = response.json()
    assert data["is_active"] is True

    login_response = client.post(
        "/v1/auth/login", json={"username": "operator_user", "password": "password123"}
    )
    assert login_response.status_code == 200


def test_revoke_nonexistent_user_404(client, db_session):
    import uuid
    response = client.patch(f"/v1/users/{uuid.uuid4()}/revoke")
    assert response.status_code == 404
