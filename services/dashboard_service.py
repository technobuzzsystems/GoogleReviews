"""Overview lists, month-wise commission, and wallet-to-bank withdrawals."""

from calendar import monthrange
from datetime import date, datetime, timedelta
from math import ceil
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from models.domain_models import (
    Booking,
    BusinessConfigModel,
    Customer,
    Franchise,
    FranchiseLedger,
    SalesExecutive,
    UserRole,
    WalletLedger,
    WalletWithdrawal,
)
from services.plan_service import PLANS
from services.sales_service import get_executive_for_user, round_money

RENEWAL_WINDOW_DAYS = 60
OVERVIEW_GRID_LIMIT = 48
COMMISSION_PAGE_SIZES = (12, 24, 48, 96)
DEFAULT_COMMISSION_PAGE_SIZE = 12


def _scope_executive_id(db: Session, user) -> Optional[int]:
    from services.franchise_service import scope_for_user

    if not user:
        return None
    if getattr(user, "role", None) == UserRole.ADMIN:
        return None
    if getattr(user, "role", None) == UserRole.FRANCHISE:
        return None
    own = get_executive_for_user(db, user)
    return own.id if own else -1


def _scope_franchise_id(db: Session, user) -> Optional[int]:
    from services.franchise_service import scope_for_user

    scope = scope_for_user(db, user)
    if scope["blocked"]:
        return -1
    return scope["franchise_id"]


def _wallet_scope(db: Session, user) -> dict:
    """Visibility for wallets, commission ledgers, and withdrawals."""
    exec_id = _scope_executive_id(db, user)
    fr_id = _scope_franchise_id(db, user)
    empty = exec_id == -1 or fr_id == -1
    return {
        "empty": empty,
        "exec_id": None if exec_id in (None, -1) else exec_id,
        "franchise_id": None if fr_id in (None, -1) else fr_id,
    }


def _filter_wallet_ledger(q, scope: dict):
    if scope.get("exec_id"):
        return q.filter(WalletLedger.sales_executive_id == scope["exec_id"])
    if scope.get("franchise_id"):
        return q.join(SalesExecutive, SalesExecutive.id == WalletLedger.sales_executive_id).filter(
            SalesExecutive.franchise_id == scope["franchise_id"]
        )
    return q


def _salesman_names(db: Session) -> dict:
    return {e.id: e.name for e in db.query(SalesExecutive).all()}


def _days_left(expiry: Optional[date], today: date) -> Optional[int]:
    if not expiry:
        return None
    return (expiry - today).days


def _renewal_label(days: Optional[int]) -> str:
    if days is None:
        return "No plan"
    if days < 0:
        return f"Expired {abs(days)}d ago"
    if days == 0:
        return "Expires today"
    if days == 1:
        return "Expires tomorrow"
    return f"Due in {days} days"


def list_renewal_clients(db: Session, user, limit: int = OVERVIEW_GRID_LIMIT) -> dict:
    """Businesses whose plan expired, is due soon, or is the next upcoming renewal."""
    today = date.today()
    cutoff = today + timedelta(days=RENEWAL_WINDOW_DAYS)
    exec_id = _scope_executive_id(db, user)
    if exec_id == -1:
        return {"rows": [], "total": 0}

    base = db.query(BusinessConfigModel).filter(BusinessConfigModel.expiry_date.isnot(None))
    if exec_id:
        base = base.filter(BusinessConfigModel.sales_executive_id == exec_id)
    fr_id = _scope_franchise_id(db, user)
    if fr_id == -1:
        return {"rows": [], "total": 0}
    if fr_id:
        base = base.filter(BusinessConfigModel.franchise_id == fr_id)

    due = base.filter(BusinessConfigModel.expiry_date <= cutoff)
    due_total = due.with_entities(func.count(BusinessConfigModel.key)).order_by(None).scalar() or 0
    q = due if due_total else base
    total = q.with_entities(func.count(BusinessConfigModel.key)).order_by(None).scalar() or 0
    names = _salesman_names(db)
    rows = []
    for b in q.order_by(BusinessConfigModel.expiry_date.asc(), BusinessConfigModel.name.asc()).limit(limit):
        days = _days_left(b.expiry_date, today)
        rows.append(
            {
                "key": b.key,
                "name": b.name,
                "mobile": b.mobile or "",
                "email": b.email or "",
                "plan_label": PLANS.get(b.plan_code or "", {}).get("label", b.plan_code or "—"),
                "plan_amount": round_money(b.plan_amount),
                "join_date": b.join_date.isoformat() if b.join_date else "",
                "expiry_date": b.expiry_date.isoformat() if b.expiry_date else "",
                "salesman_name": names.get(b.sales_executive_id, "Unassigned"),
                "days_left": days,
                "due_label": _renewal_label(days),
                "overdue": bool(days is not None and days < 0),
                "due_soon": bool(days is not None and 0 <= days <= RENEWAL_WINDOW_DAYS),
            }
        )
    return {"rows": rows, "total": int(total)}


def list_pending_payments(db: Session, user, limit: int = OVERVIEW_GRID_LIMIT) -> dict:
    """Clients with booking amount still unpaid."""
    exec_id = _scope_executive_id(db, user)
    if exec_id == -1:
        return {"rows": [], "total": 0, "amount_due": 0.0}
    fr_id = _scope_franchise_id(db, user)
    if fr_id == -1:
        return {"rows": [], "total": 0, "amount_due": 0.0}

    q = (
        db.query(Booking, Customer, SalesExecutive)
        .join(Customer, Customer.id == Booking.customer_id)
        .join(SalesExecutive, SalesExecutive.id == Booking.sales_executive_id)
        .filter(Booking.status != "cancelled")
        .filter(Booking.amount > Booking.collected_amount)
    )
    if exec_id:
        q = q.filter(Booking.sales_executive_id == exec_id)
    if fr_id:
        q = q.outerjoin(BusinessConfigModel, BusinessConfigModel.key == Booking.business_key).filter(
            (SalesExecutive.franchise_id == fr_id) | (BusinessConfigModel.franchise_id == fr_id)
        )

    total = q.with_entities(func.count(Booking.id)).order_by(None).scalar() or 0
    due_sum = (
        q.with_entities(func.coalesce(func.sum(Booking.amount - Booking.collected_amount), 0))
        .order_by(None)
        .scalar()
        or 0
    )
    rows = []
    for booking, customer, executive in q.order_by(
        (Booking.amount - Booking.collected_amount).desc(), Booking.booked_on.asc()
    ).limit(limit):
        remaining = round_money(max((booking.amount or 0) - (booking.collected_amount or 0), 0))
        rows.append(
            {
                "id": booking.id,
                "name": customer.name,
                "mobile": customer.phone or "",
                "booking_type": booking.booking_type,
                "booked_on": booking.booked_on.isoformat() if booking.booked_on else "",
                "amount": round_money(booking.amount),
                "collected": round_money(booking.collected_amount),
                "remaining": remaining,
                "salesman_name": executive.name,
                "business_key": booking.business_key,
            }
        )
    return {"rows": rows, "total": int(total), "amount_due": round_money(due_sum)}


def current_period_bounds(period: str, today: Optional[date] = None) -> tuple[date, date]:
    today = today or date.today()
    period = (period or "month").lower()
    if period == "year":
        return date(today.year, 1, 1), date(today.year, 12, 31)
    if period == "quarter":
        start_month = ((today.month - 1) // 3) * 3 + 1
        end_month = start_month + 2
        return date(today.year, start_month, 1), date(today.year, end_month, monthrange(today.year, end_month)[1])
    return date(today.year, today.month, 1), date(today.year, today.month, monthrange(today.year, today.month)[1])


def _period_bucket(value, period: str) -> tuple[str, str, str]:
    if hasattr(value, "date") and not isinstance(value, date):
        value = value.date()
    if period == "year":
        key = f"{value.year}"
        return key, key, key
    if period == "quarter":
        q = (value.month - 1) // 3 + 1
        key = f"{value.year}-Q{q}"
        return key, f"Q{q} {value.year}", f"Q{q}"
    key = f"{value.year:04d}-{value.month:02d}"
    stamp = date(value.year, value.month, 1)
    return key, stamp.strftime("%b %Y"), stamp.strftime("%b")


def _iter_period_keys(period: str, count: int, today: Optional[date] = None) -> list[tuple[str, str, str]]:
    today = today or date.today()
    if period == "year":
        year = today.year
        keys = []
        for _ in range(count):
            keys.append((f"{year}", f"{year}", f"{year}"))
            year -= 1
        keys.reverse()
        return keys
    if period == "quarter":
        year = today.year
        return [(f"{year}-Q{q}", f"Q{q} {year}", f"Q{q}") for q in range(1, 5)]
    year = today.year
    return [
        (
            f"{year:04d}-{month:02d}",
            date(year, month, 1).strftime("%b %Y"),
            date(year, month, 1).strftime("%b"),
        )
        for month in range(1, 13)
    ]


def commission_track(db: Session, user, period: str = "month") -> dict:
    """Wallet + booking commission grouped by month, quarter, or year."""
    period = period if period in ("month", "quarter", "year") else "month"
    count = {"month": 12, "quarter": 8, "year": 5}[period]
    scope = _wallet_scope(db, user)

    today = date.today()
    buckets = _iter_period_keys(period, count, today)
    keys = [item[0] for item in buckets]
    wallet_map = {k: 0.0 for k in keys}
    booking_map = {k: 0.0 for k in keys}

    if not scope["empty"]:
        start = datetime.strptime(keys[0] + "-01", "%Y-%m-%d") if period == "month" else None
        if period == "quarter":
            year, q = int(keys[0][:4]), int(keys[0][-1])
            start = datetime(year, (q - 1) * 3 + 1, 1)
        elif period == "year":
            start = datetime(int(keys[0]), 1, 1)
        wallet_q = db.query(WalletLedger).filter(WalletLedger.created_at >= start)
        wallet_q = _filter_wallet_ledger(wallet_q, scope)
        for row in wallet_q.all():
            if not row.created_at:
                continue
            key, _, _ = _period_bucket(row.created_at, period)
            amount = row.commission_amount or 0
            if key in wallet_map and row.booking_id:
                booking_map[key] = round_money(booking_map[key] + amount)
            elif key in wallet_map:
                wallet_map[key] = round_money(wallet_map[key] + amount)

    current_key, current_label, _ = _period_bucket(today, period)
    rows = []
    for key, label, short_label in buckets:
        wallet = wallet_map[key]
        bookings = booking_map[key]
        rows.append(
            {
                "key": key,
                "label": label,
                "short_label": short_label,
                "wallet": wallet,
                "bookings": bookings,
                "total": round_money(wallet + bookings),
                "is_current": key == current_key,
                "is_future": key > current_key,
            }
        )
    peak = max((m["total"] for m in rows), default=0) or 1
    for i, month_row in enumerate(rows):
        total = month_row["total"]
        prev = rows[i - 1]["total"] if i else None
        month_row["bar_pct"] = max(8, round((total / peak) * 100)) if total else 4
        month_row["wallet_share"] = round((month_row["wallet"] / total) * 100) if total else 0
        month_row["booking_share"] = 100 - month_row["wallet_share"] if total else 0
        month_row["vs_prev"] = round_money(total - prev) if prev is not None else 0.0
        month_row["vs_prev_up"] = bool(prev is not None and total >= prev)

    current = next((m for m in rows if m["is_current"]), rows[-1] if rows else None)
    start, end = current_period_bounds(period, today)
    period_names = {"month": "This month", "quarter": "This quarter", "year": "This year"}
    vs_names = {"month": "last month", "quarter": "last quarter", "year": "last year"}
    chart_names = {
        "month": f"{today.year} · month-wise",
        "quarter": f"{today.year} · quarter-wise",
        "year": "Year-wise",
    }
    return {
        "period": period,
        "period_label": period_names[period],
        "vs_label": vs_names[period],
        "chart_label": chart_names[period],
        "current_label": current_label,
        "range_start": start.isoformat(),
        "range_end": end.isoformat(),
        "rows": rows,
        "current": current,
    }


def month_commission_track(db: Session, user, months: int = 6) -> list[dict]:
    return commission_track(db, user, period="month")["rows"][-months:]


def overview_counts(db: Session, user) -> dict:
    scope = _wallet_scope(db, user)
    empty = {
        "clients": 0,
        "wallet_balance": 0.0,
        "team_wallet": 0.0,
        "franchise_wallet": 0.0,
        "pending_transfer": 0.0,
        "area": "",
    }
    if scope["empty"]:
        return empty
    bq = db.query(func.count(BusinessConfigModel.key))
    wq = db.query(func.coalesce(func.sum(SalesExecutive.wallet_balance), 0))
    pq = db.query(func.coalesce(func.sum(WalletWithdrawal.amount), 0)).filter(WalletWithdrawal.status == "requested")
    if scope["exec_id"]:
        bq = bq.filter(BusinessConfigModel.sales_executive_id == scope["exec_id"])
        wq = wq.filter(SalesExecutive.id == scope["exec_id"])
        pq = pq.filter(WalletWithdrawal.sales_executive_id == scope["exec_id"])
    elif scope["franchise_id"]:
        bq = bq.filter(BusinessConfigModel.franchise_id == scope["franchise_id"])
        wq = wq.filter(SalesExecutive.franchise_id == scope["franchise_id"])
        pq = pq.join(SalesExecutive, SalesExecutive.id == WalletWithdrawal.sales_executive_id).filter(
            SalesExecutive.franchise_id == scope["franchise_id"]
        )
    team_wallet = round_money(wq.scalar() or 0)
    franchise_wallet = 0.0
    area = ""
    if scope["franchise_id"] and getattr(user, "role", None) == UserRole.FRANCHISE:
        org = db.query(Franchise).filter(Franchise.id == scope["franchise_id"]).first()
        if org:
            franchise_wallet = round_money(org.wallet_balance)
            area = org.area or ""
    wallet_balance = franchise_wallet if getattr(user, "role", None) == UserRole.FRANCHISE else team_wallet
    return {
        "clients": int(bq.scalar() or 0),
        "wallet_balance": wallet_balance,
        "team_wallet": team_wallet,
        "franchise_wallet": franchise_wallet,
        "pending_transfer": round_money(pq.scalar() or 0),
        "area": area,
    }


def _sum_ledger(db: Session, scope: dict, since: Optional[date] = None, until: Optional[date] = None) -> dict:
    q = db.query(WalletLedger)
    q = _filter_wallet_ledger(q, scope)
    if since:
        q = q.filter(WalletLedger.created_at >= datetime.combine(since, datetime.min.time()))
    if until:
        q = q.filter(WalletLedger.created_at < datetime.combine(until + timedelta(days=1), datetime.min.time()))
    plans = bookings = 0.0
    for row in q.all():
        amount = float(row.commission_amount or 0)
        if row.booking_id:
            bookings += amount
        else:
            plans += amount
    return {
        "plans": round_money(plans),
        "bookings": round_money(bookings),
        "total": round_money(plans + bookings),
    }


def _sum_franchise_split(db: Session, franchise_id: int, since: Optional[date] = None, until: Optional[date] = None) -> dict:
    q = db.query(FranchiseLedger).filter(FranchiseLedger.franchise_id == franchise_id)
    if since:
        q = q.filter(FranchiseLedger.created_at >= datetime.combine(since, datetime.min.time()))
    if until:
        q = q.filter(FranchiseLedger.created_at < datetime.combine(until + timedelta(days=1), datetime.min.time()))
    admin = salesman = franchise = plans = 0.0
    for row in q.all():
        admin += float(row.admin_commission or 0)
        salesman += float(row.salesman_commission or 0)
        franchise += float(row.franchise_commission or 0)
        plans += float(row.plan_amount or 0)
    return {
        "admin": round_money(admin),
        "salesman": round_money(salesman),
        "franchise": round_money(franchise),
        "plan_amount": round_money(plans),
    }


def actual_commission_tile(db: Session, user) -> dict:
    """Salesman: this calendar month. Admin/franchise: all-time total for their scope."""
    scope = _wallet_scope(db, user)
    today = date.today()
    blank = {
        "total": 0.0,
        "plans": 0.0,
        "bookings": 0.0,
        "label": today.strftime("%b %Y"),
        "title": "Actual commission",
        "is_total": False,
        "admin_cut": 0.0,
        "franchise_cut": 0.0,
    }
    if scope["empty"]:
        return blank
    if getattr(user, "role", None) in (UserRole.ADMIN, UserRole.FRANCHISE):
        sums = _sum_ledger(db, scope)
        split = _sum_franchise_split(db, scope["franchise_id"]) if scope["franchise_id"] else {}
        return {
            **sums,
            "label": "All time" if user.role == UserRole.ADMIN else "Area collections",
            "title": "Team commission" if user.role == UserRole.FRANCHISE else "Total commission",
            "is_total": True,
            "admin_cut": split.get("admin", 0.0),
            "franchise_cut": split.get("franchise", 0.0),
        }
    start, end = current_period_bounds("month", today)
    sums = _sum_ledger(db, scope, start, end)
    return {
        **sums,
        "label": today.strftime("%b %Y"),
        "title": "Actual commission",
        "is_total": False,
        "admin_cut": 0.0,
        "franchise_cut": 0.0,
    }


def _page_numbers(current: int, pages: int) -> list:
    if pages <= 7:
        return list(range(1, pages + 1))
    pages_out = []
    if current <= 4:
        pages_out.extend(range(1, 6))
        pages_out.extend(["…", pages])
    elif current >= pages - 3:
        pages_out.extend([1, "…"])
        pages_out.extend(range(pages - 4, pages + 1))
    else:
        pages_out.extend([1, "…", current - 1, current, current + 1, "…", pages])
    return pages_out


def client_commission_report(
    db: Session,
    user,
    year: int,
    month: int = 0,
    page: int = 1,
    per_page: int = DEFAULT_COMMISSION_PAGE_SIZE,
) -> dict:
    """Client-wise commission for a year, or one month of that year."""
    today = date.today()
    year = int(year or today.year)
    month = int(month or 0)
    if month < 0 or month > 12:
        month = 0
    scope = _wallet_scope(db, user)
    empty = scope["empty"]

    year_q = db.query(func.min(WalletLedger.created_at), func.max(WalletLedger.created_at))
    year_q = _filter_wallet_ledger(year_q, scope)
    bounds = year_q.first() if not empty else (None, None)
    years = {today.year, today.year - 1}
    for stamp in bounds or ():
        if stamp:
            years.add(stamp.year if hasattr(stamp, "year") else stamp.year)
    year_options = sorted(years)
    if year not in year_options:
        year_options.append(year)
        year_options = sorted(set(year_options))

    if month:
        since = date(year, month, 1)
        until = date(year, month, monthrange(year, month)[1])
        period_label = since.strftime("%b %Y")
    else:
        since = date(year, 1, 1)
        until = date(year, 12, 31)
        period_label = str(year)

    grouped = {}
    if not empty:
        q = (
            db.query(WalletLedger, SalesExecutive, BusinessConfigModel)
            .join(SalesExecutive, SalesExecutive.id == WalletLedger.sales_executive_id)
            .outerjoin(BusinessConfigModel, BusinessConfigModel.key == WalletLedger.business_key)
            .filter(WalletLedger.created_at >= datetime.combine(since, datetime.min.time()))
            .filter(WalletLedger.created_at < datetime.combine(until + timedelta(days=1), datetime.min.time()))
        )
        if scope["exec_id"]:
            q = q.filter(WalletLedger.sales_executive_id == scope["exec_id"])
        elif scope["franchise_id"]:
            q = q.filter(SalesExecutive.franchise_id == scope["franchise_id"])
        for entry, executive, business in q.all():
            key = entry.business_key or "—"
            rec = grouped.get(key)
            if not rec:
                rec = {
                    "business_key": key,
                    "name": (business.name if business else "") or key,
                    "salesman_name": executive.name,
                    "plan": 0.0,
                    "bookings": 0.0,
                    "credits": 0,
                    "base_amount": 0.0,
                }
                grouped[key] = rec
            rec["credits"] += 1
            rec["base_amount"] = round_money(rec["base_amount"] + float(entry.plan_amount or 0))
            amount = float(entry.commission_amount or 0)
            if entry.booking_id:
                rec["bookings"] = round_money(rec["bookings"] + amount)
            else:
                rec["plan"] = round_money(rec["plan"] + amount)

    rows = []
    for rec in grouped.values():
        rec["total"] = round_money(rec["plan"] + rec["bookings"])
        rows.append(rec)
    rows.sort(key=lambda r: (-r["total"], r["name"].lower()))
    per_page = per_page if per_page in COMMISSION_PAGE_SIZES else DEFAULT_COMMISSION_PAGE_SIZE
    page = max(1, int(page or 1))
    client_count = len(rows)
    pages = ceil(client_count / per_page) if client_count else 0
    if pages:
        page = min(page, pages)
    start = (page - 1) * per_page + 1 if client_count else 0
    end = min(page * per_page, client_count)
    page_rows = rows[start - 1 : end] if client_count else []

    split = (
        _sum_franchise_split(db, scope["franchise_id"], since, until)
        if scope.get("franchise_id")
        else {"admin": 0.0, "salesman": 0.0, "franchise": 0.0, "plan_amount": 0.0}
    )
    totals = {
        "plan": round_money(sum(r["plan"] for r in rows)),
        "bookings": round_money(sum(r["bookings"] for r in rows)),
        "total": round_money(sum(r["total"] for r in rows)),
        "clients": client_count,
        "base_amount": round_money(sum(r["base_amount"] for r in rows)),
        "admin_cut": split["admin"],
        "franchise_cut": split["franchise"],
        "salesman_cut": split["salesman"] or round_money(sum(r["plan"] for r in rows)),
    }
    months = [{"value": m, "label": date(2000, m, 1).strftime("%B")} for m in range(1, 13)]
    return {
        "year": year,
        "month": month,
        "period_label": period_label,
        "year_options": year_options,
        "months": months,
        "rows": page_rows,
        "totals": totals,
        "page": page,
        "pages": pages,
        "per_page": per_page,
        "page_sizes": COMMISSION_PAGE_SIZES,
        "page_numbers": _page_numbers(page, pages) if pages else [],
        "start": start,
        "end": end,
    }


def mask_account(number: str) -> str:
    digits = "".join(ch for ch in (number or "") if ch.isdigit())
    if len(digits) < 4:
        return "—"
    return "•••• " + digits[-4:]


def list_withdrawals(
    db: Session,
    sales_executive_id: Optional[int] = None,
    franchise_id: Optional[int] = None,
    since: Optional[date] = None,
    until: Optional[date] = None,
    status: Optional[str] = None,
) -> list[dict]:
    q = db.query(WalletWithdrawal, SalesExecutive).join(
        SalesExecutive, SalesExecutive.id == WalletWithdrawal.sales_executive_id
    )
    if sales_executive_id:
        q = q.filter(WalletWithdrawal.sales_executive_id == sales_executive_id)
    if franchise_id:
        q = q.filter(SalesExecutive.franchise_id == franchise_id)
    if status:
        q = q.filter(WalletWithdrawal.status == status)
    if since:
        q = q.filter(WalletWithdrawal.created_at >= datetime.combine(since, datetime.min.time()))
    if until:
        q = q.filter(WalletWithdrawal.created_at < datetime.combine(until + timedelta(days=1), datetime.min.time()))
    rows = q.order_by(WalletWithdrawal.created_at.desc(), WalletWithdrawal.id.desc()).all()
    labels = {"requested": "Requested", "sent": "Transferred", "rejected": "Rejected"}
    result = []
    for entry, executive in rows:
        result.append(
            {
                "id": entry.id,
                "sales_executive_id": executive.id,
                "executive_name": executive.name,
                "amount": round_money(entry.amount),
                "bank_name": entry.bank_name,
                "account_name": entry.account_name,
                "account_masked": mask_account(entry.account_number),
                "ifsc": entry.ifsc,
                "status": entry.status,
                "status_label": labels.get(entry.status, entry.status),
                "note": entry.note,
                "created_at": entry.created_at.strftime("%Y-%m-%d %H:%M") if entry.created_at else "",
                "processed_at": entry.processed_at.strftime("%Y-%m-%d %H:%M") if entry.processed_at else "",
            }
        )
    return result


def request_wallet_transfer(
    db: Session,
    executive: SalesExecutive,
    amount: float,
    bank: dict,
    requested_by_user_id: Optional[int] = None,
) -> WalletWithdrawal:
    """Hold wallet cash and raise a bank-transfer request for admin."""
    money = round_money(amount)
    balance = round_money(executive.wallet_balance)
    if money <= 0:
        raise ValueError("Enter an amount greater than zero.")
    if money > balance:
        raise ValueError("Amount is more than the available wallet balance.")
    if not bank or not bank.get("account_number") or not bank.get("ifsc"):
        raise ValueError("Admin must add account number and IFSC for this user first.")

    executive.wallet_balance = round_money(balance - money)
    row = WalletWithdrawal(
        sales_executive_id=executive.id,
        amount=money,
        bank_name=bank.get("bank_name") or "",
        account_name=bank.get("account_name") or executive.name,
        account_number=bank["account_number"],
        ifsc=bank["ifsc"],
        status="requested",
        note=f"Transfer request · {bank['ifsc']}",
        requested_by_user_id=requested_by_user_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def transfer_wallet_to_bank(
    db: Session,
    withdrawal_id: int,
    processed_by_user_id: Optional[int] = None,
) -> WalletWithdrawal:
    """Admin sends a requested amount to the salesman's saved bank account."""
    row = db.query(WalletWithdrawal).filter(WalletWithdrawal.id == withdrawal_id).first()
    if not row:
        raise ValueError("Transfer request not found.")
    if row.status == "sent":
        return row
    if row.status != "requested":
        raise ValueError("Only requested transfers can be sent to the bank.")
    row.status = "sent"
    row.note = f"Transferred to bank · {row.ifsc}"
    row.processed_by_user_id = processed_by_user_id
    row.processed_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return row


def reject_wallet_transfer(
    db: Session,
    withdrawal_id: int,
    processed_by_user_id: Optional[int] = None,
) -> WalletWithdrawal:
    """Admin rejects a request and returns the amount to the wallet."""
    row = db.query(WalletWithdrawal).filter(WalletWithdrawal.id == withdrawal_id).first()
    if not row:
        raise ValueError("Transfer request not found.")
    if row.status != "requested":
        raise ValueError("Only requested transfers can be rejected.")
    executive = db.query(SalesExecutive).filter(SalesExecutive.id == row.sales_executive_id).first()
    if executive:
        executive.wallet_balance = round_money(float(executive.wallet_balance or 0) + float(row.amount or 0))
    row.status = "rejected"
    row.note = "Request rejected · returned to wallet"
    row.processed_by_user_id = processed_by_user_id
    row.processed_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return row


def withdraw_wallet_to_bank(
    db: Session,
    executive: SalesExecutive,
    amount: float,
    bank_name: str,
    account_name: str,
    account_number: str,
    ifsc: str,
    processed_by_user_id: Optional[int] = None,
) -> WalletWithdrawal:
    """Admin transfers wallet cash to the bank immediately."""
    money = round_money(amount)
    balance = round_money(executive.wallet_balance)
    if money <= 0:
        raise ValueError("Enter an amount greater than zero.")
    if money > balance:
        raise ValueError("Amount is more than the available wallet balance.")

    executive.wallet_balance = round_money(balance - money)
    now = datetime.utcnow()
    row = WalletWithdrawal(
        sales_executive_id=executive.id,
        amount=money,
        bank_name=bank_name,
        account_name=account_name,
        account_number=account_number,
        ifsc=ifsc,
        status="sent",
        note=f"Transferred to bank · {ifsc}",
        processed_by_user_id=processed_by_user_id,
        processed_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
