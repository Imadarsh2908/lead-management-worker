import pytest

def test_login_success(client):
    response = client.post(
        "/v1/auth/login",
        json={"username": "admin_user", "password": "password123"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


def test_login_invalid_credentials(client):
    response = client.post(
        "/v1/auth/login",
        json={"username": "admin_user", "password": "wrong_password"}
    )
    assert response.status_code == 401
    assert "Incorrect username or password" in response.json()["detail"]


def test_refresh_token_success(client):
    # First login to get a refresh token
    login_response = client.post(
        "/v1/auth/login",
        json={"username": "sales_user", "password": "password123"}
    )
    refresh_token = login_response.json()["refresh_token"]

    # Use the refresh token
    refresh_response = client.post(
        "/v1/auth/refresh",
        json={"refresh_token": refresh_token}
    )
    assert refresh_response.status_code == 200
    data = refresh_response.json()
    assert "access_token" in data
    assert data["refresh_token"] == refresh_token


def test_refresh_token_invalid(client):
    response = client.post(
        "/v1/auth/refresh",
        json={"refresh_token": "invalid.jwt.token"}
    )
    assert response.status_code == 401
    assert "Invalid or expired refresh token" in response.json()["detail"]


def test_refresh_token_nonexistent_user(client):
    # We forge a JWT refresh token with a non-existent user
    from jose import jwt
    from app.core.config import settings
    from datetime import datetime, timedelta, timezone

    expire = datetime.now(timezone.utc) + timedelta(days=1)
    to_encode = {"exp": expire, "sub": "nonexistent_user"}
    forged_token = jwt.encode(
        to_encode,
        settings.REFRESH_SECRET_KEY.get_secret_value(),
        algorithm=settings.ALGORITHM
    )

    response = client.post(
        "/v1/auth/refresh",
        json={"refresh_token": forged_token}
    )
    assert response.status_code == 401
    assert "User associated with this token no longer exists" in response.json()["detail"]
