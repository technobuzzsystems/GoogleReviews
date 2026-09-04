"""
routes/feedback_routes.py
--------------------------
All customer-facing feedback routes (FastAPI).
"""

import logging
from fastapi import APIRouter, Request, HTTPException, status, Depends
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from config import get_config
from database import get_db
from services.business_service import find_business_by_route, get_business
from services.storage_service import get_logo_object, public_logo_url
from models.schemas import GenerateFeedbackRequest, SubmitFeedbackRequest
from utils.validators import sanitize_string
from services.gemini_service import generate_feedback_suggestions

router = APIRouter()
config = get_config()
logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="templates")

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
    context = {
        "request": request,
        "business_id":       b_config.get("key") or business_id,
        "company_name":      b_config.get("name", "Feedback"),
        "company_id":        b_config.get("id", ""),
        "google_review_url": b_config.get("google_review_url", ""),
        "logo_filename":     logo,
        "logo_url":          b_config.get("logo_url") or public_logo_url(logo),
        "feedback_path":     b_config.get("feedback_path") or "/feedback",
    }
    return templates.TemplateResponse(request=request, name="feedback.html", context=context)

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

    try:
        suggestions = generate_feedback_suggestions(
            rating,
            business_context=b_config,
            language=payload.language,
        )
        if not suggestions:
            raise HTTPException(status_code=500, detail="AI returned no suggestions. Please try again.")

        logger.info("generate_feedback: %d suggestions for rating=%d", len(suggestions), rating)
        return {"suggestions": suggestions}

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
def submit_feedback(payload: SubmitFeedbackRequest):
    """
    Validate the selected feedback but DO NOT save it to any database.
    Always returns a mock success response to keep the frontend flow working.
    """
    # ── Sanitize inputs ────────────────────────────────────────────────────────
    company       = sanitize_string(payload.company, max_length=200)
    company_id    = sanitize_string(payload.company_id, max_length=100)
    rating        = payload.rating
    feedback_text = sanitize_string(payload.feedback, max_length=1000)
    business_id   = payload.business_id
    
    # Normally we would save to a DB here, but database connectivity is removed.
    logger.info(
        "submit_feedback (MOCK SUCCESS) — company=%s rating=%d feedback='%s'",
        company_id, rating, feedback_text[:50]
    )

    return {
        "success": True,
        "message": "Thank you! Your feedback has been recorded.",
        "id":      "no-db-submission",
    }
