"""
routes/google_business_routes.py
--------------------------------
FastAPI routes for Google Business Profile OAuth 2.0 connection,
review synchronization, AI auto-reply triggers, and reply audit logs.
"""

import json
import logging
import urllib.parse
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models.domain_models import BusinessConfigModel, GoogleBusinessAccount, ReviewReplyLog
from services.google_business_service import (
    exchange_code_for_tokens,
    fetch_accounts_and_locations,
    fetch_user_info,
    get_google_auth_url,
    is_google_api_configured,
    sync_all_active_businesses,
    sync_and_auto_reply_for_business,
)
from services.reply_service import (
    clear_review_reply_logs,
    generate_review_reply,
    get_review_reply_logs,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/google-business", tags=["Google Business Auto-Reply"])


class TestReplyRequest(BaseModel):
    business_key: str
    reviewer_name: str = "Valued Customer"
    rating: int = 5
    review_text: str = "Great service!"
    language: str = "auto"
    tone: str = "professional_warm"


# ─── 1. Initiate OAuth Login for a Business ───────────────────────────────────
@router.get("/auth/{business_key}")
def start_google_oauth(
    business_key: str,
    db: Session = Depends(get_db),
):
    """Start Google OAuth 2.0 flow for connecting a specific business profile."""
    biz = db.query(BusinessConfigModel).filter(BusinessConfigModel.key == business_key).first()
    if not biz:
        raise HTTPException(status_code=404, detail="Business not found.")

    if not is_google_api_configured():
        return RedirectResponse(
            url=f"/admin/auto-reply?business={business_key}&setup_creds=1",
            status_code=302,
        )

    auth_url = get_google_auth_url(business_key)
    return RedirectResponse(url=auth_url, status_code=302)


# ─── 2. Google OAuth Callback Handler ─────────────────────────────────────────
@router.get("/callback")
def google_oauth_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Google redirects here with authorization code and state containing business_key."""
    if error:
        logger.warning("Google OAuth error: %s", error)
        return RedirectResponse(url=f"/admin/auto-reply?error={urllib.parse.quote(error)}", status_code=302)

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing required code or state parameter.")

    try:
        decoded_state = urllib.parse.unquote(state)
        state_data = json.loads(decoded_state)
        business_key = state_data.get("business_key", "technobuzz")
    except Exception:
        business_key = "technobuzz"

    try:
        tokens = exchange_code_for_tokens(code)
        access_token = tokens["access_token"]
        refresh_token = tokens.get("refresh_token", "")
        expires_in = tokens.get("expires_in", 3600)
        token_expiry = datetime.utcnow() + timedelta(seconds=expires_in)

        # Get Google Account Email
        user_info = fetch_user_info(access_token)
        account_email = user_info.get("email", "")

        # Discover Google Business Locations
        locs = fetch_accounts_and_locations(access_token)
        google_account_id = locs[0]["account_id"] if locs else ""
        google_location_id = locs[0]["location_id"] if locs else ""
        location_title = locs[0]["title"] if locs else ""

        # Upsert GoogleBusinessAccount record
        account = db.query(GoogleBusinessAccount).filter_by(business_key=business_key).first()
        if not account:
            account = GoogleBusinessAccount(business_key=business_key)
            db.add(account)

        account.account_email = account_email
        account.access_token = access_token
        if refresh_token:
            account.refresh_token = refresh_token
        account.token_expiry = token_expiry
        account.google_account_id = google_account_id
        account.google_location_id = google_location_id
        account.location_name = location_title
        account.is_connected = True
        account.last_sync_status = "success"
        account.error_message = ""
        db.commit()

        logger.info("Successfully connected Google Business Profile for '%s' (%s)", business_key, account_email)
        return RedirectResponse(
            url=f"/admin/auto-reply?business={business_key}&connected=1",
            status_code=302,
        )

    except Exception as e:
        logger.error("OAuth callback processing failed for '%s': %s", business_key, str(e), exc_info=True)
        return RedirectResponse(
            url=f"/admin/auto-reply?business={business_key}&error={urllib.parse.quote(str(e))}",
            status_code=302,
        )


# ─── 3. Connection Status Endpoint ────────────────────────────────────────────
@router.get("/status/{business_key}")
def get_connection_status(
    business_key: str,
    db: Session = Depends(get_db),
):
    """Check whether a business is currently connected to Google Business Profile."""
    account = db.query(GoogleBusinessAccount).filter_by(business_key=business_key).first()
    biz = db.query(BusinessConfigModel).filter_by(key=business_key).first()

    return {
        "business_key": business_key,
        "business_name": biz.name if biz else business_key,
        "is_configured": is_google_api_configured(),
        "is_connected": bool(account and account.is_connected),
        "account_email": account.account_email if account else "",
        "location_name": account.location_name if account else "",
        "auto_reply_enabled": getattr(account, "auto_reply_enabled", True) if account else True,
        "last_synced_at": account.last_synced_at.isoformat() if account and account.last_synced_at else "",
        "last_sync_status": account.last_sync_status if account else "never",
        "error_message": account.error_message if account else "",
    }


# ─── 4. Trigger Immediate Review Sync & Auto-Reply ────────────────────────────
@router.post("/sync/{business_key}")
def trigger_business_sync(
    business_key: str,
    db: Session = Depends(get_db),
):
    """Trigger an immediate check and auto-reply for a single business."""
    result = sync_and_auto_reply_for_business(db, business_key)
    return result


# ─── 5. Sync All Active Businesses ────────────────────────────────────────────
@router.post("/sync-all")
def trigger_all_sync(
    db: Session = Depends(get_db),
):
    """Trigger background check and auto-reply for all connected businesses."""
    results = sync_all_active_businesses(db)
    return {"count": len(results), "results": results}


# ─── 6. Disconnect Google Account ─────────────────────────────────────────────
@router.post("/disconnect/{business_key}")
def disconnect_business_google(
    business_key: str,
    db: Session = Depends(get_db),
):
    """Disconnect a business's Google Business Profile account."""
    account = db.query(GoogleBusinessAccount).filter_by(business_key=business_key).first()
    if account:
        account.is_connected = False
        account.access_token = ""
        account.refresh_token = ""
        account.google_account_id = ""
        account.google_location_id = ""
        account.last_sync_status = "never"
        account.error_message = "Disconnected by user."
        db.commit()

    return {"success": True, "business_key": business_key, "message": "Disconnected successfully."}


# ─── 7. Toggle Auto-Reply On/Off ──────────────────────────────────────────────
@router.post("/toggle/{business_key}")
def toggle_auto_reply(
    business_key: str,
    enabled: bool = Query(...),
    db: Session = Depends(get_db),
):
    """Enable or disable automated review replies for a business."""
    account = db.query(GoogleBusinessAccount).filter_by(business_key=business_key).first()
    if account:
        account.auto_reply_enabled = enabled
        db.commit()

    biz = db.query(BusinessConfigModel).filter_by(key=business_key).first()
    if biz:
        biz.auto_reply_enabled = enabled
        db.commit()

    return {"success": True, "business_key": business_key, "auto_reply_enabled": enabled}


# ─── 8. Test AI Reply Generation (Live Simulator) ─────────────────────────────
@router.post("/test-generate")
def test_generate_reply(
    payload: TestReplyRequest,
    db: Session = Depends(get_db),
):
    """Test AI reply generation with full business SEO context without posting to Google."""
    biz = db.query(BusinessConfigModel).filter_by(key=payload.business_key).first()
    b_dict = {
        "name": biz.name if biz else payload.business_key,
        "business_category": getattr(biz, "business_category", "") or "Local Business",
        "area": getattr(biz, "area", "") or "",
        "scope": getattr(biz, "scope", "") or "",
        "seo_keywords": getattr(biz, "seo_keywords", "") or "",
        "mobile": getattr(biz, "mobile", "") or "",
        "reply_signature": getattr(biz, "reply_signature", "") or f"— Team {biz.name if biz else payload.business_key}",
    }

    result = generate_review_reply(
        business_name=b_dict["name"],
        reviewer_name=payload.reviewer_name,
        rating=payload.rating,
        review_text=payload.review_text,
        business_context=b_dict,
        language=payload.language,
        tone=payload.tone,
        auto_sign=True,
    )
    return result


# ─── 9. Retrieve Review Reply Audit Logs ──────────────────────────────────────
@router.get("/logs/{business_key}")
def get_business_reply_logs(
    business_key: str,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Get recent review replies sent or generated for a business."""
    logs = get_review_reply_logs(db, business_key=business_key, limit=limit)
    return [
        {
            "id": l.id,
            "business_key": l.business_key,
            "reviewer_name": l.reviewer_name,
            "rating": l.rating,
            "review_text": l.review_text,
            "reply_text": l.reply_text,
            "language": l.language,
            "status": l.status,
            "source": l.source,
            "created_at": l.created_at.strftime("%Y-%m-%d %H:%M:%S") if l.created_at else "",
        }
        for l in logs
    ]


@router.post("/logs/clear")
def clear_logs(
    business_key: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Clear reply logs."""
    deleted = clear_review_reply_logs(db, business_key=business_key)
    return {"success": True, "deleted_count": deleted}


# ─── 10. Save Google OAuth Credentials ────────────────────────────────────────
class SaveCredsRequest(BaseModel):
    client_id: str
    client_secret: str


@router.post("/save-creds")
def save_google_creds(payload: SaveCredsRequest):
    """Save Google OAuth credentials into .env and runtime environment."""
    cid = payload.client_id.strip()
    csec = payload.client_secret.strip()
    if not cid or not csec:
        raise HTTPException(status_code=400, detail="Client ID and Client Secret are required.")

    import os
    os.environ["GOOGLE_CLIENT_ID"] = cid
    os.environ["GOOGLE_CLIENT_SECRET"] = csec

    env_path = ".env"
    try:
        lines = []
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

        updated_cid = False
        updated_csec = False
        new_lines = []
        for line in lines:
            if line.startswith("GOOGLE_CLIENT_ID="):
                new_lines.append(f'GOOGLE_CLIENT_ID="{cid}"\n')
                updated_cid = True
            elif line.startswith("GOOGLE_CLIENT_SECRET="):
                new_lines.append(f'GOOGLE_CLIENT_SECRET="{csec}"\n')
                updated_csec = True
            else:
                new_lines.append(line)

        if not updated_cid:
            new_lines.append(f'GOOGLE_CLIENT_ID="{cid}"\n')
        if not updated_csec:
            new_lines.append(f'GOOGLE_CLIENT_SECRET="{csec}"\n')

        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

        logger.info("Saved Google OAuth credentials to .env")
    except Exception as e:
        logger.warning("Could not persist to .env: %s", e)

    return {"success": True, "message": "Credentials saved successfully."}


# ─── 11. Save Business Reply Settings ─────────────────────────────────────────
class BusinessReplySettingsRequest(BaseModel):
    reply_tone: Optional[str] = "professional_warm"
    reply_signature: Optional[str] = ""
    reply_language_mode: Optional[str] = "auto"
    auto_send_enabled: Optional[bool] = True
    auto_send_delay: Optional[int] = 2
    seo_keywords: Optional[str] = ""


@router.post("/settings/{business_key}")
def save_reply_settings(
    business_key: str,
    payload: BusinessReplySettingsRequest,
    db: Session = Depends(get_db),
):
    """Save auto-reply and SEO preferences for a specific business."""
    biz = db.query(BusinessConfigModel).filter_by(key=business_key).first()
    if not biz:
        raise HTTPException(status_code=404, detail="Business not found.")

    if payload.reply_tone is not None:
        biz.reply_tone = payload.reply_tone
    if payload.reply_signature is not None:
        biz.reply_signature = payload.reply_signature
    if payload.reply_language_mode is not None:
        biz.reply_language_mode = payload.reply_language_mode
    if payload.auto_send_enabled is not None:
        biz.auto_reply_enabled = payload.auto_send_enabled
    if payload.seo_keywords is not None:
        biz.seo_keywords = payload.seo_keywords

    account = db.query(GoogleBusinessAccount).filter_by(business_key=business_key).first()
    if account and payload.auto_send_enabled is not None:
        account.auto_reply_enabled = payload.auto_send_enabled

    db.commit()
    return {"success": True, "business_key": business_key, "message": "Preferences saved successfully."}


# ─── 12. Compatibility Router for /api/reviews/* ──────────────────────────────
reviews_router = APIRouter(prefix="/api/reviews", tags=["Reviews Auto-Reply Compatibility"])


class ReviewsGenerateReplyRequest(BaseModel):
    business_name: Optional[str] = ""
    reviewer_name: Optional[str] = "Valued Customer"
    rating: Optional[int] = 5
    review_text: Optional[str] = ""
    language: Optional[str] = "auto"
    tone: Optional[str] = "professional_warm"
    auto_sign: Optional[bool] = True
    business_key: Optional[str] = ""
    business_id: Optional[str] = ""


@reviews_router.post("/generate-reply")
def api_reviews_generate_reply(
    payload: ReviewsGenerateReplyRequest,
    business: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    biz_key = payload.business_key or payload.business_id or business or "technobuzz"
    biz = db.query(BusinessConfigModel).filter_by(key=biz_key).first()
    b_dict = {
        "name": (biz.name if biz else payload.business_name) or biz_key,
        "business_category": getattr(biz, "business_category", "") or "Local Business",
        "area": getattr(biz, "area", "") or "",
        "scope": getattr(biz, "scope", "") or "",
        "seo_keywords": getattr(biz, "seo_keywords", "") or "",
        "mobile": getattr(biz, "mobile", "") or "",
        "reply_signature": getattr(biz, "reply_signature", "") or (f"— Team {biz.name}" if biz else ""),
    }
    result = generate_review_reply(
        business_name=b_dict["name"],
        reviewer_name=payload.reviewer_name or "Valued Customer",
        rating=payload.rating or 5,
        review_text=payload.review_text or "",
        business_context=b_dict,
        language=payload.language or "auto",
        tone=payload.tone or "professional_warm",
        auto_sign=payload.auto_sign if payload.auto_sign is not None else True,
    )
    return result


class BatchReviewsGenerateReplyRequest(BaseModel):
    reviews: list[ReviewsGenerateReplyRequest]


@reviews_router.post("/batch-generate")
def api_reviews_batch_generate(
    payload: BatchReviewsGenerateReplyRequest,
    db: Session = Depends(get_db),
):
    results = []
    for item in payload.reviews:
        biz_key = item.business_key or item.business_id or "technobuzz"
        biz = db.query(BusinessConfigModel).filter_by(key=biz_key).first()
        b_dict = {
            "name": (biz.name if biz else item.business_name) or biz_key,
            "business_category": getattr(biz, "business_category", "") or "Local Business",
            "area": getattr(biz, "area", "") or "",
            "scope": getattr(biz, "scope", "") or "",
            "seo_keywords": getattr(biz, "seo_keywords", "") or "",
            "mobile": getattr(biz, "mobile", "") or "",
            "reply_signature": getattr(biz, "reply_signature", "") or (f"— Team {biz.name}" if biz else ""),
        }
        res = generate_review_reply(
            business_name=b_dict["name"],
            reviewer_name=item.reviewer_name or "Valued Customer",
            rating=item.rating or 5,
            review_text=item.review_text or "",
            business_context=b_dict,
            language=item.language or "auto",
            tone=item.tone or "professional_warm",
            auto_sign=item.auto_sign if item.auto_sign is not None else True,
        )
        results.append({
            "reviewer_name": item.reviewer_name,
            "rating": item.rating,
            "reply_result": res,
        })
    return {"success": True, "results": results}



class LogReplyRequest(BaseModel):
    business_key: Optional[str] = "technobuzz"
    business_id: Optional[str] = None
    reviewer_name: Optional[str] = "Valued Customer"
    rating: Optional[int] = 5
    review_text: Optional[str] = ""
    reply_text: Optional[str] = ""
    language: Optional[str] = "mr"
    status: Optional[str] = "sent"
    source: Optional[str] = "admin_simulator"


@reviews_router.post("/log")
def api_reviews_log(
    payload: LogReplyRequest,
    db: Session = Depends(get_db),
):
    from services.reply_service import log_review_reply
    target_key = payload.business_key or payload.business_id or "technobuzz"
    log_review_reply(
        db,
        business_key=target_key,
        reviewer_name=payload.reviewer_name or "Valued Customer",
        rating=payload.rating or 5,
        review_text=payload.review_text or "",
        reply_text=payload.reply_text or "",
        language=payload.language or "mr",
        status=payload.status or "sent",
        source=payload.source or "admin_simulator",
    )
    return {"success": True}


@reviews_router.post("/settings/{business_key}")
def api_reviews_settings(
    business_key: str,
    payload: BusinessReplySettingsRequest,
    db: Session = Depends(get_db),
):
    return save_reply_settings(business_key, payload, db)


@reviews_router.get("/logs/{business_key}")
def api_reviews_logs_get(
    business_key: str,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    return get_business_reply_logs(business_key, limit, db)


@reviews_router.get("/history")
def api_reviews_history(
    business_id: Optional[str] = Query(None),
    business_key: Optional[str] = Query(None),
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Retrieve auto-reply logs formatted for the admin dashboard logs table."""
    target = business_id or business_key
    target = None if target == "all" else target
    logs = get_review_reply_logs(db, business_key=target, limit=limit)
    return [
        {
            "id": l.id,
            "business_key": l.business_key,
            "reviewer_name": l.reviewer_name,
            "rating": l.rating,
            "review_text": l.review_text,
            "reply_text": l.reply_text,
            "language": l.language,
            "status": l.status,
            "source": l.source,
            "created_at": l.created_at.isoformat() if l.created_at else "",
        }
        for l in logs
    ]


@reviews_router.get("/extension/download")
def api_reviews_extension_download():
    """Information endpoint for legacy extension."""
    return JSONResponse(
        status_code=200,
        content={"message": "Cloud-based auto-reply is active. The browser extension is optional and no longer required."},
    )


@reviews_router.post("/logs/clear")
def api_reviews_logs_clear(
    business_id: Optional[str] = Query(None),
    business_key: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    target = business_id or business_key
    return clear_logs(target, db)


# Alias for legacy Google OAuth connect link
connect_router = APIRouter(prefix="/auth/google", tags=["Google Connect"])


@connect_router.get("/connect")
def google_connect_alias(business: str = "technobuzz"):
    return RedirectResponse(url=f"/api/google-business/auth/{business}", status_code=302)


# Alias router for server engine compatibility
server_engine_router = APIRouter(prefix="/api/server-engine", tags=["Server Engine Compatibility"])


@server_engine_router.get("/status/{business_key}")
def api_server_engine_status(business_key: str):
    from services.server_reply_engine import get_browser_login_status
    return get_browser_login_status(business_key)


@server_engine_router.post("/sync/{business_key}")
def api_server_engine_sync(
    business_key: str,
    db: Session = Depends(get_db),
):
    from services.server_reply_engine import sync_business_server_side
    return sync_business_server_side(db, business_key)


@server_engine_router.post("/login/{business_key}")
def api_server_engine_login(
    business_key: str,
):
    from services.server_reply_engine import launch_business_login
    return launch_business_login(business_key)


@server_engine_router.post("/sync-all")
def api_server_engine_sync_all(db: Session = Depends(get_db)):
    from services.server_reply_engine import sync_all_active_businesses
    return sync_all_active_businesses(db)

