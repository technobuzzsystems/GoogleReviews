from datetime import datetime

from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text

from database import Base


class UserRole:
    ADMIN = "admin"
    SALES = "sales"
    ACCOUNT = "account"
    MARKETING = "marketing"
    FRANCHISE = "franchise"

    ALL = (ADMIN, SALES, ACCOUNT, MARKETING, FRANCHISE)
    BUSINESS_MANAGERS = (ADMIN, SALES)
    SALES_BOOK = (ADMIN, SALES)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, index=True)
    full_name = Column(String, default="")
    phone = Column(String, default="")
    alternate_mobile = Column(String, default="")
    email = Column(String, default="")
    address = Column(Text, default="")
    aadhaar = Column(String, default="")
    bank_name = Column(String, default="")
    account_name = Column(String, default="")
    account_number = Column(String, default="")
    ifsc = Column(String, default="")
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class BusinessConfigModel(Base):
    __tablename__ = "businesses"

    key = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    id = Column(String, nullable=False)
    route_slug = Column(String, default="")
    collection = Column(String, default="feedback")
    place_id = Column(String, default="")
    google_review_url = Column(String, default="")
    logo_filename = Column(String, default="")
    scope = Column(String, default="")

    examples_1star = Column(String, default="")
    examples_2star = Column(String, default="")
    examples_3star = Column(String, default="")
    examples_4star = Column(String, default="")
    examples_5star = Column(String, default="")

    mobile = Column(String, default="")
    alternate_mobile = Column(String, default="")
    email = Column(String, default="")
    address = Column(Text, default="")

    plan_code = Column(String, default="")  # 6m | 1y | 2y
    plan_amount = Column(Float, default=0.0)
    join_date = Column(Date, nullable=True)
    expiry_date = Column(Date, nullable=True)
    sales_executive_id = Column(Integer, ForeignKey("sales_executives.id"), nullable=True, index=True)


class SalesExecutive(Base):
    __tablename__ = "sales_executives"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    name = Column(String, nullable=False)
    phone = Column(String, default="")
    commission_rate = Column(Float, default=10.0, nullable=False)
    wallet_balance = Column(Float, default=0.0, nullable=False)
    bank_name = Column(String, default="")
    account_name = Column(String, default="")
    account_number = Column(String, default="")
    ifsc = Column(String, default="")
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    phone = Column(String, default="", index=True)
    email = Column(String, default="")
    business_key = Column(String, default="technobuzz", index=True)
    sales_executive_id = Column(Integer, ForeignKey("sales_executives.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Booking(Base):
    __tablename__ = "bookings"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    sales_executive_id = Column(Integer, ForeignKey("sales_executives.id"), nullable=False, index=True)
    business_key = Column(String, default="technobuzz", index=True)
    booking_type = Column(String, default="new", nullable=False)  # new | renewal
    amount = Column(Float, default=0.0, nullable=False)
    collected_amount = Column(Float, default=0.0, nullable=False)
    commission_rate = Column(Float, default=0.0, nullable=False)
    commission_amount = Column(Float, default=0.0, nullable=False)
    status = Column(String, default="collected", nullable=False)  # booked | collected | cancelled
    notes = Column(Text, default="")
    booked_on = Column(Date, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class WalletLedger(Base):
    __tablename__ = "wallet_ledger"

    id = Column(Integer, primary_key=True, index=True)
    sales_executive_id = Column(Integer, ForeignKey("sales_executives.id"), nullable=False, index=True)
    business_key = Column(String, nullable=False, index=True)
    plan_code = Column(String, default="")
    plan_amount = Column(Float, default=0.0, nullable=False)
    commission_amount = Column(Float, default=0.0, nullable=False)
    join_date = Column(Date, nullable=True)
    booking_id = Column(Integer, ForeignKey("bookings.id"), nullable=True, index=True)
    note = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class WalletWithdrawal(Base):
    __tablename__ = "wallet_withdrawals"

    id = Column(Integer, primary_key=True, index=True)
    sales_executive_id = Column(Integer, ForeignKey("sales_executives.id"), nullable=False, index=True)
    amount = Column(Float, default=0.0, nullable=False)
    bank_name = Column(String, default="")
    account_name = Column(String, default="")
    account_number = Column(String, default="")
    ifsc = Column(String, default="")
    status = Column(String, default="requested", nullable=False, index=True)
    note = Column(String, default="")
    requested_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    processed_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    processed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
