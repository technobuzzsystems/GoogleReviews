"""Franchise orgs: allocated client area, own sales team, 20% admin commission."""

from typing import Optional

from sqlalchemy.orm import Session

from models.domain_models import Franchise, SalesExecutive, User, UserRole

ADMIN_FRANCHISE_COMMISSION_RATE = 20.0


def split_franchise_plan(plan_amount: float, salesman_rate: float) -> dict:
    """
    Plan prices stay fixed. Example ₹1,999:
    admin 20% = ₹399.80, salesman 25% ≈ ₹499.75, franchise keeps the rest.
    """
    plan_amount = round(float(plan_amount or 0), 2)
    rate = float(salesman_rate or 0)
    admin_cut = round(plan_amount * ADMIN_FRANCHISE_COMMISSION_RATE / 100.0, 2)
    remaining = round(plan_amount - admin_cut, 2)
    salesman_cut = round(plan_amount * rate / 100.0, 2)
    if salesman_cut > remaining:
        salesman_cut = remaining
    franchise_cut = round(remaining - salesman_cut, 2)
    return {
        "plan_amount": plan_amount,
        "admin": admin_cut,
        "salesman": salesman_cut,
        "franchise": franchise_cut,
        "salesman_rate": rate,
        "admin_rate": ADMIN_FRANCHISE_COMMISSION_RATE,
    }


def get_franchise_for_user(db: Session, user) -> Optional[Franchise]:
    if not user:
        return None
    if getattr(user, "franchise_id", None):
        row = db.query(Franchise).filter(Franchise.id == user.franchise_id, Franchise.is_active.is_(True)).first()
        if row:
            return row
    if getattr(user, "role", None) != UserRole.FRANCHISE:
        return None
    return (
        db.query(Franchise)
        .filter(Franchise.user_id == user.id, Franchise.is_active.is_(True))
        .first()
    )


def list_franchises(db: Session, active_only: bool = True) -> list[Franchise]:
    q = db.query(Franchise)
    if active_only:
        q = q.filter(Franchise.is_active.is_(True))
    return q.order_by(Franchise.name.asc()).all()


def ensure_franchise_profile(db: Session, user: User, area: str = "") -> Optional[Franchise]:
    """Create/update the franchise org when an admin saves a franchise login."""
    if not user or user.role != UserRole.FRANCHISE:
        return None
    area = (area or "").strip()
    row = db.query(Franchise).filter(Franchise.user_id == user.id).first()
    if not row and user.franchise_id:
        row = db.query(Franchise).filter(Franchise.id == user.franchise_id).first()
    if not row:
        row = Franchise(
            user_id=user.id,
            name=user.full_name or user.username,
            area=area or "Allocated",
            wallet_balance=0.0,
            is_active=bool(user.is_active),
        )
        db.add(row)
        db.flush()
    row.user_id = user.id
    row.name = user.full_name or user.username
    if area:
        row.area = area
    elif not (row.area or "").strip():
        row.area = "Allocated"
    row.is_active = bool(user.is_active)
    user.franchise_id = row.id
    return row


def scope_for_user(db: Session, user) -> dict:
    """
    Visibility for lists.
    admin: all
    franchise: their area / sales team
    sales: only their assigned clients
    """
    if not user:
        return {"blocked": True, "franchise_id": None, "executive_id": None, "area": ""}
    if user.role == UserRole.ADMIN:
        return {"blocked": False, "franchise_id": None, "executive_id": None, "area": ""}
    if user.role == UserRole.FRANCHISE:
        org = get_franchise_for_user(db, user)
        if not org:
            return {"blocked": True, "franchise_id": -1, "executive_id": None, "area": ""}
        return {"blocked": False, "franchise_id": org.id, "executive_id": None, "area": org.area or ""}
    if user.role == UserRole.SALES:
        from services.sales_service import get_executive_for_user

        own = get_executive_for_user(db, user)
        if not own:
            return {"blocked": True, "franchise_id": None, "executive_id": -1, "area": ""}
        return {
            "blocked": False,
            "franchise_id": own.franchise_id,
            "executive_id": own.id,
            "area": "",
        }
    return {"blocked": True, "franchise_id": None, "executive_id": None, "area": ""}


def seed_default_franchise(db: Session) -> None:
    user = db.query(User).filter(User.username == "franchise", User.role == UserRole.FRANCHISE).first()
    if not user:
        return
    if not (user.full_name or "").strip():
        user.full_name = "Franchise Partner"
    ensure_franchise_profile(db, user, area="Allocated")
    db.commit()
