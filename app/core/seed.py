"""
app/core/seed.py
------------------
One-time idempotent seeding of the demo user accounts. Runs on every startup
(app/main.py lifespan) but only inserts rows the first time — safe to call
repeatedly. This keeps the zero-dependency demo path (SQLite, no Alembic)
working out of the box, matching how create_all_tables() already behaves.
"""
from loguru import logger
from sqlalchemy.orm import Session

from app.core.security import get_password_hash
from app.models.user import User

DEMO_ACCOUNTS = [
    ("admin_user", "Admin"),
    ("sales_user", "Sales"),
    ("operator_user", "Operator"),
]
DEMO_PASSWORD = "password123"


def seed_demo_users(db: Session) -> None:
    """Inserts the three demo accounts if the users table is empty."""
    if db.query(User).count() > 0:
        return

    hashed = get_password_hash(DEMO_PASSWORD)
    for username, role in DEMO_ACCOUNTS:
        db.add(User(username=username, hashed_password=hashed, role=role))
    db.commit()
    logger.info(f"Seeded {len(DEMO_ACCOUNTS)} demo user accounts.")
