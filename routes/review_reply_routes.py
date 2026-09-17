"""
routes/review_reply_routes.py
------------------------------
API routes for Google Reviews AI Auto-Replier (FastAPI).
Serves Chrome Extension, Webhook automations, and Admin Dashboard.
"""

import io
import json
import logging
import os
import zipfile
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from config import get_config
from database import get_db
from models.schemas import (
    BatchGenerateReplyRequest,
    GenerateReplyRequest,
    LogReplyRequest,
    SaveReplySettingsRequest,
)
from services.business_service import get_business
from services.reply_service import (
    find_matching_business,
    generate_review_reply,
    get_business_reply_settings,
    get_review_reply_logs,
    log_review_reply,
    save_business_reply_settings,
)

router = APIRouter(prefix="/api/reviews", tags=["AI Review Reply"])
logger = logging.getLogger(__name__)
config = get_config()


# ─── POST /api/reviews/generate-reply ──────────────────────────────────────────
@router.post("/generate-reply")
def api_generate_review_reply(
    payload: GenerateReplyRequest,
    db: Session = Depends(get_db),
):
    """
    Generate an AI reply for a Google Review using Gemini AI for ANY organization.
    Understands review language (e.g. Marathi, Hindi, English), sentiment, and context.
    """
    # 1. First try matching by business_name (from Google page) or business_id
    b_config = None
    if payload.business_name:
        b_config = find_matching_business(db, payload.business_name)
    if not b_config and payload.business_id:
        b_config = find_matching_business(db, payload.business_id) or get_business(db, payload.business_id)
        
    resolved_name = (b_config.get("name") if b_config else "") or payload.business_name or config.COMPANY_NAME or "Our Business"
    resolved_id = (b_config.get("key") if b_config else "") or payload.business_id or "default"

    try:
        result = generate_review_reply(
            review_text=payload.review_text,
            reviewer_name=payload.reviewer_name,
            rating=payload.rating,
            business_context=b_config,
            business_name=resolved_name,
            language=payload.language or "auto",
            tone=payload.tone or "professional_warm",
            signature=payload.signature,
            auto_sign=payload.auto_sign,
        )

        # Automatically log generated reply
        try:
            log_review_reply(
                db=db,
                business_key=resolved_id,
                reviewer_name=payload.reviewer_name,
                rating=payload.rating,
                review_text=payload.review_text,
                reply_text=result.get("reply", ""),
                language=result.get("language", "auto"),
                tone=payload.tone or "professional_warm",
                status="generated",
                source="api",
            )
        except Exception as log_err:
            logger.warning("Failed to log review reply to DB: %s", log_err)

        return result



    except Exception as e:
        logger.error("api_generate_review_reply error: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to generate reply: {str(e)}")



# ─── POST /api/reviews/batch-generate ─────────────────────────────────────────
@router.post("/batch-generate")
def api_batch_generate_replies(
    payload: BatchGenerateReplyRequest,
    db: Session = Depends(get_db),
):
    """Generate replies for multiple customer reviews simultaneously."""
    results = []
    for item in payload.reviews:
        business_id = item.business_id or "technobuzz"
        b_config = get_business(db, business_id) or {
            "name": config.COMPANY_NAME,
            "scope": "Software and IT solutions.",
        }
        res = generate_review_reply(
            review_text=item.review_text,
            reviewer_name=item.reviewer_name,
            rating=item.rating,
            business_context=b_config,
            language=item.language or "auto",
            tone=item.tone or "professional_warm",
            signature=item.signature,
            auto_sign=item.auto_sign,
        )
        results.append({
            "reviewer_name": item.reviewer_name,
            "rating": item.rating,
            "reply_result": res,
        })
    return {"count": len(results), "items": results}


# ─── POST /api/reviews/log ────────────────────────────────────────────────────
@router.post("/log")
def api_log_review_reply(
    payload: LogReplyRequest,
    db: Session = Depends(get_db),
):
    """Log an auto-reply that was submitted on Google."""
    try:
        entry = log_review_reply(
            db=db,
            business_key=payload.business_id,
            reviewer_name=payload.reviewer_name,
            rating=payload.rating,
            review_text=payload.review_text,
            reply_text=payload.reply_text,
            language=payload.language,
            tone=payload.tone,
            status=payload.status,
            source=payload.source,
        )
        return {"success": True, "id": entry.id, "status": entry.status}
    except Exception as e:
        logger.error("api_log_review_reply error: %s", str(e))
        return {"success": False, "error": str(e)}


# ─── GET /api/reviews/history ─────────────────────────────────────────────────
@router.get("/history")
def api_get_reply_history(
    business_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Get past auto-reply activity logs."""
    logs = get_review_reply_logs(db, business_key=business_id, limit=limit)
    return [
        {
            "id": log.id,
            "business_key": log.business_key,
            "reviewer_name": log.reviewer_name,
            "rating": log.rating,
            "review_text": log.review_text,
            "reply_text": log.reply_text,
            "language": log.language,
            "tone": log.tone,
            "status": log.status,
            "source": log.source,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in logs
    ]


# ─── POST /api/reviews/logs/clear ─────────────────────────────────────────────
@router.post("/logs/clear")
def api_clear_reply_logs(
    business_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Clear past review reply logs (optionally for a specific business)."""
    from models.domain_models import ReviewReplyLog
    query = db.query(ReviewReplyLog)
    if business_id and business_id != "all":
        query = query.filter(ReviewReplyLog.business_key == business_id)
    count = query.delete(synchronize_session=False)
    db.commit()
    return {"success": True, "deleted_count": count}


# ─── GET /api/reviews/settings/{business_key} ─────────────────────────────────
@router.get("/settings/{business_key}")
def api_get_settings(
    business_key: str,
    db: Session = Depends(get_db),
):
    """Get auto-reply preferences for a business."""
    settings = get_business_reply_settings(db, business_key)
    return settings


# ─── POST /api/reviews/settings/{business_key} ────────────────────────────────
@router.post("/settings/{business_key}")
def api_save_settings(
    business_key: str,
    payload: SaveReplySettingsRequest,
    db: Session = Depends(get_db),
):
    """Save auto-reply preferences for a business."""
    success = save_business_reply_settings(
        db=db,
        business_key=business_key,
        reply_tone=payload.reply_tone,
        reply_signature=payload.reply_signature,
        reply_language_mode=payload.reply_language_mode,
        auto_send_enabled=payload.auto_send_enabled,
        auto_send_delay=payload.auto_send_delay,
    )
    if not success:
        raise HTTPException(status_code=404, detail="Business not found.")
    return {"success": True, "message": "Settings updated successfully."}


# ─── GET /api/reviews/extension/download ──────────────────────────────────────
@router.get("/extension/download")
def api_download_extension(
    request: Request,
    business: Optional[str] = Query(None),
):
    """Package and stream the Google Reviews Auto-Replier Chrome Extension as a .zip file pre-configured for the business."""
    ext_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "extension")
    if not os.path.isdir(ext_dir):
        raise HTTPException(status_code=404, detail="Extension directory not found.")

    server_origin = str(request.base_url).rstrip("/")
    target_biz = (business or "").strip() or "technobuzz"

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for root, _, files in os.walk(ext_dir):
            for file in files:
                abs_path = os.path.join(root, file)
                rel_path = os.path.relpath(abs_path, ext_dir)

                # Dynamically pre-configure server URL and business ID into content.js and popup.js
                if file in {"content.js", "popup.js"}:
                    with open(abs_path, "r", encoding="utf-8") as f:
                        code = f.read()
                    code = code.replace('apiBaseUrl: "http://localhost:5001"', f'apiBaseUrl: "{server_origin}"')
                    code = code.replace('businessId: "technobuzz"', f'businessId: "{target_biz}"')
                    zip_file.writestr(rel_path, code)
                else:
                    zip_file.write(abs_path, rel_path)

    zip_buffer.seek(0)
    filename = f"{target_biz}-google-reviews-autoreplier.zip"
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        },
    )
