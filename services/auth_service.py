"""
Authentication helpers: password hashing, session users, default role seeds.
"""

import logging
from typing import Optional
from urllib.parse import quote, urlparse

import bcrypt
from fastapi import Depends, Request
from sqlalchemy.orm import Session

from database import get_db
from models.domain_models import User, UserRole

logger = logging.getLogger(__name__)

DEFAULT_USERS = (
    ("admin", "admin123", UserRole.ADMIN),
    ("sales", "sales123", UserRole.SALES),
    ("account", "account123", UserRole.ACCOUNT),
    ("marketing", "marketing123", UserRole.MARKETING),
    ("franchise", "franchise123", UserRole.FRANCHISE),
)

ROLE_LABELS = {
    UserRole.ADMIN: "Admin",
    UserRole.SALES: "Salesman",
    UserRole.ACCOUNT: "Account",
    UserRole.MARKETING: "Marketing",
    UserRole.FRANCHISE: "Franchise",
}


class AuthRedirect(Exception):
    """Raised when an HTML admin page should redirect (login or forbidden)."""

    def __init__(self, url: str):
        self.url = url


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def seed_default_users(db: Session) -> None:
    """Create one local user per role if the users table is empty."""
    if db.query(User).count() > 0:
        return
    for username, password, role in DEFAULT_USERS:
        db.add(
            User(
                username=username,
                password_hash=hash_password(password),
                role=role,
                is_active=True,
            )
        )
    db.commit()
    logger.info("Seeded default users: %s", ", ".join(u[0] for u in DEFAULT_USERS))


def authenticate_user(db: Session, username: str, password: str) -> Optional[User]:
    username = (username or "").strip()
    if not username or not password:
        return None
    user = db.query(User).filter(User.username == username).first()
    if not user or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def get_session_user(request: Request, db: Session) -> Optional[User]:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if not user:
        request.session.clear()
        return None
    return user


def login_user(request: Request, user: User) -> None:
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["username"] = user.username
    request.session["role"] = user.role


def logout_user(request: Request) -> None:
    request.session.clear()


def safe_next_url(next_url: str) -> str:
    """Only allow relative /admin redirects after login."""
    if not next_url:
        return "/admin"
    parsed = urlparse(next_url)
    if parsed.scheme or parsed.netloc:
        return "/admin"
    path = parsed.path or ""
    if path.startswith("/admin") and not path.startswith("//"):
        return path
    return "/admin"


def require_login(request: Request, db: Session = Depends(get_db)) -> User:
    user = get_session_user(request, db)
    if not user:
        nxt = quote(request.url.path, safe="/")
        raise AuthRedirect(f"/admin/login?next={nxt}")
    return user


def require_business_access(request: Request, db: Session = Depends(get_db)) -> User:
    user = require_login(request, db)
    if user.role not in UserRole.BUSINESS_MANAGERS:
        raise AuthRedirect("/admin/forbidden")
    return user


def can_manage_businesses(user: Optional[User]) -> bool:
    return bool(user and user.role in UserRole.BUSINESS_MANAGERS)


def can_view_sales_book(user: Optional[User]) -> bool:
    return bool(user and user.role in UserRole.SALES_BOOK)


def require_sales_book(request: Request, db: Session = Depends(get_db)) -> User:
    user = require_login(request, db)
    if user.role not in UserRole.SALES_BOOK:
        raise AuthRedirect("/admin/forbidden")
    return user


def can_manage_users(user: Optional[User]) -> bool:
    return bool(user and user.role == UserRole.ADMIN)


def require_admin(request: Request, db: Session = Depends(get_db)) -> User:
    user = require_login(request, db)
    if user.role != UserRole.ADMIN:
        raise AuthRedirect("/admin/forbidden")
    return user


def _mask_aadhaar(number: str) -> str:
    digits = "".join(ch for ch in (number or "") if ch.isdigit())
    if len(digits) < 4:
        return "—"
    return "XXXX-XXXX-" + digits[-4:]


def _mask_account(number: str) -> str:
    digits = "".join(ch for ch in (number or "") if ch.isdigit())
    if len(digits) < 4:
        return "—"
    return "•••• " + digits[-4:]


def serialize_user(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "role_label": ROLE_LABELS.get(user.role, user.role),
        "full_name": user.full_name or "",
        "phone": user.phone or "",
        "alternate_mobile": user.alternate_mobile or "",
        "email": user.email or "",
        "address": user.address or "",
        "aadhaar": user.aadhaar or "",
        "aadhaar_masked": _mask_aadhaar(user.aadhaar or ""),
        "bank_name": user.bank_name or "",
        "account_name": user.account_name or "",
        "account_number": user.account_number or "",
        "account_masked": _mask_account(user.account_number or ""),
        "ifsc": user.ifsc or "",
        "is_active": bool(user.is_active),
        "has_bank": bool((user.account_number or "").strip() and (user.ifsc or "").strip()),
    }


def list_users(db: Session) -> list[dict]:
    rows = db.query(User).order_by(User.role.asc(), User.username.asc()).all()
    return [serialize_user(u) for u in rows]


def bank_details_for_executive(db: Session, executive) -> Optional[dict]:
    if not executive:
        return None
    user = db.query(User).filter(User.id == executive.user_id).first() if executive.user_id else None
    source = user if user and (user.account_number or "").strip() and (user.ifsc or "").strip() else executive
    account = (getattr(source, "account_number", "") or "").strip()
    ifsc = (getattr(source, "ifsc", "") or "").strip()
    if not account or not ifsc:
        return None
    return {
        "bank_name": (getattr(source, "bank_name", "") or "").strip(),
        "account_name": (getattr(source, "account_name", "") or "").strip() or (getattr(executive, "name", "") or ""),
        "account_number": account,
        "account_masked": _mask_account(account),
        "ifsc": ifsc.upper(),
    }


def _sync_sales_executive(db: Session, user: User) -> None:
    from models.domain_models import SalesExecutive

    if user.role != UserRole.SALES:
        return
    executive = db.query(SalesExecutive).filter(SalesExecutive.user_id == user.id).first()
    if not executive:
        executive = (
            db.query(SalesExecutive)
            .filter(SalesExecutive.name == (user.full_name or user.username), SalesExecutive.user_id.is_(None))
            .first()
        )
    if not executive:
        executive = SalesExecutive(user_id=user.id, name=user.full_name or user.username, is_active=True)
        db.add(executive)
    executive.name = user.full_name or user.username
    executive.phone = user.phone or executive.phone or ""
    executive.bank_name = user.bank_name or ""
    executive.account_name = user.account_name or ""
    executive.account_number = user.account_number or ""
    executive.ifsc = user.ifsc or ""
    executive.is_active = bool(user.is_active)


def save_staff_user(db: Session, user_id: Optional[int], data: dict) -> User:
    """Admin create/update for any role. Account number and IFSC are required."""

    username = (data.get("username") or "").strip().lower()
    role = (data.get("role") or "").strip()
    if role not in UserRole.ALL:
        raise ValueError("Select a valid role.")
    if not username or len(username) < 3:
        raise ValueError("Username must be at least 3 characters.")

    existing = db.query(User).filter(User.username == username).first()
    if existing and (not user_id or existing.id != user_id):
        raise ValueError("That username is already in use.")

    user = db.query(User).filter(User.id == user_id).first() if user_id else None
    is_new = user is None
    if is_new:
        password = data.get("password") or ""
        if len(password) < 6:
            raise ValueError("Password must be at least 6 characters.")
        user = User(username=username, password_hash=hash_password(password), role=role)
        db.add(user)
    else:
        user.username = username
        user.role = role
        password = data.get("password") or ""
        if password:
            if len(password) < 6:
                raise ValueError("Password must be at least 6 characters.")
            user.password_hash = hash_password(password)

    user.full_name = (data.get("full_name") or "").strip()
    if not user.full_name:
        raise ValueError("Full name is required.")
    user.phone = (data.get("phone") or "").strip()
    user.alternate_mobile = (data.get("alternate_mobile") or "").strip()
    user.email = (data.get("email") or "").strip()
    user.address = (data.get("address") or "").strip()
    user.aadhaar = (data.get("aadhaar") or "").strip()
    user.bank_name = (data.get("bank_name") or "").strip()
    user.account_name = (data.get("account_name") or "").strip()
    user.account_number = (data.get("account_number") or "").strip()
    user.ifsc = (data.get("ifsc") or "").strip().upper()
    if not user.bank_name:
        raise ValueError("Bank name is required.")
    if not user.account_name:
        raise ValueError("Account holder name is required.")
    if not user.account_number or not user.ifsc:
        raise ValueError("Account number and IFSC are required for every role.")
    user.is_active = bool(data.get("is_active", True))
    db.flush()
    _sync_sales_executive(db, user)
    db.commit()
    db.refresh(user)
    return user
