"""Customer bookings, sales-executive commission, and dashboard stats."""

from datetime import date, datetime
from math import ceil
from typing import Optional

from sqlalchemy.orm import Session

from models.domain_models import Booking, BusinessConfigModel, Customer, SalesExecutive, User, UserRole
from services.auth_service import hash_password
from services.plan_service import CLIENT_PLAN_NOTE, PLAN_COMMISSION_RATE


SALES_PEOPLE = (
    ("sales", "sales123", "Rahul Sharma", "9876500001", 10.0),
    ("priya", "priya123", "Priya Patel", "9876500002", 10.0),
    ("amit", "amit123", "Amit Kulkarni", "9876500003", 10.0),
    ("sneha", "sneha123", "Sneha Deshmukh", "9876500004", 10.0),
    ("vikram", "vikram123", "Vikram Joshi", "9876500005", 10.0),
)

SALES_PAGE_SIZES = (12, 24, 48, 96)
DEFAULT_SALES_PAGE_SIZE = 12
DUMMY_SEED_AMOUNT = 14000.0  # original seed bookings were ₹14,000–₹125,000


def round_money(value: float) -> float:
    return round(float(value or 0), 2)


def calc_commission(amount: float, rate: float = PLAN_COMMISSION_RATE) -> float:
    return round_money(float(amount or 0) * float(rate or 0) / 100.0)


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


def seed_sales_data(db: Session) -> None:
    """Create sales executives if none exist. Client rows come from real businesses."""
    if db.query(SalesExecutive).count() > 0:
        return

    sales_user = db.query(User).filter(User.username == "sales").first()
    executives = [
        SalesExecutive(
            name="Rahul Sharma",
            phone="9876500001",
            commission_rate=PLAN_COMMISSION_RATE,
            user_id=sales_user.id if sales_user else None,
        ),
        SalesExecutive(name="Priya Patel", phone="9876500002", commission_rate=PLAN_COMMISSION_RATE),
        SalesExecutive(name="Amit Kulkarni", phone="9876500003", commission_rate=PLAN_COMMISSION_RATE),
        SalesExecutive(name="Sneha Deshmukh", phone="9876500004", commission_rate=PLAN_COMMISSION_RATE),
    ]
    db.add_all(executives)
    db.commit()


def ensure_sales_users(db: Session) -> None:
    """Ensure 5 sales-role users exist in Postgres and are linked to executives."""
    import logging

    created = []
    for username, password, name, phone, rate in SALES_PEOPLE:
        user = db.query(User).filter(User.username == username).first()
        if not user:
            user = User(
                username=username,
                password_hash=hash_password(password),
                role=UserRole.SALES,
                is_active=True,
            )
            db.add(user)
            db.flush()
            created.append(username)
        else:
            user.role = UserRole.SALES
            user.is_active = True

        executive = (
            db.query(SalesExecutive).filter(SalesExecutive.name == name).first()
            or db.query(SalesExecutive).filter(SalesExecutive.user_id == user.id).first()
        )
        if not executive:
            executive = SalesExecutive(
                name=name,
                phone=phone,
                commission_rate=rate,
                wallet_balance=0.0,
                user_id=user.id,
                is_active=True,
            )
            db.add(executive)
        else:
            executive.user_id = user.id
            executive.name = name
            executive.commission_rate = rate
            if not executive.phone:
                executive.phone = phone
            executive.is_active = True
    db.commit()
    if created:
        logging.getLogger(__name__).info("Created sales users: %s", ", ".join(created))


def get_executive_for_user(db: Session, user: User) -> Optional[SalesExecutive]:
    if not user:
        return None
    return (
        db.query(SalesExecutive)
        .filter(SalesExecutive.user_id == user.id, SalesExecutive.is_active.is_(True))
        .first()
    )


def list_executives(db: Session, active_only: bool = True) -> list[SalesExecutive]:
    q = db.query(SalesExecutive)
    if active_only:
        q = q.filter(SalesExecutive.is_active.is_(True))
    return q.order_by(SalesExecutive.name.asc()).all()


def list_sales_clients(db: Session, executive_id: Optional[int] = None) -> list[dict]:
    q = db.query(BusinessConfigModel).order_by(BusinessConfigModel.name.asc(), BusinessConfigModel.key.asc())
    if executive_id:
        q = q.filter(BusinessConfigModel.sales_executive_id == executive_id)
    rows = []
    for b in q.all():
        rows.append(
            {
                "key": b.key,
                "name": b.name,
                "mobile": b.mobile or "",
                "plan_code": b.plan_code or "",
                "plan_amount": round_money(b.plan_amount),
                "sales_executive_id": b.sales_executive_id,
            }
        )
    return rows


def _serialize_booking(booking: Booking, customer: Customer, executive: SalesExecutive, business_name: str = "") -> dict:
    amount = round_money(booking.amount)
    collected = round_money(booking.collected_amount)
    pending = round_money(max(amount - collected, 0))
    return {
        "id": booking.id,
        "booked_on": booking.booked_on.isoformat() if booking.booked_on else "",
        "customer_name": customer.name,
        "customer_phone": customer.phone,
        "business_key": booking.business_key,
        "business_name": business_name or customer.name,
        "booking_type": booking.booking_type,
        "executive_id": executive.id,
        "executive_name": executive.name,
        "amount": amount,
        "collected_amount": collected,
        "pending": pending,
        "commission_rate": round_money(booking.commission_rate),
        "commission_amount": round_money(booking.commission_amount),
        "status": booking.status,
        "is_plan": (booking.notes or "").strip() == CLIENT_PLAN_NOTE,
    }


def _bookings_query(
    db: Session,
    business_key: str = "",
    executive_id: Optional[int] = None,
    booking_type: str = "",
    search: str = "",
):
    q = (
        db.query(Booking, Customer, SalesExecutive, BusinessConfigModel)
        .join(Customer, Customer.id == Booking.customer_id)
        .join(SalesExecutive, SalesExecutive.id == Booking.sales_executive_id)
        .outerjoin(BusinessConfigModel, BusinessConfigModel.key == Booking.business_key)
        .filter(Booking.status != "cancelled")
    )
    if business_key:
        q = q.filter(Booking.business_key == business_key)
    if executive_id:
        q = q.filter(Booking.sales_executive_id == executive_id)
    if booking_type in ("new", "renewal"):
        q = q.filter(Booking.booking_type == booking_type)
    if search:
        like = f"%{search.strip()}%"
        q = q.filter(
            Customer.name.ilike(like)
            | Customer.phone.ilike(like)
            | Booking.business_key.ilike(like)
            | BusinessConfigModel.name.ilike(like)
        )
    return q


def list_bookings(
    db: Session,
    business_key: str = "",
    executive_id: Optional[int] = None,
    booking_type: str = "",
    search: str = "",
    page: int = 1,
    per_page: int = DEFAULT_SALES_PAGE_SIZE,
) -> dict:
    q = _bookings_query(db, business_key, executive_id, booking_type, search)
    rows = q.order_by(Booking.booked_on.desc(), Booking.id.desc()).all()
    items = [
        _serialize_booking(booking, customer, executive, business.name if business else "")
        for booking, customer, executive, business in rows
    ]
    per_page = per_page if per_page in SALES_PAGE_SIZES else DEFAULT_SALES_PAGE_SIZE
    page = max(1, int(page or 1))
    total = len(items)
    pages = ceil(total / per_page) if total else 0
    if pages:
        page = min(page, pages)
    start = (page - 1) * per_page + 1 if total else 0
    end = min(page * per_page, total)
    page_rows = items[start - 1 : end] if total else []
    return {
        "rows": page_rows,
        "total": total,
        "page": page,
        "pages": pages,
        "per_page": per_page,
        "page_sizes": SALES_PAGE_SIZES,
        "page_numbers": _page_numbers(page, pages) if pages else [],
        "start": start,
        "end": end,
    }


def upsert_plan_booking(db: Session, business: BusinessConfigModel, *, commit: bool = False) -> Optional[Booking]:
    """Keep one sales-book row per client at the plan amount (10% commission, no extra wallet credit)."""
    if not business or not business.plan_amount or not business.sales_executive_id:
        return None
    executive = db.query(SalesExecutive).filter(SalesExecutive.id == business.sales_executive_id).first()
    if not executive:
        return None

    amount = round_money(business.plan_amount)
    rate = PLAN_COMMISSION_RATE
    booked_on = business.join_date or date.today()
    phone = business.mobile or ""
    from services.payment_service import razorpay_configured

    collect_now = not razorpay_configured()

    existing = (
        db.query(Booking)
        .filter(Booking.business_key == business.key, Booking.notes == CLIENT_PLAN_NOTE, Booking.status != "cancelled")
        .order_by(Booking.id.asc())
        .first()
    )
    if existing:
        customer = db.query(Customer).filter(Customer.id == existing.customer_id).first()
        if customer:
            customer.name = business.name
            customer.phone = phone or customer.phone
            customer.business_key = business.key
            customer.sales_executive_id = executive.id
        existing.sales_executive_id = executive.id
        existing.business_key = business.key
        existing.booking_type = "new"
        existing.amount = amount
        existing.commission_rate = rate
        existing.commission_amount = calc_commission(amount, rate)
        existing.notes = CLIENT_PLAN_NOTE
        existing.booked_on = booked_on
        if collect_now or (existing.collected_amount or 0) >= amount:
            existing.collected_amount = amount
            existing.status = "collected"
        else:
            existing.status = "booked"
        if commit:
            db.commit()
            db.refresh(existing)
        return existing

    customer = Customer(
        name=business.name,
        phone=phone,
        business_key=business.key,
        sales_executive_id=executive.id,
    )
    db.add(customer)
    db.flush()
    booking = Booking(
        customer_id=customer.id,
        sales_executive_id=executive.id,
        business_key=business.key,
        booking_type="new",
        amount=amount,
        collected_amount=amount if collect_now else 0.0,
        commission_rate=rate,
        commission_amount=calc_commission(amount, rate),
        status="collected" if collect_now else "booked",
        notes=CLIENT_PLAN_NOTE,
        booked_on=booked_on,
    )
    db.add(booking)
    db.flush()
    if commit:
        db.commit()
        db.refresh(booking)
    return booking


def align_sales_book_to_plans(db: Session) -> dict:
    """Replace dummy seed bookings with one row per client at that client's plan amount."""
    from services.plan_service import reverse_booking_wallet_credit

    removed = 0
    dummy_customer_ids = set()
    dummy_rows = (
        db.query(Booking, Customer)
        .join(Customer, Customer.id == Booking.customer_id)
        .filter(Booking.status != "cancelled")
        .all()
    )
    for booking, customer in dummy_rows:
        if (booking.notes or "").strip() == CLIENT_PLAN_NOTE:
            continue
        phone = (customer.phone or "").strip()
        leftover_dummy = (
            booking.amount >= DUMMY_SEED_AMOUNT
            and (booking.notes or "") == ""
            and phone.startswith("98220111")
        )
        if not leftover_dummy:
            continue
        dummy_customer_ids.add(booking.customer_id)
        reverse_booking_wallet_credit(db, booking, commit=False)
        db.delete(booking)
        removed += 1

    created = 0
    updated = 0
    businesses = (
        db.query(BusinessConfigModel)
        .filter(BusinessConfigModel.plan_amount > 0, BusinessConfigModel.sales_executive_id.isnot(None))
        .all()
    )
    for business in businesses:
        existed = (
            db.query(Booking)
            .filter(Booking.business_key == business.key, Booking.notes == CLIENT_PLAN_NOTE, Booking.status != "cancelled")
            .first()
        )
        if upsert_plan_booking(db, business, commit=False):
            if existed:
                updated += 1
            else:
                created += 1

    for cid in dummy_customer_ids:
        still_used = db.query(Booking).filter(Booking.customer_id == cid).first()
        if still_used:
            continue
        customer = db.query(Customer).filter(Customer.id == cid).first()
        if customer:
            db.delete(customer)

    db.commit()
    return {"removed": removed, "created": created, "updated": updated}


def save_booking(db: Session, data: dict) -> Booking:
    from services.plan_service import credit_booking_to_wallet

    executive = db.query(SalesExecutive).filter(SalesExecutive.id == int(data["sales_executive_id"])).first()
    if not executive:
        raise ValueError("Sales executive not found.")

    business_key = (data.get("business_key") or "").strip()
    business = db.query(BusinessConfigModel).filter(BusinessConfigModel.key == business_key).first() if business_key else None
    customer_name = (data.get("customer_name") or "").strip() or (business.name if business else "")
    if not customer_name:
        raise ValueError("Customer name is required.")
    phone = (data.get("customer_phone") or "").strip() or (business.mobile if business else "")
    if not business_key:
        business_key = "technobuzz"

    customer = Customer(
        name=customer_name,
        phone=phone,
        email=(data.get("customer_email") or "").strip(),
        business_key=business_key,
        sales_executive_id=executive.id,
    )
    db.add(customer)
    db.flush()

    amount = round_money(data.get("amount") or 0)
    if amount <= 0 and business and business.plan_amount:
        amount = round_money(business.plan_amount)
    collected = round_money(data.get("collected_amount") or 0)
    rate = data.get("commission_rate")
    if rate in (None, ""):
        rate = PLAN_COMMISSION_RATE
    rate = round_money(rate)
    booked_on = data.get("booked_on") or date.today()
    if isinstance(booked_on, str):
        booked_on = datetime.strptime(booked_on, "%Y-%m-%d").date()

    booking_type = data.get("booking_type") or "new"
    if booking_type not in ("new", "renewal"):
        booking_type = "new"

    status = "collected" if collected >= amount and amount > 0 else "booked"
    booking = Booking(
        customer_id=customer.id,
        sales_executive_id=executive.id,
        business_key=customer.business_key,
        booking_type=booking_type,
        amount=amount,
        collected_amount=min(collected, amount) if amount else collected,
        commission_rate=rate,
        commission_amount=calc_commission(amount, rate),
        status=status,
        notes=(data.get("notes") or "").strip(),
        booked_on=booked_on,
    )
    db.add(booking)
    db.flush()
    credit_booking_to_wallet(db, booking, commit=False)
    db.commit()
    db.refresh(booking)
    return booking


def sales_stats(db: Session, business_key: str = "", executive_id: Optional[int] = None) -> dict:
    q = db.query(Booking).filter(Booking.status != "cancelled")
    if business_key:
        q = q.filter(Booking.business_key == business_key)
    if executive_id:
        q = q.filter(Booking.sales_executive_id == executive_id)

    bookings = q.all()
    total_sales = round_money(sum(b.amount or 0 for b in bookings))
    total_collection = round_money(sum(b.collected_amount or 0 for b in bookings))
    total_commission = round_money(sum(b.commission_amount or 0 for b in bookings))
    pending = round_money(max(total_sales - total_collection, 0))
    collection_pct = round((total_collection / total_sales * 100), 1) if total_sales else 0.0

    customer_ids = {b.customer_id for b in bookings}
    client_keys = {b.business_key for b in bookings if b.business_key}
    renewal_ids = {b.customer_id for b in bookings if b.booking_type == "renewal"}
    new_count = len(customer_ids - renewal_ids)

    executives = list_executives(db)
    exec_rows = []
    for exec_ in executives:
        e_bookings = [b for b in bookings if b.sales_executive_id == exec_.id]
        sales = round_money(sum(b.amount or 0 for b in e_bookings))
        collected = round_money(sum(b.collected_amount or 0 for b in e_bookings))
        commission = round_money(sum(b.commission_amount or 0 for b in e_bookings))
        share = round((sales / total_sales * 100), 1) if total_sales else 0.0
        exec_rows.append(
            {
                "id": exec_.id,
                "name": exec_.name,
                "commission_rate": PLAN_COMMISSION_RATE,
                "bookings": len(e_bookings),
                "sales": sales,
                "collected": collected,
                "pending": round_money(max(sales - collected, 0)),
                "commission": commission,
                "booking_commission": commission,
                "sales_share_pct": share,
                "collection_pct": round((collected / sales * 100), 1) if sales else 0.0,
                "wallet_balance": round_money(exec_.wallet_balance),
            }
        )
    exec_rows.sort(key=lambda r: (r["commission"], r["sales"]), reverse=True)
    for i, row in enumerate(exec_rows, start=1):
        row["rank"] = i
    top_executives = [r for r in exec_rows if r["sales"] or r["commission"]][:5]
    visible_rows = [r for r in exec_rows if (not executive_id or r["id"] == executive_id)]
    mine = next((r for r in exec_rows if r["id"] == executive_id), None) if executive_id else None

    return {
        "total_customers": len(client_keys) or len(customer_ids),
        "renewal_customers": len(renewal_ids),
        "new_customers": new_count,
        "total_bookings": len(bookings),
        "total_sales": total_sales,
        "total_collection": total_collection,
        "pending_collection": pending,
        "collection_pct": collection_pct,
        "total_commission": total_commission,
        "total_wallet": round_money(sum(r["wallet_balance"] for r in exec_rows)),
        "my_commission": mine["commission"] if mine else (total_commission if not executive_id else 0),
        "my_rank": mine["rank"] if mine else None,
        "my_name": mine["name"] if mine else None,
        "top_executives": top_executives,
        "executives": visible_rows,
    }
