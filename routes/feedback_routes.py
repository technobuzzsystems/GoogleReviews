"""
routes/feedback_routes.py
--------------------------
All customer-facing feedback routes (FastAPI).
"""

import logging
import re
from fastapi import APIRouter, Request, HTTPException, status, Depends
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from config import get_config
from database import get_db
from services.business_service import find_business_by_route, get_business, qr_feedback_target
from services.storage_service import get_logo_object, public_logo_url
from models.schemas import GenerateFeedbackRequest, SubmitFeedbackRequest
from utils.validators import sanitize_string
from services.gemini_service import generate_feedback_suggestions, validate_and_sanitize_suggestions
from services.static_feedback_service import get_available_feedback_suggestions, record_response_usage

router = APIRouter()
config = get_config()
logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="templates")


@router.get("/go/{business_key}")
def qr_go(business_key: str, db: Session = Depends(get_db)):
    """Permanent QR address. Sends the customer to the business's latest destination."""
    target = qr_feedback_target(db, business_key)
    if not target:
        raise HTTPException(status_code=404, detail="This QR code is not available.")
    destination = target.get("feedback_path") or "/feedback"
    return RedirectResponse(url=destination, status_code=302, headers={"Cache-Control": "no-store"})


# ─── GET /media/logo/{key} and /media/file/{key} ───────────────────────────────
@router.get("/media/logo/{key:path}")
@router.get("/media/file/{key:path}")
def serve_business_logo(key: str):
    """Stream a client logo or transfer screenshot from S3 / local uploads."""
    try:
        body, content_type = get_logo_object(key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found.")
    return Response(
        content=body,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


# ─── GET / ────────────────────────────────────────────────────────────────────
@router.get("/")
def index():
    """Redirect root URL to the main feedback page."""
    return RedirectResponse(url="/feedback")


# ─── GET /feedback and /boardwale ──────────────────────────────────────────────
def render_business_feedback(request: Request, business_id: str, db: Session, b_config: dict = None):
    """Internal helper to render the feedback page with specific business context."""
    b_config = b_config or get_business(db, business_id)
    if not b_config:
        raise HTTPException(status_code=404, detail="This feedback page is not available.")
    logo = (b_config.get("logo_filename") or "").strip()
    is_active = bool(b_config.get("is_active", True) if b_config.get("is_active") is not None else True)
    is_expired = bool(b_config.get("is_expired") or b_config.get("plan_status") == "expired")
    is_deactivated = not is_active
    is_blocked = is_expired or is_deactivated

    raw_wa = (b_config.get("whatsapp_number") or "").strip()
    wa_digits = re.sub(r"\D", "", raw_wa)
    if wa_digits:
        if len(wa_digits) == 10:
            wa_digits = f"91{wa_digits}"
        elif len(wa_digits) == 11 and wa_digits.startswith("0"):
            wa_digits = f"91{wa_digits[1:]}"
        whatsapp_link = f"https://wa.me/{wa_digits}"
    else:
        whatsapp_link = ""

    instagram_url = (b_config.get("instagram_url") or "").strip()
    if instagram_url and not (instagram_url.startswith("http://") or instagram_url.startswith("https://")):
        instagram_url = f"https://{instagram_url}"

    facebook_url = (b_config.get("facebook_url") or "").strip()
    if facebook_url and not (facebook_url.startswith("http://") or facebook_url.startswith("https://")):
        facebook_url = f"https://{facebook_url}"

    instagram_enabled = bool(b_config.get("instagram_enabled", False)) and bool(instagram_url)
    facebook_enabled = bool(b_config.get("facebook_enabled", False)) and bool(facebook_url)
    whatsapp_enabled = bool(b_config.get("whatsapp_enabled", False)) and bool(whatsapp_link)
    has_social_links = bool(instagram_enabled or facebook_enabled or whatsapp_enabled)

    context = {
        "request": request,
        "business_id":       b_config.get("key") or business_id,
        "company_name":      b_config.get("name", "Feedback"),
        "company_id":        b_config.get("id", ""),
        "google_review_url": b_config.get("google_review_url", ""),
        "logo_filename":     logo,
        "logo_url":          b_config.get("logo_url") or public_logo_url(logo),
        "feedback_path":     b_config.get("feedback_path") or "/feedback",
        "is_expired":        is_expired,
        "is_deactivated":    is_deactivated,
        "is_blocked":        is_blocked,
        "expiry_date":       b_config.get("expiry_date") or "",
        "instagram_url":     instagram_url,
        "instagram_enabled": instagram_enabled,
        "facebook_url":      facebook_url,
        "facebook_enabled":  facebook_enabled,
        "whatsapp_link":     whatsapp_link,
        "whatsapp_enabled":  whatsapp_enabled,
        "has_social_links":  has_social_links,
    }
    return templates.TemplateResponse(
        request=request,
        name="feedback.html",
        context=context,
        status_code=403 if is_blocked else 200,
    )

@router.get("/feedback")
def feedback_page(request: Request, db: Session = Depends(get_db)):
    return render_business_feedback(request, "technobuzz", db)

@router.get("/boardwale")
def old_boardwale_redirect():
    """Temporary backward-compatible redirect for old QR codes/links."""
    return RedirectResponse(url="/feedback/board_001", status_code=status.HTTP_301_MOVED_PERMANENTLY)

@router.get("/feedback/board_001")
def boardwale_feedback_page(request: Request, db: Session = Depends(get_db)):
    return render_business_feedback(request, "boardwale", db)

@router.get("/feedback/showroom_001")
def jawa_feedback_page(request: Request, db: Session = Depends(get_db)):
    return render_business_feedback(request, "jawa_showroom", db)

@router.get("/feedback/1")
def rutuja_battery_feedback_page(request: Request, db: Session = Depends(get_db)):
    return render_business_feedback(request, "rutuja_battery", db)


# ─── Dynamic slug route handler ───────────────────────────────────────────────
@router.get("/feedback/{slug}")
def dynamic_business_feedback_page(request: Request, slug: str, db: Session = Depends(get_db)):
    found = find_business_by_route(db, slug)
    if not found:
        raise HTTPException(status_code=404, detail="This feedback page is not available.")
    return render_business_feedback(request, found["key"], db, b_config=found)


# ─── POST /generate-feedback ──────────────────────────────────────────────────
@router.post("/generate-feedback")
def generate_feedback(payload: GenerateFeedbackRequest, db: Session = Depends(get_db)):
    """
    Accept a star rating, call Google Gemini AI,
    and return 8–10 human-like feedback suggestions.
    """
    rating = payload.rating
    business_id = payload.business_id
    b_config = get_business(db, business_id)
    if not b_config:
        raise HTTPException(status_code=400, detail="Unknown business.")
    if b_config.get("is_active") is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This review service is temporarily unavailable because the subscription is inactive. Please contact the business for more information.",
        )
    if b_config.get("is_expired") or b_config.get("plan_status") == "expired":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This review service is currently unavailable because the subscription has expired. Please renew the plan to continue using this service.",
        )

    # Determine suggestion count: maximum 3 for 1-3 stars, 10 for 4-5 stars
    limit = 3 if rating in (1, 2, 3) else 10

    # 1. Try predefined static responses first (Instant < 5ms, non-repeating, no Gemini delay)
    biz_key = b_config.get("key") or business_id
    static_items = get_available_feedback_suggestions(
        db,
        business_key=biz_key,
        rating=rating,
        language=payload.language,
        limit=limit,
    )
    if static_items:
        raw_suggestions = [item["text"] for item in static_items][:limit]
        response_ids = [item["id"] for item in static_items][:limit]
        # Enforce backend polarity guard: 5-star must never contain negative feedback
        suggestions = validate_and_sanitize_suggestions(
            raw_suggestions, rating, b_config, payload.language, target_count=limit
        )[:limit]
        logger.info(
            "[FEEDBACK_AUDIT] Static pool returned %d items | business=%s | rating=%d | lang=%s",
            len(suggestions),
            biz_key,
            rating,
            payload.language,
        )
        return {"rating": rating, "suggestions": suggestions, "response_ids": response_ids, "source": "static_pool"}

    # 2. Fallback to Gemini AI if static pool not generated yet
    try:
        raw_suggestions = generate_feedback_suggestions(
            rating,
            business_context=b_config,
            language=payload.language,
            count=limit,
        )
        if not raw_suggestions:
            raise HTTPException(status_code=500, detail="AI returned no suggestions. Please try again.")

        suggestions = validate_and_sanitize_suggestions(
            raw_suggestions, rating, b_config, payload.language, target_count=limit
        )[:limit]
        logger.info(
            "[FEEDBACK_AUDIT] Gemini AI returned %d items | business=%s | rating=%d | lang=%s",
            len(suggestions),
            biz_key,
            rating,
            payload.language,
        )
        return {"rating": rating, "suggestions": suggestions, "response_ids": [], "source": "gemini_ai"}

    except ValueError as e:
        logger.warning("generate_feedback: ValueError — %s", str(e))
        raise HTTPException(status_code=400, detail=str(e))

    except RuntimeError as e:
        error_str = str(e)
        logger.error("generate_feedback: RuntimeError — %s", error_str)
        if "GEMINI_API_KEY" in error_str:
            raise HTTPException(status_code=503, detail="AI service is not configured. Please contact support.")
        raise HTTPException(status_code=500, detail="AI service is temporarily unavailable. Please try again.")

    except Exception as e:
        logger.error("generate_feedback: unexpected error — %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="An unexpected error occurred. Please try again.")


# ─── POST /submit-feedback ────────────────────────────────────────────────────
@router.post("/submit-feedback", status_code=status.HTTP_201_CREATED)
def submit_feedback(payload: SubmitFeedbackRequest, db: Session = Depends(get_db)):
    business_id = payload.business_id
    b_config = None
    if business_id:
        b_config = get_business(db, business_id)
        if b_config:
            if b_config.get("is_active") is False:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="This review service is temporarily unavailable because the subscription is inactive. Please contact the business for more information.",
                )
            if b_config.get("is_expired") or b_config.get("plan_status") == "expired":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="This review service is currently unavailable because the subscription has expired. Please renew the plan to continue using this service.",
                )
    company       = sanitize_string(payload.company, max_length=200)
    company_id    = sanitize_string(payload.company_id, max_length=100)
    rating        = payload.rating
    feedback_text = sanitize_string(payload.feedback, max_length=1000)
    business_id   = payload.business_id
    saved_id = "no-db-submission"

    if b_config and feedback_text:
        try:
            from services.feedback_service import save_private_feedback

            is_manual = bool(getattr(payload, "is_manual", False))
            source = "manual" if is_manual else "qr"
            row = save_private_feedback(
                db,
                business_key=b_config.get("key") or business_id,
                rating=rating,
                comment=feedback_text,
                source=source,
            )
            saved_id = str(row.id)
            if payload.response_id and not is_manual:
                record_response_usage(
                    db,
                    response_id=payload.response_id,
                    business_key=b_config.get("key") or business_id,
                    rating=rating,
                    private_feedback_id=row.id,
                )
        except Exception:
            logger.exception("submit_feedback: could not store review for %s", business_id)
            db.rollback()

    logger.info(
        "submit_feedback — company=%s rating=%d feedback='%s'",
        company_id, rating, feedback_text[:50]
    )

    return {
        "success": True,
        "message": "Thank you! Your feedback has been recorded.",
        "id":      saved_id,
    }
