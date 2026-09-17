"""
services/google_business_service.py
-----------------------------------
Google Business Profile API (Direct Cloud Auto-Replier) Service.

Features:
    1. OAuth 2.0 Authorization Flow (Offline access with Refresh Tokens).
    2. Automatic Access Token Refresh when expired.
    3. Google Business Accounts & Locations Discovery.
    4. Fetch unreplied customer reviews directly from Google Cloud API.
    5. Generate contextual Gemini AI replies (Marathi, English, Hindi).
    6. Post replies directly to Google Business Profile server-to-server.
    7. Multi-tenant background synchronization & database audit logging.
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
    client_id = (os.getenv("GOOGLE_CLIENT_ID") or cfg.GOOGLE_CLIENT_ID or "").strip()
    client_secret = (os.getenv("GOOGLE_CLIENT_SECRET") or cfg.GOOGLE_CLIENT_SECRET or "").strip()
    redirect_uri = (os.getenv("GOOGLE_REDIRECT_URI") or cfg.GOOGLE_REDIRECT_URI or "").strip()
    return client_id, client_secret, redirect_uri


def is_google_api_configured() -> bool:
    """Check if Google OAuth credentials are set in environment/config."""
    client_id, client_secret, _ = _get_credentials()
    return bool(client_id and client_secret)


# ─── 1. Build OAuth Authorization URL ─────────────────────────────────────────
def get_google_auth_url(business_key: str, redirect_uri: Optional[str] = None) -> str:
    """Build the Google OAuth 2.0 login URL for connecting a business profile."""
    client_id, _, default_redirect = _get_credentials()
    target_redirect = redirect_uri or default_redirect
    if not client_id:
        raise ValueError("GOOGLE_CLIENT_ID is not configured. Please add it to your .env file.")

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
            data = json.loads(resp.read().decode("utf-8"))
            return data
    except Exception as e:
        logger.error("exchange_code_for_tokens failed: %s", str(e))
        raise RuntimeError(f"Failed to exchange Google OAuth code: {str(e)}") from e


# ─── 3. Refresh Access Token Automatically ───────────────────────────────────
def refresh_access_token(account: GoogleBusinessAccount, db: Session) -> str:
    """Use refresh token to renew expired access token."""
    client_id, client_secret, _ = _get_credentials()
    if not account.refresh_token:
        raise RuntimeError("No refresh token available for this account. Re-authentication required.")

    payload = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": account.refresh_token,
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
            data = json.loads(resp.read().decode("utf-8"))
            new_token = data.get("access_token")
            expires_in = data.get("expires_in", 3600)
            account.access_token = new_token
            account.token_expiry = datetime.utcnow() + timedelta(seconds=expires_in - 120)
            db.commit()
            logger.info("Successfully refreshed Google access token for business '%s'", account.business_key)
            return new_token
    except Exception as e:
        logger.error("refresh_access_token error for business '%s': %s", account.business_key, str(e))
        account.is_connected = False
        account.last_sync_status = "error"
        account.error_message = f"Token refresh failed: {str(e)}"
        db.commit()
        raise RuntimeError(f"Failed to refresh Google token: {str(e)}") from e


def get_valid_access_token(account: GoogleBusinessAccount, db: Session) -> str:
    """Return a valid, non-expired access token, refreshing it if necessary."""
    if not account.access_token:
        return refresh_access_token(account, db)

    now = datetime.utcnow()
    if account.token_expiry and account.token_expiry <= now:
        logger.info("Access token expired for '%s'. Refreshing now...", account.business_key)
        return refresh_access_token(account, db)

    return account.access_token


# ─── 4. Fetch Google User & Profile Info ──────────────────────────────────────
def fetch_google_user_info(access_token: str) -> Dict[str, Any]:
    """Retrieve Google user account profile (email, name)."""
    req = urllib.request.Request(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.warning("fetch_google_user_info warning: %s", str(e))
        return {}


# ─── 5. Fetch Accounts & Business Locations ──────────────────────────────────
def fetch_accounts_and_locations(access_token: str) -> List[Dict[str, Any]]:
    """Query Google Business Profile API to find all accounts and managed locations."""
    locations_list = []
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    # 1. Fetch Accounts
    req_acc = urllib.request.Request(MYBUSINESS_ACCOUNTS_URL, headers=headers)
    accounts = []
    try:
        with urllib.request.urlopen(req_acc, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            accounts = data.get("accounts", [])
    except Exception as e:
        logger.warning("fetch accounts via MyBusinessAccountManagement failed: %s", str(e))

    # 2. For each account, fetch locations
    for acc in accounts:
        acc_name = acc.get("name", "")  # e.g., "accounts/123456789"
        acc_id = acc_name.replace("accounts/", "")
        loc_url = f"https://mybusinessbusinessinformation.googleapis.com/v1/{acc_name}/locations?readMask=name,title,storefrontAddress"

        req_loc = urllib.request.Request(loc_url, headers=headers)
        try:
            with urllib.request.urlopen(req_loc, timeout=15) as resp:
                loc_data = json.loads(resp.read().decode("utf-8"))
                for loc in loc_data.get("locations", []):
                    locations_list.append({
                        "account_name": acc_name,
                        "account_id": acc_id,
                        "location_name": loc.get("name", ""),
                        "location_id": loc.get("name", "").split("/")[-1],
                        "title": loc.get("title", ""),
                        "address": loc.get("storefrontAddress", {}).get("addressLines", [""])[0] if loc.get("storefrontAddress") else "",
                    })
        except Exception as e:
            logger.warning("fetch locations for account %s failed: %s", acc_name, str(e))

    return locations_list


# ─── 6. Fetch Unreplied Reviews from Google API ──────────────────────────────
def fetch_unreplied_reviews(
    account_id: str,
    location_id: str,
    access_token: str,
    page_size: int = 50,
) -> List[Dict[str, Any]]:
    """Fetch unreplied reviews from Google Business Profile API."""
    acc_clean = account_id if account_id.startswith("accounts/") else f"accounts/{account_id}"
    loc_clean = location_id if location_id.startswith("locations/") else f"locations/{location_id}"

    # Google My Business v4 Reviews endpoint
    reviews_url = f"https://mybusiness.googleapis.com/v4/{acc_clean}/{loc_clean}/reviews?pageSize={page_size}"
    headers = {"Authorization": f"Bearer {access_token}"}

    req = urllib.request.Request(reviews_url, headers=headers)
    unreplied = []

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            reviews = data.get("reviews", [])
            for r in reviews:
                # If review has no reviewReply, or reply comment is empty, it needs an auto-reply
                has_reply = bool(r.get("reviewReply") and r.get("reviewReply", {}).get("comment"))
                if not has_reply:
                    star_mapping = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}
                    rating_raw = r.get("starRating", "FIVE")
                    rating_num = star_mapping.get(rating_raw, 5)

                    unreplied.append({
                        "review_id": r.get("reviewId") or r.get("name", "").split("/")[-1],
                        "reviewer_name": r.get("reviewer", {}).get("displayName", ""),
                        "rating": rating_num,
                        "comment": r.get("comment", ""),
                        "create_time": r.get("createTime", ""),
                        "update_time": r.get("updateTime", ""),
                    })
            logger.info("Found %d unreplied reviews for %s/%s", len(unreplied), acc_clean, loc_clean)
            return unreplied
    except Exception as e:
        logger.error("fetch_unreplied_reviews error for %s/%s: %s", acc_clean, loc_clean, str(e))
        return []


# ─── 7. Post Reply to Google API ──────────────────────────────────────────────
def post_review_reply(
    account_id: str,
    location_id: str,
    review_id: str,
    reply_text: str,
    access_token: str,
) -> bool:
    """Post an owner reply to a Google Business Review via Google REST API."""
    acc_clean = account_id if account_id.startswith("accounts/") else f"accounts/{account_id}"
    loc_clean = location_id if location_id.startswith("locations/") else f"locations/{location_id}"
    rev_clean = review_id if not review_id.startswith("reviews/") else review_id.replace("reviews/", "")

    reply_url = f"https://mybusiness.googleapis.com/v4/{acc_clean}/{loc_clean}/reviews/{rev_clean}/reply"
    payload = json.dumps({"comment": reply_text}).encode("utf-8")

    req = urllib.request.Request(
        reply_url,
        data=payload,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="PUT",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.getcode()
            if status in (200, 201):
                logger.info("Successfully posted review reply to Google for review %s", rev_clean)
                return True
            return False
    except Exception as e:
        logger.error("post_review_reply failed for review %s: %s", rev_clean, str(e))
        return False


# ─── 8. Orchestrate Sync & Auto-Reply for a Business ─────────────────────────
def sync_and_auto_reply_for_business(db: Session, business_key: str) -> Dict[str, Any]:
    """
    1. Fetch GoogleBusinessAccount for business_key.
    2. Get valid access token.
    3. Fetch unreplied reviews from Google Cloud.
    4. Generate multi-lingual Gemini AI reply for each.
    5. Post reply to Google API server-to-server.
    6. Log audit records in database.
    """
    account = db.query(GoogleBusinessAccount).filter_by(business_key=business_key).first()
    if not account or not account.is_connected:
        return {"success": False, "error": "Google Business Profile is not connected for this business."}

    b_config = db.query(BusinessConfigModel).filter_by(key=business_key).first()
    b_dict = {
        "name": b_config.name if b_config else account.location_name or business_key,
        "scope": b_config.scope if b_config else "",
        "mobile": b_config.mobile if b_config else "",
        "email": b_config.email if b_config else "",
    }

    try:
        access_token = get_valid_access_token(account, db)
    except Exception as e:
        return {"success": False, "error": f"Authentication failed: {str(e)}"}

    if not account.google_account_id or not account.google_location_id:
        # Try to auto-discover location
        locs = fetch_accounts_and_locations(access_token)
        if locs:
            account.google_account_id = locs[0]["account_id"]
            account.google_location_id = locs[0]["location_id"]
            account.location_name = locs[0]["title"]
            db.commit()
        else:
            return {"success": False, "error": "No verified Google Business location found in connected Google Account."}

    unreplied = fetch_unreplied_reviews(account.google_account_id, account.google_location_id, access_token)
    replied_count = 0
    replied_details = []

    for item in unreplied:
        reviewer = sanitize_reviewer_name(item.get("reviewer_name"))
        rating = item.get("rating", 5)
        comment = item.get("comment", "")
        review_id = item.get("review_id", "")

        # Generate contextual Gemini AI response
        ai_result = generate_review_reply(
            business_name=b_dict["name"],
            reviewer_name=reviewer,
            rating=rating,
            review_text=comment,
            business_context=b_dict,
            language="auto",
            tone="professional_warm",
            auto_sign=True,
        )

        reply_text = ai_result.get("reply") or ai_result.get("reply_text") or ""
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
            tone="professional_warm",
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
        "unreplied_found": len(unreplied),
        "replied_count": replied_count,
        "replies": replied_details,
        "last_synced_at": account.last_synced_at.isoformat(),
    }


# ─── 9. Background Worker Sync for All Connected Businesses ──────────────────
def sync_all_active_businesses(db: Session) -> Dict[str, Any]:
    """Periodic job that scans and auto-replies for all businesses connected via Google API."""
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
            logger.error("Error during background sync for business '%s': %s", acc.business_key, str(e))
            results[acc.business_key] = {"success": False, "error": str(e)}

    return results
