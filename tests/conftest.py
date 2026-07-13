"""
tests/conftest.py
------------------
Pytest shared fixtures. Loaded automatically by Pytest before any test runs.

Key fixtures provided:
  - db_session: fresh SQLite in-memory DB for each test (fast, isolated)
  - client: FastAPI TestClient with mocked auth and DB dependencies
"""
import os
os.environ["ENVIRONMENT"] = "testing"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.api.dependencies import get_db, allow_all_roles, allow_sales_or_admin, allow_admin_only
from app.core.database import Base

# Use SQLite in-memory database for tests — no Postgres needed!
SQLALCHEMY_TEST_URL = "sqlite:///./test.db"

engine = create_engine(
    SQLALCHEMY_TEST_URL,
    connect_args={"check_same_thread": False},  # Required for SQLite with threading
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="function", autouse=True)
def db_session():
    """Creates fresh database tables for each test, then drops them after."""
    # Import all models so Base.metadata is populated
    from app.models import lead  # noqa: F401
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="module")
def client():
    """
    FastAPI TestClient with dependency overrides:
      - DB uses SQLite (not Postgres)
      - Auth always returns a fake 'Sales' user (no real JWT needed in tests)
    """
    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    def override_auth():
        """Simulates a logged-in Sales user for all protected endpoints."""
        return {"sub": "test_user", "role": "Sales"}

    def override_admin_auth():
        """Simulates a logged-in Admin user for Admin-only endpoints."""
        return {"sub": "admin_user", "role": "Admin"}

    # Override both DB and all role checkers
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[allow_all_roles] = override_auth
    app.dependency_overrides[allow_sales_or_admin] = override_auth
    app.dependency_overrides[allow_admin_only] = override_admin_auth

    with TestClient(app) as c:
        yield c

    # Clean up overrides after the module finishes
    app.dependency_overrides.clear()
