"""
routes/admin_routes.py
-----------------------
Admin dashboard, login, and business management (FastAPI).
Business CRUD is limited to admin and sales roles.
"""

import csv
import io
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from config import get_config
from database import get_db
from models.domain_models import User, UserRole
from services.auth_service import (
    ROLE_LABELS,
    authenticate_user,
    bank_details_for_executive,
    can_manage_businesses,
    can_manage_users,
    can_view_sales_book,
    get_session_user,
    list_users,
    login_user,
    logout_user,
    require_admin,
    require_business_access,
    require_login,
    require_sales_book,
    safe_next_url,
    save_staff_user,
    serialize_user,
)
from services.business_service import (
    BUSINESS_PAGE_SIZES,
    get_business,
    list_businesses_page,
    save_business,
    user_owns_business,
)
from services.dashboard_service import (
    actual_commission_tile,
    client_commission_report,
    list_pending_payments,
    list_renewal_clients,
    list_withdrawals,
    overview_counts,
    reject_wallet_transfer,
    request_wallet_transfer,
    transfer_wallet_to_bank,
    withdraw_wallet_to_bank,
)
from services.plan_service import PLANS, PLAN_COMMISSION_RATE
from services.sales_service import (
    DEFAULT_SALES_PAGE_SIZE,
    get_executive_for_user,
    list_bookings,
    list_executives,
    list_sales_clients,
    sales_stats,
    save_booking,
)
from models.schemas import GenerateExamplesRequest
from services.gemini_service import generate_star_example_prompts
from services.storage_service import public_logo_url, s3_configured, save_logo_upload
from utils.google_review import build_google_review_url, extract_place_id
from utils.validators import (
    sanitize_string,
    validate_account_number,
    validate_aadhaar,
    validate_email,
    validate_ifsc,
    validate_mobile,
    validate_withdraw_amount,
)

router = APIRouter(prefix="/admin")
config = get_config()
logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="templates")


def _admin_context(request: Request, user: User, **extra):
    ctx = {
        "request": request,
        "user": user,
        "is_admin": user.role == UserRole.ADMIN,
        "can_manage_businesses": can_manage_businesses(user),
        "can_manage_users": can_manage_users(user),
        "can_view_sales_book": can_view_sales_book(user),
        "role_labels": ROLE_LABELS,
        "plans": PLANS,
        "plan_commission_rate": PLAN_COMMISSION_RATE,
        "company_name": config.COMPANY_NAME,
        "company_id": config.COMPANY_ID,
        "business_id": "technobuzz",
        "feedback_path": "/feedback",
        "s3_ready": s3_configured(),
        "businesses": {},
        "active_nav": "overview",
        "page_title": "Overview",
    }
    ctx.update(extra)
    return ctx


def _render_business_form(
    request: Request,
    user: User,
    db: Session,
    *,
    business,
    business_key: str,
    error: str = "",
):
    own = get_executive_for_user(db, user)
    is_edit = bool(business_key and business and business.get("key"))
    if business is not None:
        business = dict(business)
        business["logo_url"] = public_logo_url(business.get("logo_filename") or "")
    return templates.TemplateResponse(
        "admin_business_form.html",
        _admin_context(
            request,
            user,
            business=business,
            business_key=business_key,
            executives=list_executives(db),
            locked_executive=own if user.role != UserRole.ADMIN else None,
            form_error=error,
            feedback_path=(business or {}).get("feedback_path") or "/feedback",
            s3_ready=s3_configured(),
            page_title="Edit Business" if is_edit else "Add Business",
            active_nav="businesses",
        ),
        status_code=400 if error else 200,
    )


def _visible_business_key(db: Session, user: User, business: str) -> tuple[str, dict]:
    """Resolve one allowed business without loading the full catalog."""
    if business and user_owns_business(db, user, business):
        biz = get_business(db, business)
        if biz:
            return business, {business: biz}
    page = list_businesses_page(db, user=user, page=1, per_page=12)
    if page["rows"]:
        item = page["rows"][0]
        key = item["key"]
        return key, {key: item}
    return business, {}


# ─── Auth ─────────────────────────────────────────────────────────────────────
@router.get("/login")
def login_page(request: Request, next: str = "/admin", error: str = "", db: Session = Depends(get_db)):
    if get_session_user(request, db):
        return RedirectResponse(url=safe_next_url(next), status_code=302)
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "next": safe_next_url(next), "error": error},
    )


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/admin"),
    db: Session = Depends(get_db),
):
    user = authenticate_user(db, username, password)
    if not user:
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "next": safe_next_url(next),
                "error": "Invalid username or password.",
                "username": username,
            },
            status_code=401,
        )
    login_user(request, user)
    return RedirectResponse(url=safe_next_url(next), status_code=302)


@router.get("/logout")
def logout(request: Request):
    logout_user(request)
    return RedirectResponse(url="/admin/login", status_code=302)


@router.get("/forbidden")
def forbidden(request: Request, user: User = Depends(require_login)):
    return templates.TemplateResponse(
        "forbidden.html",
        _admin_context(request, user, page_title="Access Denied", active_nav=""),
        status_code=403,
    )


# ─── GET /admin ────────────────────────────────────────────────────────────────
@router.get("")
@router.get("/")
def admin_dashboard(
    request: Request,
    business: str = "technobuzz",
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    """Render the operations overview: renewals, unpaid clients, and wallet summary."""
    business, businesses = _visible_business_key(db, user, business)
    b_config = businesses.get(business) or {}
    renewals = list_renewal_clients(db, user)
    pending = list_pending_payments(db, user)
    counts = overview_counts(db, user)
    commission_tile = actual_commission_tile(db, user)

    return templates.TemplateResponse(
        "admin.html",
        _admin_context(
            request,
            user,
            company_name=b_config.get("name", config.COMPANY_NAME),
            company_id=b_config.get("id", config.COMPANY_ID),
            business_id=business,
            businesses=businesses,
            feedback_path=b_config.get("feedback_path") or "/feedback",
            renewals=renewals,
            pending=pending,
            overview_counts=counts,
            commission_tile=commission_tile,
            page_title="Overview",
            active_nav="overview",
        ),
    )


# ─── GET /admin/api/stats ──────────────────────────────────────────────────────
@router.get("/api/stats")
def api_stats(
    business: str = "technobuzz",
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    """Return aggregate feedback statistics as JSON (Mocked)."""
    business, _ = _visible_business_key(db, user, business)
    return {
        "total": 0,
        "average_rating": 0.0,
        "by_rating": {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0},
    }


# ─── GET /admin/api/feedback ───────────────────────────────────────────────────
@router.get("/api/feedback")
def api_feedback(
    page: int = 1,
    limit: int = 10,
    rating: int = None,
    search: str = "",
    business: str = "technobuzz",
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    """Return paginated, filtered feedback records as JSON (Mocked)."""
    business, _ = _visible_business_key(db, user, business)
    page = max(1, page)
    return {
        "records": [],
        "total": 0,
        "page": page,
        "pages": 0,
    }


# ─── GET /admin/api/export ────────────────────────────────────────────────────
@router.get("/api/export")
def api_export(
    business: str = "technobuzz",
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    """Export all feedback records as a downloadable CSV file (Mocked - Empty)."""
    business, _ = _visible_business_key(db, user, business)
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["ID", "Company", "Company ID", "Rating", "Feedback", "Date", "Time"])

    output.seek(0)
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=technobuzz_feedback.csv"},
    )


@router.post("/api/generate-examples")
def api_generate_examples(
    payload: GenerateExamplesRequest,
    user: User = Depends(require_business_access),
):
    """Fill 1–5 star prompt example themes from business name and scope."""
    try:
        examples = generate_star_example_prompts(payload.name, payload.scope)
        return {"examples": examples}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error("generate-examples failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Could not generate examples. Please try again.",
        ) from e


# ─── Business Management Routes (admin + sales only) ──────────────────────────
@router.get("/businesses")
def list_businesses(
    request: Request,
    q: str = "",
    page: int = 1,
    per_page: int = 24,
    db: Session = Depends(get_db),
    user: User = Depends(require_business_access),
):
    listing = list_businesses_page(db, user=user, search=q, page=page, per_page=per_page)
    first = listing["rows"][0] if listing["rows"] else None
    return templates.TemplateResponse(
        "admin_businesses.html",
        _admin_context(
            request,
            user,
            listing=listing,
            search_q=listing["search"],
            page_sizes=BUSINESS_PAGE_SIZES,
            business_id=first["key"] if first else "technobuzz",
            feedback_path=(first or {}).get("feedback_path") or "/feedback",
            page_title="Your Clients" if user.role != UserRole.ADMIN else "Registered Businesses",
            active_nav="businesses",
        ),
    )


@router.get("/businesses/new")
def new_business(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_business_access),
):
    own = get_executive_for_user(db, user)
    if user.role != UserRole.ADMIN and not own:
        return RedirectResponse(url="/admin/forbidden", status_code=302)
    return templates.TemplateResponse(
        "admin_business_form.html",
        _admin_context(
            request,
            user,
            business=None,
            business_key="",
            executives=list_executives(db),
            locked_executive=own if user.role != UserRole.ADMIN else None,
            page_title="Add Business",
            active_nav="businesses",
            feedback_path="/feedback",
        ),
    )


@router.get("/businesses/{business_key}/edit")
def edit_business(
    request: Request,
    business_key: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_business_access),
):
    business = get_business(db, business_key)
    if not business or not user_owns_business(db, user, business_key):
        return RedirectResponse(url="/admin/businesses", status_code=302)
    own = get_executive_for_user(db, user)
    return templates.TemplateResponse(
        "admin_business_form.html",
        _admin_context(
            request,
            user,
            business=business,
            business_key=business_key,
            executives=list_executives(db),
            locked_executive=own if user.role != UserRole.ADMIN else None,
            page_title="Edit Business",
            active_nav="businesses",
            feedback_path=business.get("feedback_path") or "/feedback",
        ),
    )


@router.post("/businesses")
def save_business_route(
    request: Request,
    business_key: str = Form(...),
    name: str = Form(...),
    id: str = Form(...),
    route_slug: str = Form(""),
    collection: str = Form(""),
    place_id: str = Form(""),
    logo_filename: str = Form(""),
    logo_file: UploadFile | None = File(None),
    remove_logo: str = Form(""),
    scope: str = Form(""),
    examples_1star: str = Form(""),
    examples_2star: str = Form(""),
    examples_3star: str = Form(""),
    examples_4star: str = Form(""),
    examples_5star: str = Form(""),
    plan_code: str = Form(""),
    join_date: str = Form(""),
    sales_executive_id: str = Form(""),
    mobile: str = Form(""),
    alternate_mobile: str = Form(""),
    email: str = Form(""),
    address: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_business_access),
):
    own = get_executive_for_user(db, user)
    if user.role != UserRole.ADMIN:
        if not own:
            return RedirectResponse(url="/admin/forbidden", status_code=302)
        existing = get_business(db, business_key.strip())
        if existing and not user_owns_business(db, user, business_key.strip()):
            return RedirectResponse(url="/admin/businesses", status_code=302)
        sales_executive_id = str(own.id)

    posted = {
        "key": business_key.strip(),
        "name": name,
        "id": id,
        "route_slug": route_slug,
        "collection": collection,
        "place_id": place_id,
        "logo_filename": logo_filename,
        "scope": scope,
        "examples_1star": examples_1star,
        "examples_2star": examples_2star,
        "examples_3star": examples_3star,
        "examples_4star": examples_4star,
        "examples_5star": examples_5star,
        "plan_code": plan_code,
        "join_date": join_date,
        "sales_executive_id": int(sales_executive_id) if str(sales_executive_id).isdigit() else None,
        "mobile": mobile,
        "alternate_mobile": alternate_mobile,
        "email": email,
        "address": address,
    }

    mobile_val, mobile_err = validate_mobile(mobile, required=True)
    alt_val, alt_err = validate_mobile(alternate_mobile, required=False, label="Alternate mobile number")
    email_val, email_err = validate_email(email, required=True)
    address_val = sanitize_string(address, 500)
    error = mobile_err or alt_err or email_err
    if not address_val:
        error = error or "Address is required."
    if mobile_val and alt_val and mobile_val == alt_val:
        error = error or "Alternate mobile number must be different from the primary mobile."

    stored_logo = (logo_filename or "").strip()
    if (remove_logo or "").strip() in {"1", "on", "true", "yes"} and not (logo_file and logo_file.filename):
        stored_logo = ""
    if not error and logo_file and logo_file.filename:
        try:
            stored_logo = save_logo_upload(logo_file, business_key.strip())
        except ValueError as exc:
            error = str(exc)
    posted["logo_filename"] = stored_logo
    posted["logo_url"] = public_logo_url(stored_logo)

    if error:
        return _render_business_form(
            request,
            user,
            db,
            business=posted,
            business_key=business_key.strip(),
            error=error,
        )

    cleaned_place_id = extract_place_id(place_id)
    business_data = {
        "name": name,
        "id": id,
        "route_slug": route_slug,
        "collection": collection,
        "place_id": cleaned_place_id,
        "google_review_url": build_google_review_url(cleaned_place_id),
        "logo_filename": stored_logo,
        "scope": scope,
        "examples_1star": examples_1star,
        "examples_2star": examples_2star,
        "examples_3star": examples_3star,
        "examples_4star": examples_4star,
        "examples_5star": examples_5star,
        "plan_code": plan_code,
        "join_date": join_date,
        "sales_executive_id": sales_executive_id,
        "mobile": mobile_val,
        "alternate_mobile": alt_val,
        "email": email_val,
        "address": address_val,
    }
    save_business(db, business_key.strip(), business_data)
    return RedirectResponse(url="/admin/businesses", status_code=303)


# ─── Sales book (admin + sales) ───────────────────────────────────────────────
@router.get("/api/sales-stats")
def api_sales_stats(
    business: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_sales_book),
):
    executive_id = None
    if user.role != UserRole.ADMIN:
        own = get_executive_for_user(db, user)
        executive_id = own.id if own else -1
    return sales_stats(db, business_key=business, executive_id=executive_id)


@router.get("/sales-book")
def sales_book(
    request: Request,
    business: str = "",
    executive_id: int = 0,
    booking_type: str = "",
    search: str = "",
    page: int = 1,
    per_page: int = DEFAULT_SALES_PAGE_SIZE,
    db: Session = Depends(get_db),
    user: User = Depends(require_sales_book),
):
    own = get_executive_for_user(db, user)
    locked_executive_id = own.id if (user.role != UserRole.ADMIN and own) else None
    filter_exec = locked_executive_id or (executive_id or None)
    listing = list_bookings(
        db,
        business_key=business,
        executive_id=filter_exec,
        booking_type=booking_type,
        search=search,
        page=page,
        per_page=per_page,
    )
    stats = sales_stats(db, business_key=business, executive_id=filter_exec)
    return templates.TemplateResponse(
        "admin_sales_book.html",
        _admin_context(
            request,
            user,
            listing=listing,
            executives=list_executives(db),
            clients=list_sales_clients(db, filter_exec if locked_executive_id else None),
            stats=stats,
            business_id=business,
            selected_executive_id=filter_exec or 0,
            selected_type=booking_type,
            search=search,
            locked_executive=bool(locked_executive_id),
            page_title="Sales Book",
            active_nav="sales",
        ),
    )


@router.get("/sales-book/new")
def new_booking(
    request: Request,
    business: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_sales_book),
):
    own = get_executive_for_user(db, user)
    locked = own if user.role != UserRole.ADMIN else None
    return templates.TemplateResponse(
        "admin_sales_form.html",
        _admin_context(
            request,
            user,
            executives=list_executives(db),
            clients=list_sales_clients(db, locked.id if locked else None),
            business_id=business,
            locked_executive=locked,
            error="",
            page_title="Add Booking",
            active_nav="sales",
        ),
    )


@router.post("/sales-book")
def save_booking_route(
    request: Request,
    customer_name: str = Form(""),
    customer_phone: str = Form(""),
    booking_type: str = Form("new"),
    sales_executive_id: int = Form(...),
    amount: float = Form(0),
    collected_amount: float = Form(0),
    commission_rate: str = Form(""),
    booked_on: str = Form(""),
    notes: str = Form(""),
    business_key: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_sales_book),
):
    own = get_executive_for_user(db, user)
    locked = own if user.role != UserRole.ADMIN else None
    if user.role != UserRole.ADMIN:
        if not own:
            return templates.TemplateResponse(
                "admin_sales_form.html",
                _admin_context(
                    request,
                    user,
                    executives=list_executives(db),
                    clients=list_sales_clients(db),
                    business_id=business_key,
                    locked_executive=None,
                    error="Your login is not linked to a sales executive profile.",
                    page_title="Add Booking",
                    active_nav="sales",
                ),
                status_code=403,
            )
        sales_executive_id = own.id

    rate_val = PLAN_COMMISSION_RATE
    if commission_rate.strip():
        rate_val = float(commission_rate)

    try:
        save_booking(
            db,
            {
                "customer_name": customer_name,
                "customer_phone": customer_phone,
                "booking_type": booking_type,
                "sales_executive_id": sales_executive_id,
                "amount": amount,
                "collected_amount": collected_amount,
                "commission_rate": rate_val,
                "booked_on": booked_on,
                "notes": notes,
                "business_key": business_key,
            },
        )
    except ValueError as exc:
        return templates.TemplateResponse(
            "admin_sales_form.html",
            _admin_context(
                request,
                user,
                executives=list_executives(db),
                clients=list_sales_clients(db, locked.id if locked else None),
                business_id=business_key,
                locked_executive=locked,
                error=str(exc),
                page_title="Add Booking",
                active_nav="sales",
            ),
            status_code=400,
        )
    return RedirectResponse(url="/admin/sales-book", status_code=302)


def _wallet_view(
    request: Request,
    db: Session,
    user: User,
    *,
    error: str = "",
    success: str = "",
    form: dict = None,
):
    own = get_executive_for_user(db, user)
    filter_exec = own.id if (user.role != UserRole.ADMIN and own) else None
    pending = list_withdrawals(db, sales_executive_id=filter_exec, status="requested")
    history = [
        w
        for w in list_withdrawals(db, sales_executive_id=filter_exec)
        if w["status"] != "requested"
    ]
    execs = list_executives(db)
    if filter_exec:
        execs = [e for e in execs if e.id == filter_exec]
    wallet_total = round(sum(float(e.wallet_balance or 0) for e in execs), 2)
    pending_total = round(sum(float(w["amount"] or 0) for w in pending), 2)
    withdrawn_total = round(sum(float(w["amount"] or 0) for w in history if w["status"] == "sent"), 2)
    commission_tile = actual_commission_tile(db, user)
    selected = None
    posted_exec = (form or {}).get("sales_executive_id")
    if posted_exec and str(posted_exec).isdigit():
        selected = next((e for e in execs if e.id == int(posted_exec)), None)
    if not selected and own:
        selected = own
    elif not selected and execs:
        selected = execs[0]
    banks = {e.id: bank_details_for_executive(db, e) for e in execs}
    bank = banks.get(selected.id) if selected else None
    return templates.TemplateResponse(
        "admin_wallet.html",
        _admin_context(
            request,
            user,
            pending_transfers=pending,
            withdrawals=history,
            executives=execs,
            wallet_total=wallet_total,
            pending_total=pending_total,
            withdrawn_total=withdrawn_total,
            commission_tile=commission_tile,
            locked_executive=own if user.role != UserRole.ADMIN else None,
            selected_executive=selected,
            bank=bank,
            banks=banks,
            form_error=error,
            form_success=success,
            form=form or {},
            page_title="Salesman Wallet",
            active_nav="wallet",
        ),
        status_code=400 if error else 200,
    )


@router.get("/wallet")
def wallet_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_sales_book),
):
    if request.query_params.get("sent"):
        success = "Amount transferred to the bank account."
    elif request.query_params.get("requested"):
        success = "Transfer request sent. Admin will transfer it to the bank."
    elif request.query_params.get("rejected"):
        success = "Request rejected. Amount returned to the wallet."
    else:
        success = ""
    return _wallet_view(request, db, user, success=success)


@router.get("/commission")
def commission_page(
    request: Request,
    year: int = 0,
    month: int = 0,
    page: int = 1,
    per_page: int = 12,
    db: Session = Depends(get_db),
    user: User = Depends(require_sales_book),
):
    from datetime import date as date_cls

    today = date_cls.today()
    year = year or today.year
    if month < 0 or month > 12:
        month = today.month
    if "month" not in request.query_params:
        month = today.month
    report = client_commission_report(
        db, user, year=year, month=month, page=page, per_page=per_page
    )
    return templates.TemplateResponse(
        "admin_commission.html",
        _admin_context(
            request,
            user,
            report=report,
            page_title="Commission",
            active_nav="commission",
        ),
    )


@router.post("/wallet/request")
def wallet_request_transfer(
    request: Request,
    amount: str = Form(""),
    sales_executive_id: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_sales_book),
):
    own = get_executive_for_user(db, user)
    posted = {"amount": amount, "sales_executive_id": sales_executive_id}
    if user.role != UserRole.ADMIN:
        if not own:
            return RedirectResponse(url="/admin/forbidden", status_code=302)
        executive = own
    else:
        exec_id = int(sales_executive_id) if str(sales_executive_id).isdigit() else 0
        executive = next((e for e in list_executives(db) if e.id == exec_id), None)
        if not executive:
            return _wallet_view(request, db, user, error="Select a salesman.", form=posted)

    bank = bank_details_for_executive(db, executive)
    money, money_err = validate_withdraw_amount(amount, executive.wallet_balance)
    error = money_err
    if not bank:
        error = error or "Admin must add account number and IFSC for this user first."
    if error:
        return _wallet_view(request, db, user, error=error, form=posted)

    try:
        request_wallet_transfer(db, executive, money, bank, requested_by_user_id=user.id)
    except ValueError as exc:
        return _wallet_view(request, db, user, error=str(exc), form=posted)
    return RedirectResponse(url="/admin/wallet?requested=1", status_code=303)


@router.post("/wallet/withdraw")
def wallet_withdraw(
    request: Request,
    amount: str = Form(""),
    sales_executive_id: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    posted = {"amount": amount, "sales_executive_id": sales_executive_id}
    exec_id = int(sales_executive_id) if str(sales_executive_id).isdigit() else 0
    executive = next((e for e in list_executives(db) if e.id == exec_id), None)
    if not executive:
        return _wallet_view(request, db, user, error="Select a salesman to transfer from.", form=posted)

    bank = bank_details_for_executive(db, executive)
    money, money_err = validate_withdraw_amount(amount, executive.wallet_balance)
    error = money_err
    if not bank:
        error = error or "Add account number and IFSC for this user first."
    if error:
        return _wallet_view(request, db, user, error=error, form=posted)

    try:
        withdraw_wallet_to_bank(
            db,
            executive,
            money,
            bank["bank_name"],
            bank["account_name"],
            bank["account_number"],
            bank["ifsc"],
            processed_by_user_id=user.id,
        )
    except ValueError as exc:
        return _wallet_view(request, db, user, error=str(exc), form=posted)
    return RedirectResponse(url="/admin/wallet?sent=1", status_code=303)


@router.post("/wallet/withdrawals/{withdrawal_id}/transfer")
def wallet_approve_transfer(
    request: Request,
    withdrawal_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    try:
        transfer_wallet_to_bank(db, withdrawal_id, processed_by_user_id=user.id)
    except ValueError as exc:
        return _wallet_view(request, db, user, error=str(exc))
    return RedirectResponse(url="/admin/wallet?sent=1", status_code=303)


@router.post("/wallet/withdrawals/{withdrawal_id}/reject")
def wallet_reject_transfer(
    request: Request,
    withdrawal_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    try:
        reject_wallet_transfer(db, withdrawal_id, processed_by_user_id=user.id)
    except ValueError as exc:
        return _wallet_view(request, db, user, error=str(exc))
    return RedirectResponse(url="/admin/wallet?rejected=1", status_code=303)


def _user_form_view(request: Request, user: User, *, staff=None, error: str = "", posted: dict = None):
    data = posted or staff or {}
    return templates.TemplateResponse(
        "admin_user_form.html",
        _admin_context(
            request,
            user,
            staff=data,
            is_edit=bool(staff and staff.get("id")),
            form_error=error,
            page_title="Edit user" if staff and staff.get("id") else "Add user",
            active_nav="users",
        ),
        status_code=400 if error else 200,
    )


@router.get("/users")
def users_list(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    return templates.TemplateResponse(
        "admin_users.html",
        _admin_context(
            request,
            user,
            staff_users=list_users(db),
            page_title="Users & bank accounts",
            active_nav="users",
        ),
    )


@router.get("/users/new")
def users_new(request: Request, user: User = Depends(require_admin)):
    return _user_form_view(request, user)


@router.get("/users/{user_id}/edit")
def users_edit(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    staff = db.query(User).filter(User.id == user_id).first()
    if not staff:
        return RedirectResponse(url="/admin/users", status_code=302)
    return _user_form_view(request, user, staff=serialize_user(staff))


@router.post("/users")
def users_save(
    request: Request,
    user_id: str = Form(""),
    username: str = Form(""),
    password: str = Form(""),
    role: str = Form(""),
    full_name: str = Form(""),
    phone: str = Form(""),
    alternate_mobile: str = Form(""),
    email: str = Form(""),
    address: str = Form(""),
    aadhaar: str = Form(""),
    bank_name: str = Form(""),
    account_name: str = Form(""),
    account_number: str = Form(""),
    ifsc: str = Form(""),
    is_active: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    posted = {
        "id": int(user_id) if str(user_id).isdigit() else None,
        "username": username,
        "role": role,
        "full_name": full_name,
        "phone": phone,
        "alternate_mobile": alternate_mobile,
        "email": email,
        "address": address,
        "aadhaar": aadhaar,
        "bank_name": bank_name,
        "account_name": account_name,
        "account_number": account_number,
        "ifsc": ifsc,
        "is_active": is_active in {"1", "on", "true", "yes"},
        "role_label": ROLE_LABELS.get(role, role),
        "has_bank": True,
    }
    phone_val, phone_err = validate_mobile(phone, required=True, label="Mobile number")
    alt_val, alt_err = validate_mobile(alternate_mobile, required=False, label="Alternate mobile number")
    email_val, email_err = validate_email(email, required=True)
    aadhaar_val, aadhaar_err = validate_aadhaar(aadhaar, required=True)
    address_val = sanitize_string(address, 500)
    account_val, account_err = validate_account_number(account_number)
    ifsc_val, ifsc_err = validate_ifsc(ifsc)
    bank_name_val = sanitize_string(bank_name, 80)
    account_name_val = sanitize_string(account_name, 80)
    error = phone_err or alt_err or email_err or aadhaar_err or account_err or ifsc_err
    if not sanitize_string(full_name, 80):
        error = error or "Full name is required."
    if not address_val:
        error = error or "Address is required."
    if not bank_name_val:
        error = error or "Bank name is required."
    if not account_name_val:
        error = error or "Account holder name is required."
    if error:
        return _user_form_view(request, user, staff=posted if posted["id"] else None, error=error, posted=posted)

    try:
        save_staff_user(
            db,
            posted["id"],
            {
                "username": username,
                "password": password,
                "role": role,
                "full_name": sanitize_string(full_name, 80),
                "phone": phone_val,
                "alternate_mobile": alt_val,
                "email": email_val,
                "address": address_val,
                "aadhaar": aadhaar_val,
                "bank_name": bank_name_val,
                "account_name": account_name_val,
                "account_number": account_val,
                "ifsc": ifsc_val,
                "is_active": posted["is_active"],
            },
        )
    except ValueError as exc:
        return _user_form_view(request, user, staff=posted if posted["id"] else None, error=str(exc), posted=posted)
    return RedirectResponse(url="/admin/users", status_code=303)

