"""
database package
----------------
SQLAlchemy engine, session factory, and startup schema/seed helpers.
Uses local PostgreSQL by default (user postgres / password root).
"""

import logging
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base

load_dotenv(override=True)

logger = logging.getLogger(__name__)

SQLALCHEMY_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:root@localhost:5432/googlereviews",
)

connect_args = {"check_same_thread": False} if SQLALCHEMY_DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args=connect_args)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ensure_index(table: str, name: str, ddl: str) -> None:
    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        return
    existing = {idx["name"] for idx in inspector.get_indexes(table)}
    if name in existing:
        return
    with engine.begin() as conn:
        conn.execute(text(ddl))
    logger.info("Added index %s on %s", name, table)


def _ensure_column(table: str, column: str, ddl: str) -> None:
    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns(table)}
    if column in existing:
        return
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))
    logger.info("Added column %s.%s", table, column)


def init_db():
    """Create tables and seed default users/businesses if the database is empty."""
    from models.domain_models import (  # noqa: F401
        Booking,
        BusinessConfigModel,
        Customer,
        SalesExecutive,
        User,
        WalletLedger,
        WalletWithdrawal,
    )
    from services.auth_service import seed_default_users
    from services.business_service import (
        distribute_businesses_to_sales,
        ensure_feedback_routes,
        seed_initial_businesses,
    )
    from services.plan_service import backfill_booking_wallet_credits
    from services.sales_service import align_sales_book_to_plans, ensure_sales_users, seed_sales_data

    Base.metadata.create_all(bind=engine)
    _ensure_column("businesses", "plan_code", "plan_code VARCHAR DEFAULT ''")
    _ensure_column("businesses", "plan_amount", "plan_amount FLOAT DEFAULT 0")
    _ensure_column("businesses", "join_date", "join_date DATE")
    _ensure_column("businesses", "expiry_date", "expiry_date DATE")
    _ensure_column("businesses", "sales_executive_id", "sales_executive_id INTEGER")
    _ensure_column("businesses", "mobile", "mobile VARCHAR DEFAULT ''")
    _ensure_column("businesses", "alternate_mobile", "alternate_mobile VARCHAR DEFAULT ''")
    _ensure_column("businesses", "email", "email VARCHAR DEFAULT ''")
    _ensure_column("businesses", "address", "address TEXT DEFAULT ''")
    _ensure_column("sales_executives", "wallet_balance", "wallet_balance FLOAT DEFAULT 0")
    _ensure_column("sales_executives", "bank_name", "bank_name VARCHAR DEFAULT ''")
    _ensure_column("sales_executives", "account_name", "account_name VARCHAR DEFAULT ''")
    _ensure_column("sales_executives", "account_number", "account_number VARCHAR DEFAULT ''")
    _ensure_column("sales_executives", "ifsc", "ifsc VARCHAR DEFAULT ''")
    _ensure_column("users", "full_name", "full_name VARCHAR DEFAULT ''")
    _ensure_column("users", "phone", "phone VARCHAR DEFAULT ''")
    _ensure_column("users", "alternate_mobile", "alternate_mobile VARCHAR DEFAULT ''")
    _ensure_column("users", "email", "email VARCHAR DEFAULT ''")
    _ensure_column("users", "address", "address TEXT DEFAULT ''")
    _ensure_column("users", "aadhaar", "aadhaar VARCHAR DEFAULT ''")
    _ensure_column("users", "bank_name", "bank_name VARCHAR DEFAULT ''")
    _ensure_column("users", "account_name", "account_name VARCHAR DEFAULT ''")
    _ensure_column("users", "account_number", "account_number VARCHAR DEFAULT ''")
    _ensure_column("users", "ifsc", "ifsc VARCHAR DEFAULT ''")
    _ensure_column("wallet_ledger", "booking_id", "booking_id INTEGER")
    _ensure_column("wallet_withdrawals", "requested_by_user_id", "requested_by_user_id INTEGER")
    _ensure_column("wallet_withdrawals", "processed_by_user_id", "processed_by_user_id INTEGER")
    _ensure_column("wallet_withdrawals", "processed_at", "processed_at TIMESTAMP")
    _ensure_index(
        "businesses",
        "ix_businesses_name",
        "CREATE INDEX IF NOT EXISTS ix_businesses_name ON businesses (name)",
    )
    _ensure_index(
        "businesses",
        "ix_businesses_id",
        "CREATE INDEX IF NOT EXISTS ix_businesses_id ON businesses (id)",
    )
    _ensure_index(
        "businesses",
        "ix_businesses_route_slug",
        "CREATE INDEX IF NOT EXISTS ix_businesses_route_slug ON businesses (route_slug)",
    )
    _ensure_index(
        "businesses",
        "ix_businesses_collection",
        "CREATE INDEX IF NOT EXISTS ix_businesses_collection ON businesses (collection)",
    )
    with engine.begin() as conn:
        conn.execute(text("UPDATE sales_executives SET wallet_balance = 0 WHERE wallet_balance IS NULL"))
    logger.info("Database tables ready (%s)", SQLALCHEMY_DATABASE_URL.split("@")[-1])

    db = SessionLocal()
    try:
        seed_default_users(db)
        seed_initial_businesses(db)
        seed_sales_data(db)
        ensure_sales_users(db)
        ensure_feedback_routes(db)
        distribute_businesses_to_sales(db)
        align_sales_book_to_plans(db)
        backfill_booking_wallet_credits(db)
    finally:
        db.close()
