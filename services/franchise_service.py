"""Franchise orgs: allocated client area, own sales team, 53% admin commission."""

from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from models.domain_models import (
    BusinessConfigModel,
    Franchise,
    FranchiseLedger,
    SalesExecutive,
    User,
    UserRole,
    WalletWithdrawal,
)
from services.plan_service import PLANS
from services.sales_service import round_money

ADMIN_FRANCHISE_COMMISSION_RATE = 53.0
MAX_SALESMAN_COMMISSION_RATE = round(100.0 - ADMIN_FRANCHISE_COMMISSION_RATE, 2)


def split_franchise_plan(plan_amount: float, salesman_rate: float) -> dict:
    """
    Plan prices stay fixed. Example ₹1,999:
    admin 53% = ₹1,059.47; remaining 47% is shared — salesman gets their set %,
    franchise keeps the rest.
    """
    plan_amount = round(float(plan_amount or 0), 2)
    rate = min(max(float(salesman_rate or 0), 0.0), MAX_SALESMAN_COMMISSION_RATE)
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
        "pool_rate": MAX_SALESMAN_COMMISSION_RATE,
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


def _ledger_totals(db: Session, franchise_id: int) -> dict:
    row = (
        db.query(
            func.coalesce(func.sum(FranchiseLedger.plan_amount), 0),
            func.coalesce(func.sum(FranchiseLedger.admin_commission), 0),
            func.coalesce(func.sum(FranchiseLedger.salesman_commission), 0),
            func.coalesce(func.sum(FranchiseLedger.franchise_commission), 0),
            func.count(FranchiseLedger.id),
        )
        .filter(FranchiseLedger.franchise_id == franchise_id)
        .first()
    )
    sales, admin, salesman, franchise, count = row or (0, 0, 0, 0, 0)
    return {
        "sales": round_money(sales),
        "admin": round_money(admin),
        "salesman": round_money(salesman),
        "franchise": round_money(franchise),
        "entries": int(count or 0),
    }


def franchise_summaries(db: Session) -> list[dict]:
    """All franchises with collections, splits, and amounts still to transfer."""
    rows = []
    for org in list_franchises(db, active_only=False):
        totals = _ledger_totals(db, org.id)
        owner = db.query(User).filter(User.id == org.user_id).first() if org.user_id else None
        execs = db.query(SalesExecutive).filter(SalesExecutive.franchise_id == org.id).all()
        clients = (
            db.query(func.count(BusinessConfigModel.key))
            .filter(BusinessConfigModel.franchise_id == org.id)
            .scalar()
            or 0
        )
        pending = (
            db.query(func.coalesce(func.sum(WalletWithdrawal.amount), 0))
            .filter(
                WalletWithdrawal.franchise_id == org.id,
                WalletWithdrawal.status == "requested",
            )
            .scalar()
            or 0
        )
        transferred = (
            db.query(func.coalesce(func.sum(WalletWithdrawal.amount), 0))
            .filter(
                WalletWithdrawal.franchise_id == org.id,
                WalletWithdrawal.party_type == "franchise",
                WalletWithdrawal.status == "sent",
            )
            .scalar()
            or 0
        )
        team_wallet = round_money(sum(float(e.wallet_balance or 0) for e in execs))
        rows.append(
            {
                "id": org.id,
                "name": org.name,
                "area": org.area or "",
                "is_active": bool(org.is_active),
                "owner_name": (owner.full_name or owner.username) if owner else org.name,
                "owner_phone": (owner.phone or "") if owner else "",
                "salesmen": len(execs),
                "clients": int(clients),
                "sales": totals["sales"],
                "admin": totals["admin"],
                "salesman": totals["salesman"],
                "franchise": totals["franchise"],
                "wallet": round_money(org.wallet_balance),
                "team_wallet": team_wallet,
                "due_total": round_money(float(org.wallet_balance or 0) + team_wallet),
                "pending": round_money(pending),
                "transferred": round_money(transferred),
            }
        )
    rows.sort(key=lambda r: (-r["due_total"], r["name"].lower()))
    return rows


def franchise_detail(db: Session, franchise_id: int) -> Optional[dict]:
    org = db.query(Franchise).filter(Franchise.id == franchise_id).first()
    if not org:
        return None
    totals = _ledger_totals(db, org.id)
    owner = db.query(User).filter(User.id == org.user_id).first() if org.user_id else None
    names = {e.id: e.name for e in db.query(SalesExecutive).all()}
    biz_names = {b.key: b.name for b in db.query(BusinessConfigModel).filter(BusinessConfigModel.franchise_id == org.id).all()}

    salesmen = []
    for exec_ in (
        db.query(SalesExecutive)
        .filter(SalesExecutive.franchise_id == org.id)
        .order_by(SalesExecutive.name.asc())
        .all()
    ):
        exec_ledgers = (
            db.query(FranchiseLedger)
            .filter(FranchiseLedger.franchise_id == org.id, FranchiseLedger.sales_executive_id == exec_.id)
            .all()
        )
        salesmen.append(
            {
                "id": exec_.id,
                "name": exec_.name,
                "phone": exec_.phone or "",
                "commission_rate": round_money(exec_.commission_rate),
                "clients": len({row.business_key for row in exec_ledgers}),
                "sales": round_money(sum(float(row.plan_amount or 0) for row in exec_ledgers)),
                "commission": round_money(sum(float(row.salesman_commission or 0) for row in exec_ledgers)),
                "wallet": round_money(exec_.wallet_balance),
            }
        )

    collections = []
    for row in (
        db.query(FranchiseLedger)
        .filter(FranchiseLedger.franchise_id == org.id)
        .order_by(FranchiseLedger.created_at.desc(), FranchiseLedger.id.desc())
        .all()
    ):
        collections.append(
            {
                "id": row.id,
                "business_key": row.business_key,
                "name": biz_names.get(row.business_key) or row.business_key,
                "plan_label": PLANS.get(row.plan_code or "", {}).get("label", row.plan_code or "—"),
                "plan_amount": round_money(row.plan_amount),
                "admin": round_money(row.admin_commission),
                "salesman": round_money(row.salesman_commission),
                "franchise": round_money(row.franchise_commission),
                "salesman_name": names.get(row.sales_executive_id, "—"),
                "join_date": row.join_date.isoformat() if row.join_date else "",
                "created_at": row.created_at.strftime("%Y-%m-%d %H:%M") if row.created_at else "",
                "note": row.note or "",
            }
        )

    return {
        "id": org.id,
        "name": org.name,
        "area": org.area or "",
        "is_active": bool(org.is_active),
        "wallet": round_money(org.wallet_balance),
        "owner": {
            "id": owner.id if owner else None,
            "name": (owner.full_name or owner.username) if owner else org.name,
            "username": owner.username if owner else "",
            "phone": (owner.phone or "") if owner else "",
            "email": (owner.email or "") if owner else "",
        },
        "totals": totals,
        "team_wallet": round_money(sum(s["wallet"] for s in salesmen)),
        "due_total": round_money(float(org.wallet_balance or 0) + sum(s["wallet"] for s in salesmen)),
        "clients": len({c["business_key"] for c in collections}),
        "salesmen": salesmen,
        "collections": collections,
    }
