"""Stored customer reviews from the QR feedback page."""

from datetime import datetime, time

from sqlalchemy import func
from sqlalchemy.orm import Session

from models.domain_models import BusinessConfigModel, PrivateFeedback
from services.business_service import user_owns_business
from services.franchise_service import scope_for_user

PAGE_SIZE = 20


def save_private_feedback(db: Session, *, business_key: str, rating: int, comment: str, source: str = "qr") -> PrivateFeedback:
    row = PrivateFeedback(
        business_key=(business_key or "").strip(),
        rating=int(rating),
        comment=(comment or "").strip(),
        source=(source or "qr").strip(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _business_keys_for_user(db: Session, user) -> list | None:
    """None means every business. An empty list means this user sees none."""
    from models.domain_models import UserRole

    if getattr(user, "role", None) == UserRole.OWNER:
        rows = (
            db.query(BusinessConfigModel.key)
            .filter(BusinessConfigModel.owner_user_id == user.id)
            .all()
        )
        return [key for (key,) in rows]
    scope = scope_for_user(db, user)
    if scope["blocked"]:
        return []
    if scope["executive_id"]:
        rows = (
            db.query(BusinessConfigModel.key)
            .filter(BusinessConfigModel.sales_executive_id == scope["executive_id"])
            .all()
        )
        return [key for (key,) in rows]
    if scope["franchise_id"]:
        rows = (
            db.query(BusinessConfigModel.key)
            .filter(BusinessConfigModel.franchise_id == scope["franchise_id"])
            .all()
        )
        return [key for (key,) in rows]
    return None


def _apply_scope(query, keys: list | None):
    if keys is None:
        return query
    if not keys:
        return query.filter(PrivateFeedback.id == -1)
    return query.filter(PrivateFeedback.business_key.in_(keys))


def _parse_day(value: str, end: bool = False):
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        day = datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return None
    if end:
        return datetime.combine(day.date(), time(23, 59, 59))
    return day


def feedback_query(
    db: Session,
    user,
    *,
    business_key: str = "",
    rating: int = 0,
    date_from: str = "",
    date_to: str = "",
    unread_only: bool = False,
):
    keys = _business_keys_for_user(db, user)
    selected = (business_key or "").strip()
    if selected and not user_owns_business(db, user, selected):
        selected = ""
    query = _apply_scope(db.query(PrivateFeedback), keys)
    if selected:
        query = query.filter(PrivateFeedback.business_key == selected)
    if rating in (1, 2, 3, 4, 5):
        query = query.filter(PrivateFeedback.rating == rating)
    start = _parse_day(date_from)
    end = _parse_day(date_to, end=True)
    if start:
        query = query.filter(PrivateFeedback.created_at >= start)
    if end:
        query = query.filter(PrivateFeedback.created_at <= end)
    if unread_only:
        query = query.filter(PrivateFeedback.read_at.is_(None))
    return query


def feedback_stats(db: Session, user, **filters) -> dict:
    rows = (
        feedback_query(db, user, **filters)
        .with_entities(PrivateFeedback.rating, func.count(PrivateFeedback.id))
        .group_by(PrivateFeedback.rating)
        .all()
    )
    by_rating = {str(n): 0 for n in range(1, 6)}
    total = 0
    weighted = 0
    for rating, count in rows:
        count = int(count or 0)
        by_rating[str(int(rating))] = count
        total += count
        weighted += int(rating) * count
    unread = feedback_query(db, user, **filters).filter(PrivateFeedback.read_at.is_(None)).count()
    return {
        "total": total,
        "average_rating": round(weighted / total, 1) if total else 0.0,
        "by_rating": by_rating,
        "unread": unread,
        "negative": by_rating["1"] + by_rating["2"] + by_rating["3"],
    }


def list_feedback_page(db: Session, user, *, page: int = 1, **filters) -> dict:
    query = feedback_query(db, user, **filters).order_by(PrivateFeedback.created_at.desc(), PrivateFeedback.id.desc())
    total = query.count()
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE if total else 0
    page = max(1, int(page or 1))
    if pages:
        page = min(page, pages)
    rows = query.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all() if total else []
    names = {}
    business_keys = {row.business_key for row in rows}
    if business_keys:
        names = dict(
            db.query(BusinessConfigModel.key, BusinessConfigModel.name)
            .filter(BusinessConfigModel.key.in_(business_keys))
            .all()
        )
    records = []
    for row in rows:
        records.append(
            {
                "id": row.id,
                "business_key": row.business_key,
                "business_name": names.get(row.business_key) or row.business_key,
                "rating": row.rating,
                "comment": row.comment,
                "source": getattr(row, "source", "qr") or "qr",
                "created_at": row.created_at,
                "is_unread": row.read_at is None,
            }
        )
    return {"records": records, "total": total, "page": page, "pages": pages}


def businesses_for_filter(db: Session, user) -> list[dict]:
    keys = _business_keys_for_user(db, user)
    query = db.query(BusinessConfigModel.key, BusinessConfigModel.name).order_by(BusinessConfigModel.name.asc())
    if keys is not None:
        if not keys:
            return []
        query = query.filter(BusinessConfigModel.key.in_(keys))
    return [{"key": key, "name": name} for key, name in query.all()]


def negative_businesses(db: Session, user, limit: int = 5) -> list[dict]:
    keys = _business_keys_for_user(db, user)
    query = (
        db.query(
            PrivateFeedback.business_key,
            func.count(PrivateFeedback.id),
            func.avg(PrivateFeedback.rating),
        )
        .filter(PrivateFeedback.rating <= 3)
        .group_by(PrivateFeedback.business_key)
        .order_by(func.count(PrivateFeedback.id).desc())
    )
    query = _apply_scope(query, keys)
    rows = query.limit(limit).all()
    if not rows:
        return []
    names = dict(
        db.query(BusinessConfigModel.key, BusinessConfigModel.name)
        .filter(BusinessConfigModel.key.in_([key for key, _, _ in rows]))
        .all()
    )
    return [
        {
            "key": key,
            "name": names.get(key) or key,
            "count": int(count or 0),
            "average": round(float(average or 0), 1),
        }
        for key, count, average in rows
    ]


def mark_feedback_read(db: Session, user, feedback_id: int) -> bool:
    row = db.query(PrivateFeedback).filter(PrivateFeedback.id == feedback_id).first()
    if not row or not user_owns_business(db, user, row.business_key):
        return False
    if row.read_at is None:
        row.read_at = datetime.utcnow()
        row.read_by_user_id = user.id
        db.commit()
    return True


def export_rows(db: Session, user, *, business_key: str = "") -> list[PrivateFeedback]:
    return (
        feedback_query(db, user, business_key=business_key)
        .order_by(PrivateFeedback.created_at.desc())
        .limit(5000)
        .all()
    )
