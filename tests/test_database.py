from unittest.mock import patch
from app.core.database import create_all_tables
from tests.conftest import engine as test_engine


def test_create_all_tables():
    # Patch the PostgreSQL engine with the test SQLite engine to cover create_all_tables
    with patch("app.core.database.engine", test_engine):
        create_all_tables()
