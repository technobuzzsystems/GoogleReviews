"""
services/google_business_service.py
-----------------------------------
Google Business Profile API (Direct Cloud Auto-Replier) Service.

Features:
    1. Multi-Tenant OAuth 2.0 Authorization Flow (Offline access with Refresh Tokens).
    2. Automatic Access Token Refresh when expired.
    3. Google Business Accounts & Locations Discovery per business.
    4. Fetch unreplied customer reviews directly from Google Cloud API.
    5. Generate contextual Gemini AI replies with SEO intelligence.
    6. Post replies directly to Google Business Profile server-to-server.
    7. Background synchronization & database audit logging.
"""

import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from sqlalchemy.orm import Session

from config import get_config
from models.domain_models import BusinessConfigModel, GoogleBusinessAccount, ReviewReplyLog
from services.reply_service import generate_review_reply, sanitize_reviewer_name

logger = logging.getLogger(__name__)

# Google OAuth & API Endpoints
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
MYBUSINESS_ACCOUNTS_URL = "https://mybusinessaccountmanagement.googleapis.com/v1/accounts"

# OAuth 2.0 Scopes required for managing Business Profiles and Reviews
REQUIRED_SCOPES = [
    "https://www.googleapis.com/auth/business.manage",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]


def _get_credentials() -> Tuple[str, str, str]:
    """Retrieve Client ID, Client Secret, and Default Redirect URI."""
    load_dotenv(override=True)
    cfg = get_config()
    client_id = (os.getenv("GOOGLE_CLIENT_ID") or getattr(cfg, "GOOGLE_CLIENT_ID", "") or "").strip()
    client_secret = (os.getenv("GOOGLE_CLIENT_SECRET") or getattr(cfg, "GOOGLE_CLIENT_SECRET", "") or "").strip()
    app_base = (getattr(cfg, "APP_BASE_URL", "") or "https://reviewhub.technobuzzsystems.com").rstrip("/")
    redirect_uri = (os.getenv("GOOGLE_REDIRECT_URI") or f"{app_base}/api/google-business/callback").strip()
    return client_id, client_secret, redirect_uri


def is_google_api_configured() -> bool:
    """Check if Google OAuth credentials are set in environment/config."""
    client_id, client_secret, _ = _get_credentials()
    return bool(client_id and client_secret)


# ─── 1. Build OAuth Authorization URL ─────────────────────────────────────────
def get_google_auth_url(business_key: str, redirect_uri: Optional[str] = None) -> str:
    """Build the Google OAuth 2.0 login URL for connecting a specific business profile."""
    client_id, _, default_redirect = _get_credentials()
    target_redirect = redirect_uri or default_redirect
    if not client_id:
        raise ValueError("GOOGLE_CLIENT_ID is not configured. Please add it to your environment.")

    state_payload = json.dumps({"business_key": business_key, "t": int(datetime.utcnow().timestamp())})
    encoded_state = urllib.parse.quote(state_payload)

    params = {
        "client_id": client_id,
        "redirect_uri": target_redirect,
        "response_type": "code",
        "scope": " ".join(REQUIRED_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": encoded_state,
        "include_granted_scopes": "true",
    }
    return f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"


# ─── 2. Exchange Authorization Code for Tokens ────────────────────────────────
def exchange_code_for_tokens(code: str, redirect_uri: Optional[str] = None) -> Dict[str, Any]:
    """Exchange authorization code with Google for access & refresh tokens."""
    client_id, client_secret, default_redirect = _get_credentials()
    target_redirect = redirect_uri or default_redirect

    payload = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": target_redirect,
        "grant_type": "authorization_code",
    }).encode("utf-8")

    req = urllib.request.Request(
        GOOGLE_TOKEN_URL,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.error("exchange_code_for_tokens failed: %s", str(e))
        raise RuntimeError(f"Failed to exchange Google OAuth code: {str(e)}") from e


# ─── 3. Refresh Access Token Automatically ────────────────────────────────────
def refresh_access_token(refresh_token: str) -> Dict[str, Any]:
    """Use refresh_token to obtain a fresh access_token from Google."""
    client_id, client_secret, _ = _get_credentials()

    payload = urllib.parse.urlencode({
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
    }).encode("utf-8")

    req = urllib.request.Request(
        GOOGLE_TOKEN_URL,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.error("refresh_access_token failed: %s", str(e))
        raise RuntimeError(f"Failed to refresh Google access token: {str(e)}") from e


def get_valid_access_token(account: GoogleBusinessAccount, db: Session) -> str:
    """Check expiry and return a guaranteed-valid access token, refreshing if needed."""
    now = datetime.utcnow()
    # Refresh if token expired or expiring within 5 minutes
    if not account.access_token or not account.token_expiry or account.token_expiry <= now + timedelta(minutes=5):
        if not account.refresh_token:
            raise RuntimeError("No refresh token available. Re-authorization required.")

        logger.info("Access token expired for business '%s'. Refreshing...", account.business_key)
        tokens = refresh_access_token(account.refresh_token)

        account.access_token = tokens["access_token"]
        expires_in = tokens.get("expires_in", 3600)
        account.token_expiry = now + timedelta(seconds=expires_in)
        db.commit()

    return account.access_token


# ─── 4. Fetch Google User Info ────────────────────────────────────────────────
def fetch_user_info(access_token: str) -> Dict[str, Any]:
    """Retrieve Google user email and profile info."""
    req = urllib.request.Request(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ─── 5. Discover Google Business Accounts & Locations ─────────────────────────
def fetch_accounts_and_locations(access_token: str) -> List[Dict[str, Any]]:
    """Query Google Business Profile API to discover all accounts and locations."""
    headers = {"Authorization": f"Bearer {access_token}"}
    discovered = []

    req = urllib.request.Request(MYBUSINESS_ACCOUNTS_URL, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            accounts_data = json.loads(resp.read().decode("utf-8"))
            accounts = accounts_data.get("accounts", [])
    except Exception as e:
        logger.warning("Could not list Google Business Accounts: %s", e)
        return []

    for acc in accounts:
        account_name = acc.get("name")  # e.g., 'accounts/123456789'
        if not account_name:
            continue

        loc_url = f"https://mybusinessbusinessinformation.googleapis.com/v1/{account_name}/locations?readMask=name,title,storefrontAddress"
        try:
            loc_req = urllib.request.Request(loc_url, headers=headers, method="GET")
            with urllib.request.urlopen(loc_req, timeout=15) as loc_resp:
                locations_data = json.loads(loc_resp.read().decode("utf-8"))
                for loc in locations_data.get("locations", []):
                    full_name = loc.get("name")  # e.g., 'locations/987654321'
                    discovered.append({
                        "account_id": account_name,
                        "account_display_name": acc.get("accountName", ""),
                        "location_id": full_name,
                        "title": loc.get("title", ""),
                        "address": loc.get("storefrontAddress", {}),
                    })
        except Exception as e:
            logger.warning("Error fetching locations for %s: %s", account_name, e)

    return discovered


# ─── 6. Fetch Unreplied Google Reviews ────────────────────────────────────────
def fetch_unreplied_reviews(account_id: str, location_id: str, access_token: str) -> List[Dict[str, Any]]:
    """
    Fetch customer reviews for a location and return only those without owner replies.
    Endpoint: https://mybusiness.googleapis.com/v4/{account_id}/{location_id}/reviews
    """
    clean_acc = account_id.replace("accounts/", "")
    clean_loc = location_id.replace("locations/", "")
    url = f"https://mybusiness.googleapis.com/v4/accounts/{clean_acc}/locations/{clean_loc}/reviews?pageSize=20"

    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"}, method="GET")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            reviews = data.get("reviews", [])
    except Exception as e:
        logger.error("Failed to fetch Google reviews for %s/%s: %s", account_id, location_id, e)
        return []

    STAR_MAP = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}
    unreplied = []

    for r in reviews:
        # If review already has owner reply, skip
        if r.get("reviewReply"):
            continue

        raw_star = r.get("starRating", "FIVE")
        rating_num = STAR_MAP.get(raw_star, 5)

        unreplied.append({
            "review_id": r.get("reviewId") or r.get("name"),
            "reviewer_name": r.get("reviewer", {}).get("displayName", "Valued Customer"),
            "rating": rating_num,
            "comment": r.get("comment", ""),
            "create_time": r.get("createTime", ""),
        })

    return unreplied


# ─── 7. Post Review Reply to Google Business Profile ──────────────────────────
def post_review_reply(account_id: str, location_id: str, review_id: str, reply_text: str, access_token: str) -> bool:
    """
    Publish an owner reply directly to Google via the Google My Business API.
    Endpoint: PUT https://mybusiness.googleapis.com/v4/{account_id}/{location_id}/reviews/{review_id}/reply
    """
    clean_acc = account_id.replace("accounts/", "")
    clean_loc = location_id.replace("locations/", "")
    clean_rev = review_id.split("/")[-1]

    url = f"https://mybusiness.googleapis.com/v4/accounts/{clean_acc}/locations/{clean_loc}/reviews/{clean_rev}/reply"
    payload = json.dumps({"comment": reply_text}).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="PUT",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status in (200, 201):
                logger.info("Successfully posted Google review reply to review '%s'", clean_rev)
                return True
            return False
    except Exception as e:
        logger.error("Failed to post Google review reply: %s", str(e))
        return False


# ─── 8. Orchestrator: Sync & Auto-Reply for a Specific Business ───────────────
def sync_and_auto_reply_for_business(db: Session, business_key: str) -> Dict[str, Any]:
    """
    End-to-End Orchestrator for an individual business:
    1. Fetch GoogleBusinessAccount for business_key.
    2. Get valid access token.
    3. Fetch unreplied reviews from Google Cloud.
    4. Generate multi-lingual Gemini AI reply with SEO intelligence.
    5. Post reply to Google API server-to-server.
    6. Log audit records in database.
    """
    account = db.query(GoogleBusinessAccount).filter_by(business_key=business_key).first()
    if not account or not account.is_connected:
        return {"success": False, "error": "Google Business Profile is not connected for this business."}

    b_config = db.query(BusinessConfigModel).filter_by(key=business_key).first()
    b_dict = {
        "name": b_config.name if b_config else account.location_name or business_key,
        "business_category": getattr(b_config, "business_category", "") or "Local Business",
        "area": getattr(b_config, "area", "") or "",
        "scope": getattr(b_config, "scope", "") or "",
        "seo_keywords": getattr(b_config, "seo_keywords", "") or "",
        "mobile": getattr(b_config, "mobile", "") or "",
        "email": getattr(b_config, "email", "") or "",
        "reply_signature": getattr(b_config, "reply_signature", "") or f"— Team {b_config.name if b_config else business_key}",
    }

    try:
        access_token = get_valid_access_token(account, db)
    except Exception as e:
        account.last_sync_status = "error"
        account.error_message = f"Authentication error: {str(e)}"
        db.commit()
        return {"success": False, "error": f"Authentication failed: {str(e)}"}

    if not account.google_account_id or not account.google_location_id:
        locs = fetch_accounts_and_locations(access_token)
        if locs:
            account.google_account_id = locs[0]["account_id"]
            account.google_location_id = locs[0]["location_id"]
            account.location_name = locs[0]["title"]
            db.commit()
        else:
            account.last_sync_status = "error"
            account.error_message = "No verified Google Business location found in connected Google Account."
            db.commit()
            return {"success": False, "error": "No verified Google Business location found."}

    unreplied = fetch_unreplied_reviews(account.google_account_id, account.google_location_id, access_token)
    replied_count = 0
    replied_details = []

    tone = getattr(b_config, "reply_tone", "professional_warm") or "professional_warm"
    lang_mode = getattr(b_config, "reply_language_mode", "auto") or "auto"

    for item in unreplied:
        reviewer = sanitize_reviewer_name(item.get("reviewer_name"))
        rating = item.get("rating", 5)
        comment = item.get("comment", "")
        review_id = str(item.get("review_id", ""))

        # Check database log to strictly prevent duplicate replies
        existing_log = (
            db.query(ReviewReplyLog)
            .filter(
                ReviewReplyLog.business_key == business_key,
                ReviewReplyLog.reviewer_name == (reviewer or "Valued Customer"),
                ReviewReplyLog.rating == rating,
                ReviewReplyLog.status == "sent",
            )
            .first()
        )
        if existing_log:
            logger.info("Skipping already replied review for %s by %s", business_key, reviewer)
            continue

        # Generate contextual Gemini AI response with SEO keywords
        ai_result = generate_review_reply(
            business_name=b_dict["name"],
            reviewer_name=reviewer,
            rating=rating,
            review_text=comment,
            business_context=b_dict,
            language=lang_mode,
            tone=tone,
            auto_sign=True,
        )

        reply_text = ai_result.get("reply") or ""
        if not reply_text:
            continue

        # Post reply to Google API
        posted = post_review_reply(
            account_id=account.google_account_id,
            location_id=account.google_location_id,
            review_id=review_id,
            reply_text=reply_text,
            access_token=access_token,
        )

        # Log to Database
        log_entry = ReviewReplyLog(
            business_key=business_key,
            reviewer_name=reviewer or "Valued Customer",
            rating=rating,
            review_text=comment,
            reply_text=reply_text,
            language=ai_result.get("language", "auto"),
            tone=tone,
            status="sent" if posted else "failed",
            source="cloud_api",
            created_at=datetime.utcnow(),
        )
        db.add(log_entry)
        db.commit()

        if posted:
            replied_count += 1
            replied_details.append({
                "reviewer_name": reviewer or "Valued Customer",
                "rating": rating,
                "reply": reply_text,
                "review_id": review_id,
            })

    account.last_synced_at = datetime.utcnow()
    account.last_sync_status = "success"
    account.error_message = ""
    db.commit()

    return {
        "success": True,
        "business_key": business_key,
        "business_name": b_dict["name"],
        "unreplied_found": len(unreplied),
        "replied_count": replied_count,
        "replies": replied_details,
        "last_synced_at": account.last_synced_at.isoformat(),
    }


# ─── 9. Periodic Sync for All Connected Businesses ────────────────────────────
def sync_all_active_businesses(db: Session) -> Dict[str, Any]:
    """Scans and auto-replies for all businesses connected via Google API."""
    connected_accounts = (
        db.query(GoogleBusinessAccount)
        .filter_by(is_connected=True, auto_reply_enabled=True)
        .all()
    )

    results = {}
    for acc in connected_accounts:
        try:
            res = sync_and_auto_reply_for_business(db, acc.business_key)
            results[acc.business_key] = res
        except Exception as e:
            logger.error("Error during auto-reply sync for '%s': %s", acc.business_key, str(e))
            results[acc.business_key] = {"success": False, "error": str(e)}

    return results
