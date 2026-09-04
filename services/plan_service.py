"""Subscription plans for businesses and salesman wallet commission."""

from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from models.domain_models import Booking, BusinessConfigModel, SalesExecutive, WalletLedger

PLANS = {
    "6m": {"code": "6m", "label": "6 Months", "months": 6, "amount": 1499.0},
    "1y": {"code": "1y", "label": "1 Year", "months": 12, "amount": 1999.0},
    "2y": {"code": "2y", "label": "2 Years", "months": 24, "amount": 2999.0},
}

PLAN_COMMISSION_RATE = 25.0  # default salesman wallet %; franchise can override per salesman
CLIENT_PLAN_NOTE = "Client plan"


def add_months(start: date, months: int) -> date:
    month_index = start.month - 1 + int(months)
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, monthrange(year, month)[1])
    return date(year, month, day)


def parse_date(value) -> Optional[date]:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text:
        return None
    return datetime.strptime(text[:10], "%Y-%m-%d").date()


def resolve_plan(plan_code: str, join_date_value=None) -> dict:
    plan = PLANS.get((plan_code or "").strip())
    if not plan:
        return {
            "plan_code": "",
            "plan_amount": 0.0,
            "join_date": parse_date(join_date_value),
            "expiry_date": None,
            "label": "",
        }
    join_on = parse_date(join_date_value) or date.today()
    return {
        "plan_code": plan["code"],
        "plan_amount": plan["amount"],
        "join_date": join_on,
        "expiry_date": add_months(join_on, plan["months"]),
        "label": plan["label"],
    }


def plan_status(expiry_date: Optional[date]) -> str:
    if not expiry_date:
        return "none"
    return "active" if expiry_date >= date.today() else "expired"


def credit_plan_to_wallet(
    db: Session,
    sales_executive_id: Optional[int],
    business_key: str,
    plan_code: str,
    plan_amount: float,
    join_date: Optional[date],
) -> float:
    """Credit salesman commission. Franchise clients also split 20% to admin (plan prices unchanged)."""
    if not sales_executive_id or not plan_code or not plan_amount:
        return 0.0

    existing = (
        db.query(WalletLedger)
        .filter(
            WalletLedger.sales_executive_id == sales_executive_id,
            WalletLedger.business_key == business_key,
            WalletLedger.plan_code == plan_code,
            WalletLedger.join_date == join_date,
        )
        .first()
    )
    if existing:
        return 0.0

    executive = db.query(SalesExecutive).filter(SalesExecutive.id == sales_executive_id).first()
    if not executive:
        return 0.0

    from services.franchise_service import ADMIN_FRANCHISE_COMMISSION_RATE, split_franchise_plan
    from models.domain_models import Franchise, FranchiseLedger, BusinessConfigModel

    rate = float(executive.commission_rate or PLAN_COMMISSION_RATE)
    plan_amount = float(plan_amount)
    salesman_cut = round(plan_amount * rate / 100.0, 2)
    admin_cut = 0.0
    franchise_cut = 0.0
    franchise = None
    business = db.query(BusinessConfigModel).filter(BusinessConfigModel.key == business_key).first()
    franchise_id = (business.franchise_id if business else None) or executive.franchise_id
    if franchise_id:
        franchise = db.query(Franchise).filter(Franchise.id == franchise_id).first()
    if franchise:
        split = split_franchise_plan(plan_amount, rate)
        admin_cut = split["admin"]
        salesman_cut = split["salesman"]
        franchise_cut = split["franchise"]

    executive.wallet_balance = round(float(executive.wallet_balance or 0) + salesman_cut, 2)
    db.add(
        WalletLedger(
            sales_executive_id=executive.id,
            business_key=business_key,
            plan_code=plan_code,
            plan_amount=plan_amount,
            commission_amount=salesman_cut,
            join_date=join_date,
            note=f"{rate:g}% salesman commission on {PLANS.get(plan_code, {}).get('label', plan_code)} plan (₹{int(plan_amount)})",
        )
    )
    if franchise:
        franchise.wallet_balance = round(float(franchise.wallet_balance or 0) + franchise_cut, 2)
        db.add(
            FranchiseLedger(
                franchise_id=franchise.id,
                business_key=business_key,
                plan_code=plan_code,
                plan_amount=plan_amount,
                admin_commission=admin_cut,
                salesman_commission=salesman_cut,
                franchise_commission=franchise_cut,
                sales_executive_id=executive.id,
                join_date=join_date,
                note=f"Admin {ADMIN_FRANCHISE_COMMISSION_RATE:g}% · salesman {rate:g}% · franchise remainder",
            )
        )
    db.commit()
    return salesman_cut


def reverse_booking_wallet_credit(db: Session, booking: Booking, *, commit: bool = False) -> float:
    """Remove a booking's wallet credit and subtract it from the salesman wallet."""
    if not booking or not booking.id:
        return 0.0
    entry = db.query(WalletLedger).filter(WalletLedger.booking_id == booking.id).first()
    if not entry:
        return 0.0
    money = round(float(entry.commission_amount or 0), 2)
    executive = db.query(SalesExecutive).filter(SalesExecutive.id == entry.sales_executive_id).first()
    if executive:
        executive.wallet_balance = round(max(float(executive.wallet_balance or 0) - money, 0.0), 2)
    db.delete(entry)
    if commit:
        db.commit()
    return money


def credit_booking_to_wallet(db: Session, booking: Booking, *, commit: bool = False) -> float:
    """Credit booking commission into the salesman wallet. Skips duplicates."""
    if not booking or not booking.sales_executive_id:
        return 0.0
    if (booking.notes or "").strip() == CLIENT_PLAN_NOTE:
        return 0.0
    money = round(float(booking.commission_amount or 0), 2)
    if money <= 0:
        return 0.0
    existing = db.query(WalletLedger).filter(WalletLedger.booking_id == booking.id).first()
    if existing:
        return 0.0
    executive = db.query(SalesExecutive).filter(SalesExecutive.id == booking.sales_executive_id).first()
    if not executive:
        return 0.0
    booked_on = booking.booked_on or date.today()
    executive.wallet_balance = round(float(executive.wallet_balance or 0) + money, 2)
    db.add(
        WalletLedger(
            sales_executive_id=executive.id,
            business_key=booking.business_key or "technobuzz",
            plan_code="",
            plan_amount=float(booking.amount or 0),
            commission_amount=money,
            join_date=booked_on,
            booking_id=booking.id,
            note=f"Booking commission · {booking.booking_type or 'sale'} ₹{int(booking.amount or 0)}",
            created_at=datetime.combine(booked_on, datetime.min.time()) + timedelta(hours=12),
        )
    )
    if commit:
        db.commit()
    return money


def backfill_booking_wallet_credits(db: Session) -> int:
    """Put existing booking commission into wallets so this month's actual amount is payable."""
    credited_ids = {
        row[0]
        for row in db.query(WalletLedger.booking_id).filter(WalletLedger.booking_id.isnot(None)).all()
    }
    rows = (
        db.query(Booking)
        .filter(Booking.status != "cancelled", Booking.commission_amount > 0)
        .all()
    )
    count = 0
    for booking in rows:
        if booking.id in credited_ids:
            continue
        if credit_booking_to_wallet(db, booking, commit=False):
            count += 1
    if count:
        db.commit()
    return count


def list_wallet_ledger(
    db: Session,
    sales_executive_id: Optional[int] = None,
    since: Optional[date] = None,
    until: Optional[date] = None,
) -> list[dict]:
    q = db.query(WalletLedger, SalesExecutive, BusinessConfigModel).join(
        SalesExecutive, SalesExecutive.id == WalletLedger.sales_executive_id
    ).outerjoin(
        BusinessConfigModel, BusinessConfigModel.key == WalletLedger.business_key
    )
    if sales_executive_id:
        q = q.filter(WalletLedger.sales_executive_id == sales_executive_id)
    if since:
        q = q.filter(WalletLedger.created_at >= datetime.combine(since, datetime.min.time()))
    if until:
        q = q.filter(WalletLedger.created_at < datetime.combine(until + timedelta(days=1), datetime.min.time()))
    rows = q.order_by(WalletLedger.created_at.desc(), WalletLedger.id.desc()).all()
    result = []
    for entry, executive, business in rows:
        result.append(
            {
                "id": entry.id,
                "executive_name": executive.name,
                "business_key": entry.business_key,
                "business_name": business.name if business else entry.business_key,
                "plan_code": entry.plan_code,
                "plan_label": PLANS.get(entry.plan_code, {}).get("label", entry.plan_code),
                "plan_amount": entry.plan_amount,
                "commission_amount": entry.commission_amount,
                "join_date": entry.join_date.isoformat() if entry.join_date else "",
                "note": entry.note,
                "source": "booking" if entry.booking_id else "plan",
                "created_at": entry.created_at.strftime("%Y-%m-%d %H:%M") if entry.created_at else "",
            }
        )
    return result
