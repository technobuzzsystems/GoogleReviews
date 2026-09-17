"""
routes/google_business_routes.py
--------------------------------
FastAPI routes for Google Business Profile Direct Cloud Connection (OAuth 2.0).

Endpoints:
    - GET  /auth/google/connect                 : Initiate Google OAuth consent flow
    - GET  /auth/google/callback                : Handle Google OAuth 2.0 callback
    - GET  /api/google-business/status/{key}    : Check connection status for business
    - POST /api/google-business/sync/{key}      : Trigger immediate review sync & AI reply
    - POST /api/google-business/toggle/{key}    : Enable/disable server-side auto-reply
    - POST /api/google-business/disconnect/{key}: Disconnect Google account
    - POST /api/google-business/save-creds      : Save OAuth Client ID/Secret to .env
"""

import json
import logging
import os
import urllib.parse
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models.domain_models import GoogleBusinessAccount
from services.google_business_service import (
    exchange_code_for_tokens,
    fetch_accounts_and_locations,
    fetch_google_user_info,
    get_google_auth_url,
    is_google_api_configured,
    sync_and_auto_reply_for_business,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Google Business Profile"])


class SaveCredentialsRequest(BaseModel):
    client_id: str
    client_secret: str
    redirect_uri: Optional[str] = None


class ToggleAutoPilotRequest(BaseModel):
    enabled: bool


# ─── 1. Initiate Google OAuth 2.0 Flow ────────────────────────────────────────
@router.get("/auth/google/connect")
def google_connect(
    request: Request,
    business: str = Query("technobuzz"),
):
    """Redirect admin/owner to Google's OAuth 2.0 consent screen."""
    if not is_google_api_configured():
        # Redirect back to admin hub with setup prompt
        return RedirectResponse(
            url=f"/admin/auto-reply?business={business}&error=credentials_required",
            status_code=302,
        )

    # Derive full callback URL based on current host if not strictly configured
    callback_url = str(request.url_for("google_callback"))
    try:
        auth_url = get_google_auth_url(business_key=business, redirect_uri=callback_url)
        return RedirectResponse(url=auth_url, status_code=302)
    except Exception as e:
        logger.error("Failed to generate Google auth URL: %s", str(e))
        return RedirectResponse(
            url=f"/admin/auto-reply?business={business}&error={urllib.parse.quote(str(e))}",
            status_code=302,
        )


# ─── 2. Google OAuth 2.0 Callback ─────────────────────────────────────────────
@router.get("/auth/google/callback", name="google_callback")
def google_callback(
    request: Request,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Handle OAuth redirect from Google, store tokens, and link business."""
    if error:
        logger.warning("Google OAuth error received: %s", error)
        return RedirectResponse(
            url=f"/admin/auto-reply?error=google_denied_{urllib.parse.quote(error)}",
            status_code=302,
        )

    if not code:
        return RedirectResponse(url="/admin/auto-reply?error=missing_code", status_code=302)

    # Decode state parameter to extract business_key
    business_key = "technobuzz"
    if state:
        try:
            state_json = json.loads(urllib.parse.unquote(state))
            business_key = state_json.get("business_key", "technobuzz")
        except Exception as e:
            logger.warning("Could not parse state JSON '%s': %s", state, str(e))

    callback_url = str(request.url_for("google_callback"))

    try:
        token_data = exchange_code_for_tokens(code, redirect_uri=callback_url)
        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        expires_in = token_data.get("expires_in", 3600)

        # Get Google Profile Info
        user_info = fetch_google_user_info(access_token)
        account_email = user_info.get("email", "")

        # Discover Business Locations
        locations = fetch_accounts_and_locations(access_token)
        google_acc_id = ""
        google_loc_id = ""
        loc_title = ""

        if locations:
            google_acc_id = locations[0].get("account_id", "")
            google_loc_id = locations[0].get("location_id", "")
            loc_title = locations[0].get("title", "")

        # Find or create GoogleBusinessAccount record
        account = db.query(GoogleBusinessAccount).filter_by(business_key=business_key).first()
        if not account:
            account = GoogleBusinessAccount(business_key=business_key)
            db.add(account)

        account.access_token = access_token
        if refresh_token:
            account.refresh_token = refresh_token
        account.token_expiry = datetime.utcnow() + timedelta(seconds=expires_in - 120)
        account.account_email = account_email
        account.google_account_id = google_acc_id
        account.google_location_id = google_loc_id
        account.location_name = loc_title
        account.is_connected = True
        account.auto_reply_enabled = True
        account.last_sync_status = "success"
        account.error_message = ""
        account.updated_at = datetime.utcnow()
        db.commit()

        logger.info(
            "Successfully connected Google Business Profile for business '%s' (Location: %s)",
            business_key,
            loc_title or "Pending Discovery",
        )

        return RedirectResponse(
            url=f"/admin/auto-reply?business={business_key}&connected=true",
            status_code=302,
        )

    except Exception as e:
        logger.error("OAuth callback processing failed: %s", str(e), exc_info=True)
        return RedirectResponse(
            url=f"/admin/auto-reply?business={business_key}&error={urllib.parse.quote(str(e))}",
            status_code=302,
        )


# ─── 3. Check Connection Status ───────────────────────────────────────────────
@router.get("/api/google-business/status/{business_key}")
def get_connection_status(
    business_key: str,
    db: Session = Depends(get_db),
):
    """Return live connection and sync status for the given business."""
    configured = is_google_api_configured()
    account = db.query(GoogleBusinessAccount).filter_by(business_key=business_key).first()

    if not account or not account.is_connected:
        return {
            "business_key": business_key,
            "is_configured": configured,
            "is_connected": False,
            "account_email": "",
            "location_name": "",
            "auto_reply_enabled": False,
            "last_synced_at": None,
            "last_sync_status": "never",
            "error_message": account.error_message if account else "",
        }

    return {
        "business_key": business_key,
        "is_configured": configured,
        "is_connected": True,
        "account_email": account.account_email,
        "location_name": account.location_name or "Verified Location",
        "auto_reply_enabled": account.auto_reply_enabled,
        "last_synced_at": account.last_synced_at.isoformat() if account.last_synced_at else None,
        "last_sync_status": account.last_sync_status,
        "error_message": account.error_message,
    }


# ─── 4. Trigger Immediate Manual Sync & Auto-Reply ───────────────────────────
@router.post("/api/google-business/sync/{business_key}")
def trigger_sync(
    business_key: str,
    db: Session = Depends(get_db),
):
    """Trigger server-side review check and post AI replies."""
    result = sync_and_auto_reply_for_business(db, business_key)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Sync failed"))
    return result


# ─── 5. Toggle Server-Side Auto-Pilot ────────────────────────────────────────
@router.post("/api/google-business/toggle/{business_key}")
def toggle_autopilot(
    business_key: str,
    payload: ToggleAutoPilotRequest,
    db: Session = Depends(get_db),
):
    """Turn 24/7 cloud auto-replying on or off."""
    account = db.query(GoogleBusinessAccount).filter_by(business_key=business_key).first()
    if not account:
        raise HTTPException(status_code=404, detail="Google Business Account not found.")

    account.auto_reply_enabled = payload.enabled
    db.commit()
    return {"success": True, "auto_reply_enabled": account.auto_reply_enabled}


# ─── 6. Disconnect Google Account ─────────────────────────────────────────────
@router.post("/api/google-business/disconnect/{business_key}")
def disconnect_google(
    business_key: str,
    db: Session = Depends(get_db),
):
    """Disconnect Google account and revoke access for this business."""
    account = db.query(GoogleBusinessAccount).filter_by(business_key=business_key).first()
    if account:
        account.is_connected = False
        account.access_token = ""
        account.refresh_token = ""
        account.last_sync_status = "disconnected"
        db.commit()

    return {"success": True, "message": "Google Business Profile disconnected."}


# ─── 7. Save OAuth Credentials to .env ────────────────────────────────────────
@router.post("/api/google-business/save-creds")
def save_google_credentials(payload: SaveCredentialsRequest):
    """Save Google Client ID and Secret to .env file dynamically."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    lines = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    cid_set = False
    sec_set = False
    new_lines = []

    for line in lines:
        if line.startswith("GOOGLE_CLIENT_ID="):
            new_lines.append(f"GOOGLE_CLIENT_ID={payload.client_id.strip()}\n")
            cid_set = True
        elif line.startswith("GOOGLE_CLIENT_SECRET="):
            new_lines.append(f"GOOGLE_CLIENT_SECRET={payload.client_secret.strip()}\n")
            sec_set = True
        else:
            new_lines.append(line)

    if not cid_set:
        new_lines.append(f"\nGOOGLE_CLIENT_ID={payload.client_id.strip()}\n")
    if not sec_set:
        new_lines.append(f"GOOGLE_CLIENT_SECRET={payload.client_secret.strip()}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    os.environ["GOOGLE_CLIENT_ID"] = payload.client_id.strip()
    os.environ["GOOGLE_CLIENT_SECRET"] = payload.client_secret.strip()

    logger.info("Successfully updated GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env")
    return {"success": True, "message": "Google Cloud credentials saved successfully."}
