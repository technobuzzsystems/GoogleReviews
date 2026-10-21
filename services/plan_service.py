"""Subscription plans for businesses and salesman wallet commission."""

from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from models.domain_models import Booking, BusinessConfigModel, SalesExecutive, SubscriptionPlan, WalletLedger

DEFAULT_PLANS = {
    "15d": {"code": "15d", "label": "15 Days", "days": 15, "months": 0, "amount": 1.0, "sort_order": 10},
    "6m": {"code": "6m", "label": "6 Months", "days": 0, "months": 6, "amount": 1499.0, "sort_order": 20},
    "1y": {"code": "1y", "label": "1 Year", "days": 0, "months": 12, "amount": 1999.0, "sort_order": 30},
    "2y": {"code": "2y", "label": "2 Years", "days": 0, "months": 24, "amount": 2999.0, "sort_order": 40},
}

PLANS = {
    k: {key: val for key, val in v.items() if key != "sort_order" and (key not in ("days", "months") or val > 0)}
    for k, v in DEFAULT_PLANS.items()
}

PLAN_COMMISSION_RATE = 25.0  # default salesman wallet %; franchise can override per salesman
CLIENT_PLAN_NOTE = "Client plan"


def _serialize_plan_row(row: SubscriptionPlan) -> dict:
    item = {
        "code": row.code,
        "label": row.label,
        "amount": round(float(row.amount or 0), 2),
        "sort_order": int(row.sort_order or 100),
        "is_active": bool(row.is_active),
        "days": int(row.days or 0),
        "months": int(row.months or 0),
    }
    return item


def sync_plans_cache(db: Session) -> dict:
    """Sync the in-memory PLANS dictionary from the subscription_plans table."""
    rows = (
        db.query(SubscriptionPlan)
        .filter(SubscriptionPlan.is_active.is_(True))
        .order_by(SubscriptionPlan.sort_order.asc(), SubscriptionPlan.amount.asc(), SubscriptionPlan.code.asc())
        .all()
    )
    if not rows:
        return PLANS
    PLANS.clear()
    for row in rows:
        entry = {
            "code": row.code,
            "label": row.label,
            "amount": round(float(row.amount or 0), 2),
        }
        if (row.days or 0) > 0:
            entry["days"] = int(row.days)
        if (row.months or 0) > 0:
            entry["months"] = int(row.months)
        PLANS[row.code] = entry
    return PLANS


def seed_default_plans(db: Session) -> None:
    """Seed the default 4 subscription plans if subscription_plans table is empty, and sync PLANS."""
    if db.query(SubscriptionPlan).count() == 0:
        for code, p in DEFAULT_PLANS.items():
            db.add(
                SubscriptionPlan(
                    code=code,
                    label=p["label"],
                    days=p.get("days", 0),
                    months=p.get("months", 0),
                    amount=p["amount"],
                    sort_order=p.get("sort_order", 100),
                    is_active=True,
                )
            )
        db.commit()
    sync_plans_cache(db)


def list_subscription_plans(db: Session, *, active_only: bool = False) -> list[dict]:
    """Return all subscription plans ordered by sort_order and amount."""
    q = db.query(SubscriptionPlan)
    if active_only:
        q = q.filter(SubscriptionPlan.is_active.is_(True))
    rows = q.order_by(SubscriptionPlan.sort_order.asc(), SubscriptionPlan.amount.asc(), SubscriptionPlan.code.asc()).all()
    return [_serialize_plan_row(r) for r in rows]


def get_subscription_plan(db: Session, code: str) -> Optional[dict]:
    code = (code or "").strip()
    if not code:
        return None
    row = db.query(SubscriptionPlan).filter(SubscriptionPlan.code == code).first()
    return _serialize_plan_row(row) if row else None


def save_subscription_plan(
    db: Session,
    *,
    original_code: str = "",
    code: str = "",
    label: str = "",
    duration_type: str = "months",
    duration_value: int = 0,
    amount: float = 0.0,
) -> SubscriptionPlan:
    """Create or update a subscription plan and refresh the PLANS cache."""
    import re

    label = (label or "").strip()[:60]
    if not label:
        raise ValueError("Plan name / label is required (e.g. '3 Months' or '1 Year').")

    duration_type = (duration_type or "months").strip().lower()
    if duration_type not in ("days", "months"):
        raise ValueError("Select duration unit as Days or Months.")

    try:
        duration_val = int(duration_value or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("Enter a valid duration number.") from exc
    if duration_val <= 0 or duration_val > 3650:
        raise ValueError("Duration must be between 1 and 3650.")

    try:
        amount_val = round(float(amount or 0), 2)
    except (TypeError, ValueError) as exc:
        raise ValueError("Enter a valid plan amount.") from exc
    if amount_val <= 0:
        raise ValueError("Plan amount must be greater than ₹0.")

    clean_orig = (original_code or "").strip().lower()
    clean_code = (code or "").strip().lower()
    if not clean_code:
        suffix = "d" if duration_type == "days" else "m"
        clean_code = f"{duration_val}{suffix}"
    clean_code = re.sub(r"[^a-z0-9_-]+", "", clean_code)[:24]
    if not clean_code:
        raise ValueError("Enter a valid plan code (letters/numbers, e.g. '3m', '30d', '3y').")

    days = duration_val if duration_type == "days" else 0
    months = duration_val if duration_type == "months" else 0
    approx_days = days if days > 0 else months * 30

    row = None
    if clean_orig:
        row = db.query(SubscriptionPlan).filter(SubscriptionPlan.code == clean_orig).first()
        if not row:
            raise ValueError("Plan to edit was not found.")
    else:
        existing = db.query(SubscriptionPlan).filter(SubscriptionPlan.code == clean_code).first()
        if existing:
            raise ValueError(f"Plan code '{clean_code}' already exists. Edit the existing plan or use a different code.")
        row = SubscriptionPlan(code=clean_code)
        db.add(row)

    row.label = label
    row.days = days
    row.months = months
    row.amount = amount_val
    row.sort_order = approx_days
    row.is_active = True
    db.commit()
    db.refresh(row)
    sync_plans_cache(db)
    return row


def delete_subscription_plan(db: Session, code: str) -> None:
    """Delete a subscription plan (ensuring at least 1 active plan remains)."""
    clean = (code or "").strip()
    row = db.query(SubscriptionPlan).filter(SubscriptionPlan.code == clean).first()
    if not row:
        raise ValueError("Subscription plan not found.")
    total_count = db.query(SubscriptionPlan).count()
    if total_count <= 1:
        raise ValueError("Cannot delete the last remaining subscription plan.")
    db.delete(row)
    db.commit()
    sync_plans_cache(db)


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
    if "days" in plan:
        expiry_date = join_on + timedelta(days=plan["days"])
    else:
        expiry_date = add_months(join_on, plan["months"])

    return {
        "plan_code": plan["code"],
        "plan_amount": plan["amount"],
        "join_date": join_on,
        "expiry_date": expiry_date,
        "label": plan["label"],
    }


def plan_status(expiry_date: Optional[date]) -> str:
    if not expiry_date:
        return "none"
    return "active" if expiry_date >= date.today() else "expired"


def booking_is_collected(booking: Optional[Booking]) -> bool:
    """True only when the booking is fully paid/collected."""
    if not booking:
        return False
    amount = round(float(booking.amount or 0), 2)
    collected = round(float(booking.collected_amount or 0), 2)
    if amount <= 0 or collected < amount:
        return False
    return (booking.status or "").strip().lower() in {"collected", "paid"}


def credit_plan_to_wallet(
    db: Session,
    sales_executive_id: Optional[int],
    business_key: str,
    plan_code: str,
    plan_amount: float,
    join_date: Optional[date],
) -> float:
    """Credit salesman commission. Franchise clients split 53% to admin; remainder is franchise + salesman."""
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


def reverse_plan_wallet_credits(db: Session, business_key: str, join_date=None, *, commit: bool = False) -> float:
    """Remove plan-commission wallet entries for an unpaid plan period."""
    if not business_key:
        return 0.0
    from models.domain_models import Franchise, FranchiseLedger

    total = 0.0
    entries = (
        db.query(WalletLedger)
        .filter(WalletLedger.business_key == business_key, WalletLedger.plan_code != "")
        .all()
    )
    for entry in entries:
        if join_date and entry.join_date and entry.join_date != join_date:
            continue
        money = round(float(entry.commission_amount or 0), 2)
        executive = db.query(SalesExecutive).filter(SalesExecutive.id == entry.sales_executive_id).first()
        if executive:
            executive.wallet_balance = round(max(float(executive.wallet_balance or 0) - money, 0.0), 2)
        db.delete(entry)
        total += money
    ledgers = db.query(FranchiseLedger).filter(FranchiseLedger.business_key == business_key).all()
    for row in ledgers:
        if join_date and row.join_date and row.join_date != join_date:
            continue
        franchise = db.query(Franchise).filter(Franchise.id == row.franchise_id).first()
        if franchise:
            cut = round(float(row.franchise_commission or 0), 2)
            franchise.wallet_balance = round(max(float(franchise.wallet_balance or 0) - cut, 0.0), 2)
        db.delete(row)
    if commit:
        db.commit()
    return total


def credit_booking_to_wallet(db: Session, booking: Booking, *, commit: bool = False) -> float:
    """Credit booking commission into the salesman wallet after collection. Skips duplicates."""
    if not booking or not booking.sales_executive_id:
        return 0.0
    if not booking_is_collected(booking):
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
