"""Separate login for a business owner. They only see their own customer responses."""

from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import get_db
from models.domain_models import BusinessConfigModel, User, UserRole
from services.auth_service import authenticate_user, get_session_user, login_user, logout_user, require_owner
from services.feedback_service import export_rows, feedback_stats, list_feedback_page, mark_feedback_read

router = APIRouter(prefix="/owner")
templates = Jinja2Templates(directory="templates")


def _star_filter(value) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number in (0, 1, 2, 3, 4, 5) else 0


def _page_number(value) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 1
    return number if number > 0 else 1


def _safe_next(next_url: str) -> str:
    if not next_url:
        return "/owner"
    parsed = urlparse(next_url)
    if parsed.scheme or parsed.netloc:
        return "/owner"
    path = parsed.path or ""
    if path == "/owner" or path.startswith("/owner/"):
        return path
    return "/owner"


def _owner_business_name(db: Session, user: User) -> str:
    row = (
        db.query(BusinessConfigModel.name)
        .filter(BusinessConfigModel.owner_user_id == user.id)
        .order_by(BusinessConfigModel.name.asc())
        .first()
    )
    return row[0] if row else ""


@router.get("/login")
def owner_login_page(request: Request, next: str = "/owner", error: str = "", db: Session = Depends(get_db)):
    existing = get_session_user(request, db)
    if existing and existing.role == UserRole.OWNER:
        return RedirectResponse(url="/owner", status_code=302)
    if existing:
        return RedirectResponse(url="/admin", status_code=302)
    return templates.TemplateResponse(
        request=request,
        name="owner_login.html",
        context={"request": request, "next": _safe_next(next), "error": error, "username": ""},
    )


@router.post("/login")
def owner_login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/owner"),
    db: Session = Depends(get_db),
):
    typed = (username or "").strip()
    user = authenticate_user(db, typed.lower(), password)
    if not user or user.role != UserRole.OWNER:
        return templates.TemplateResponse(
            request=request,
            name="owner_login.html",
            context={
                "request": request,
                "next": _safe_next(next),
                "error": "Invalid username or password.",
                "username": typed,
            },
            status_code=401,
        )
    login_user(request, user)
    return RedirectResponse(url=_safe_next(next), status_code=302)


@router.get("/logout")
def owner_logout(request: Request):
    logout_user(request)
    return RedirectResponse(url="/owner/login", status_code=302)


@router.get("")
@router.get("/")
def owner_home(
    request: Request,
    rating: str = "",
    date_from: str = "",
    date_to: str = "",
    unread: str = "",
    page: str = "1",
    db: Session = Depends(get_db),
    user: User = Depends(require_owner),
):
    filters = {
        "rating": _star_filter(rating),
        "date_from": (date_from or "").strip(),
        "date_to": (date_to or "").strip(),
        "unread_only": (unread or "").strip() in {"1", "true", "yes"},
    }
    listing = list_feedback_page(db, user, page=_page_number(page), **filters)
    stats = feedback_stats(db, user, **filters)
    return templates.TemplateResponse(
        request=request,
        name="owner_feedback.html",
        context={
            "request": request,
            "user": user,
            "business_name": _owner_business_name(db, user),
            "listing": listing,
            "stats": stats,
            "selected_rating": filters["rating"],
            "date_from": filters["date_from"],
            "date_to": filters["date_to"],
            "unread_only": filters["unread_only"],
            "page_title": "Customer responses",
        },
    )


@router.post("/feedback/{feedback_id}/read")
def owner_mark_read(
    feedback_id: int,
    rating: str = Form("0"),
    date_from: str = Form(""),
    date_to: str = Form(""),
    unread: str = Form(""),
    page: str = Form("1"),
    db: Session = Depends(get_db),
    user: User = Depends(require_owner),
):
    mark_feedback_read(db, user, feedback_id)
    query = urlencode(
        {
            "rating": _star_filter(rating),
            "date_from": date_from,
            "date_to": date_to,
            "unread": unread,
            "page": _page_number(page),
        }
    )
    return RedirectResponse(url=f"/owner?{query}", status_code=303)


@router.get("/export")
def owner_export(
    db: Session = Depends(get_db),
    user: User = Depends(require_owner),
):
    import csv
    import io

    rows = export_rows(db, user)
    names = {}
    if rows:
        names = dict(
            db.query(BusinessConfigModel.key, BusinessConfigModel.name)
            .filter(BusinessConfigModel.key.in_({row.business_key for row in rows}))
            .all()
        )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Company", "Company ID", "Rating", "Feedback", "Date", "Time"])
    for row in rows:
        created = row.created_at
        writer.writerow([
            row.id,
            names.get(row.business_key) or row.business_key,
            row.business_key,
            row.rating,
            row.comment,
            created.strftime("%Y-%m-%d") if created else "",
            created.strftime("%H:%M:%S") if created else "",
        ])
    output.seek(0)
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=customer_responses.csv"},
    )
