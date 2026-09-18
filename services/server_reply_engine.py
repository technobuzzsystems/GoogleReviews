"""
services/server_reply_engine.py
---------------------------------
24/7 Server-Side Headless Auto-Replier Engine powered by Playwright and Gemini AI.

Features:
    - 100% Zero-Client-Extension: Runs completely on the backend server.
    - Zero Google API Approval Needed: Operates headlessly via authenticated browser automation.
    - Cross-Platform: Works on Windows (Local) and Linux/Docker (Production VPS).
    - Multi-Tenant: Supports multiple businesses (TechnoBuzz, Boardwale, Rutuja Battery, etc.).
    - Multi-Lingual: Generates contextual replies in Marathi (मराठी), Hindi (हिन्दी), and English using Gemini.
    - Duplicate Prevention: Verifies against database ReviewReplyLog before posting.
    - Audit Trail: Logs all operations with source="server_engine" and status="sent".
"""

import asyncio
import json
import logging
import os
import platform
import re
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from sqlalchemy.orm import Session

from database import SessionLocal
from models.domain_models import BusinessConfigModel, ReviewReplyLog
from services.reply_service import generate_review_reply, sanitize_reviewer_name
from utils.google_review import extract_place_id

logger = logging.getLogger(__name__)


def get_chrome_executable() -> Optional[str]:
    """Find Google Chrome or Chromium executable across Windows and Linux."""
    system = platform.system().lower()

    if system == "windows":
        candidate_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
        ]
        for p in candidate_paths:
            if os.path.exists(p):
                return p

    elif system == "linux":
        candidate_paths = [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
            "/snap/bin/chromium",
        ]
        for p in candidate_paths:
            if os.path.exists(p):
                return p

    return None


def get_browser_user_data_dir() -> str:
    """Return dedicated storage path for persistent server browser cookies and state."""
    if platform.system().lower() == "windows" and os.path.exists("D:\\"):
        base_dir = os.path.join("D:\\google-reviews\\GoogleReviews", ".server_browser_data")
    else:
        base_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".server_browser_data")

    os.makedirs(base_dir, exist_ok=True)
    return base_dir


class ServerReplyEngine:
    """Headless browser engine that checks and auto-replies to Google Reviews server-side."""

    def __init__(self):
        self.user_data_dir = get_browser_user_data_dir()
        self.chrome_path = get_chrome_executable()
        self.is_running = False

    def _build_search_url_for_business(self, biz: BusinessConfigModel) -> str:
        """Construct direct Google Maps / Google Reviews URL for a business."""
        place_id = biz.place_id or extract_place_id(biz.google_review_url or "")
        if place_id:
            return f"https://www.google.com/maps/place/?q=place_id:{place_id}&hl=en"

        name = biz.name or "TechnoBuzz Systems"
        area = getattr(biz, "area", "") or "Pune"
        query = f"{name} {area}".strip()
        return f"https://www.google.com/search?q={quote_plus(query)}&hl=en"

    def is_session_authenticated(self, business_key: str) -> bool:
        """Check if persistent storage contains valid Google cookies."""
        prof_dir = os.path.join(self.user_data_dir, business_key)
        if not os.path.exists(prof_dir):
            return False
        # Check for Cookies / Network / Default directory
        for item in ["Default", "Network", "Cookies"]:
            if os.path.exists(os.path.join(prof_dir, item)):
                return True
        return False

    def launch_login_window(self, business_key: str) -> Dict[str, Any]:
        """Launch a native visible Chrome browser window allowing the user to sign into Google once."""
        import subprocess

        prof_dir = os.path.join(self.user_data_dir, business_key)
        os.makedirs(prof_dir, exist_ok=True)

        # Clean any stale lock files
        for lock in ["SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile"]:
            lp = os.path.join(prof_dir, lock)
            if os.path.exists(lp):
                try:
                    os.remove(lp)
                except Exception:
                    pass

        if not self.chrome_path or not os.path.exists(self.chrome_path):
            return {"success": False, "error": "Google Chrome executable not found on system."}

        logger.info("[ServerEngine] Launching native Chrome window for Google Login on '%s'...", business_key)

        try:
            cmd = [
                self.chrome_path,
                f"--user-data-dir={prof_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "https://accounts.google.com/signin",
            ]
            proc = subprocess.Popen(cmd)
            return {
                "success": True,
                "pid": proc.pid,
                "message": f"Google Sign-In Chrome window opened on your screen (PID: {proc.pid}). Please sign in and then close the window when done.",
            }
        except Exception as e:
            logger.error("[ServerEngine] Failed to launch Chrome: %s", e)
            return {"success": False, "error": str(e)}

    def process_business_reviews(
        self,
        db: Session,
        business_key: str,
        headless: bool = True,
    ) -> Dict[str, Any]:
        """
        Check reviews for a single business, generate AI replies for unreplied ones,
        submit replies on Google, and record audit logs.
        """
        from playwright.sync_api import sync_playwright

        biz = db.query(BusinessConfigModel).filter(BusinessConfigModel.key == business_key).first()
        if not biz:
            return {"success": False, "error": f"Business key '{business_key}' not found in database."}

        company_name = biz.name or business_key.title()
        search_url = self._build_search_url_for_business(biz)

        logger.info("[ServerEngine] Starting server review check for '%s' (%s)", company_name, business_key)

        unreplied_found = 0
        replied_count = 0
        replied_items = []
        is_signed_in = False

        try:
            if sys.platform == "win32":
                try:
                    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
                except Exception:
                    pass

            with sync_playwright() as p:
                launch_args = [
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                    "--lang=en-US,en,mr,hi",
                ]

                prof_dir = os.path.join(self.user_data_dir, business_key)
                os.makedirs(prof_dir, exist_ok=True)
                for lock in ["SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile"]:
                    lp = os.path.join(prof_dir, lock)
                    if os.path.exists(lp):
                        try:
                            os.remove(lp)
                        except Exception:
                            pass

                # Launch persistent browser context (stores login cookies & session)
                context = p.chromium.launch_persistent_context(
                    user_data_dir=prof_dir,
                    executable_path=self.chrome_path,
                    headless=headless,
                    args=launch_args,
                    viewport={"width": 1280, "height": 900},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                )

                page = context.new_page()
                # Primary target: Google Business Profile Reviews Hub (Centralized, 100% accurate for authenticated managers)
                gbp_reviews_url = "https://business.google.com/reviews"
                page.goto(gbp_reviews_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)

                # Check if signed in
                sign_in_btn = page.query_selector("a[href*='accounts.google.com/ServiceLogin'], a:has-text('Sign in'), a:has-text('साइन इन')")
                is_signed_in = sign_in_btn is None and "signin" not in page.url.lower()

                # If redirected or not logged into GBP, fallback to direct search / maps url
                if not is_signed_in or "locations" in page.url or "signin" in page.url:
                    page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(3000)
                    
                    # Try clicking reviews tab & sorting by newest on maps
                    try:
                        rev_tab = page.query_selector("button[aria-label*='Reviews'], button[aria-label*='रिव्ह्यू'], div[role='tab']:has-text('Reviews'), button:has-text('Reviews')")
                        if rev_tab and rev_tab.is_visible():
                            rev_tab.click()
                            page.wait_for_timeout(2000)
                        sort_btn = page.query_selector("button[data-value='Sort'], button[aria-label*='Sort'], button:has-text('Sort')")
                        if sort_btn and sort_btn.is_visible():
                            sort_btn.click()
                            page.wait_for_timeout(1000)
                            newest_chip = page.query_selector("div[role='menuitemradio']:has-text('Newest'), div:has-text('Newest')")
                            if newest_chip:
                                newest_chip.click()
                                page.wait_for_timeout(2000)
                    except Exception:
                        pass

                # Scan for 'Reply' buttons (Available when signed in as Business Manager)
                reply_buttons = page.query_selector_all("button:has-text('Reply'), [aria-label*='Reply'], button:has-text('उत्तर द्या'), div[role='button']:has-text('Reply')")
                logger.info("[ServerEngine] Found %d 'Reply' buttons on page for '%s' (Signed In: %s)", len(reply_buttons), company_name, is_signed_in)

                if reply_buttons:
                    # Authenticated mode: process unreplied reviews
                    for btn in reply_buttons:
                        try:
                            # Locate the review container card
                            parent = btn.evaluate_handle("el => el.closest('[data-review-id], div[class*=\"review\"], div[jscontroller], div.jftiEf, div[role=\"region\"]') || el.parentElement.parentElement")
                            if not parent:
                                continue

                            reviewer_raw = page.evaluate("el => { const n = el.querySelector('[class*=\"reviewer\"], [class*=\"header\"], .d4r55, .TSUbDb, .WNxFfd, h3, b, strong, a'); return n ? n.innerText : ''; }", parent)
                            clean_name = sanitize_reviewer_name(reviewer_raw)

                            review_text = page.evaluate("el => { const t = el.querySelector('[class*=\"snippet\"], [class*=\"comment\"], .wiI7Mc, .Jtu6Td, .MyEned, span[jsname], p'); return t ? t.innerText : ''; }", parent)
                            review_text = (review_text or "").strip()

                            rating_stars = page.evaluate("el => { const s = el.querySelector('[aria-label*=\"star\"], [aria-label*=\"Star\"], .Fam1ne'); return s ? s.getAttribute('aria-label') : ''; }", parent)
                            rating = 5
                            if rating_stars:
                                m = re.search(r"(\d)", str(rating_stars))
                                if m:
                                    rating = int(m.group(1))

                            existing_log = db.query(ReviewReplyLog).filter(
                                ReviewReplyLog.business_key == business_key,
                                ReviewReplyLog.reviewer_name == (clean_name or "Valued Customer"),
                                ReviewReplyLog.status == "sent",
                            ).first()

                            if existing_log and (not review_text or review_text in existing_log.review_text or existing_log.review_text in review_text):
                                logger.info("[ServerEngine] Skipping already sent reply for '%s'", clean_name)
                                continue

                            unreplied_found += 1
                            logger.info("[ServerEngine] Crafting AI reply for '%s' (Rating: %d): '%s'", clean_name or "Customer", rating, review_text[:60])

                            b_dict = {
                                "name": company_name,
                                "scope": biz.scope or "Software, services, and IT solutions.",
                                "mobile": getattr(biz, "mobile", "") or "",
                                "email": getattr(biz, "email", "") or "",
                                "reply_signature": getattr(biz, "reply_signature", "") or f"— Team {company_name}",
                            }

                            reply_tone = getattr(biz, "reply_tone", "professional_warm") or "professional_warm"
                            lang_mode = getattr(biz, "reply_language_mode", "auto") or "auto"

                            ai_res = generate_review_reply(
                                review_text=review_text or "Great experience",
                                reviewer_name=clean_name,
                                rating=rating,
                                business_context=b_dict,
                                business_name=company_name,
                                language=lang_mode,
                                tone=reply_tone,
                                auto_sign=True,
                            )

                            reply_str = ai_res.get("reply", "").strip()
                            if not reply_str:
                                continue

                            # Click 'Reply' to open inline editor
                            btn.click()
                            page.wait_for_timeout(1500)

                            textarea = page.query_selector("textarea[aria-label='Your reply'], textarea, div[contenteditable='true']")
                            if textarea and textarea.is_visible():
                                textarea.click()
                                page.wait_for_timeout(300)
                                textarea.fill(reply_str)
                                textarea.dispatch_event("input")
                                textarea.dispatch_event("change")
                                page.wait_for_timeout(1000)

                                submit_btn = page.query_selector("button:has-text('Post reply'), button:has-text('Post'), button:has-text('Reply'), button:has-text('उत्तर द्या'), button:has-text('Send')")
                                if submit_btn and submit_btn.is_visible():
                                    box = submit_btn.bounding_box()
                                    if box:
                                        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                                    else:
                                        submit_btn.click()
                                    page.wait_for_timeout(4000)
                                    logger.info("[ServerEngine] Successfully posted reply on Google for '%s'!", clean_name)

                            log_entry = ReviewReplyLog(
                                business_key=business_key,
                                reviewer_name=clean_name or "Valued Customer",
                                rating=rating,
                                review_text=review_text,
                                reply_text=reply_str,
                                language=ai_res.get("language", "auto"),
                                tone=reply_tone,
                                status="sent",
                                source="server_engine",
                                created_at=datetime.utcnow(),
                            )
                            db.add(log_entry)
                            db.commit()

                            replied_count += 1
                            replied_items.append({
                                "reviewer_name": clean_name or "Valued Customer",
                                "rating": rating,
                                "review_text": review_text,
                                "reply_text": reply_str,
                            })

                        except Exception as item_err:
                            logger.warning("[ServerEngine] Error replying to review: %s", item_err)
                            continue

                else:
                    # Scan review cards in public view (fallback / read-only)
                    review_cards = page.query_selector_all("div.jftiEf, div.WMH2pe, div[data-review-id], div.gws-localreviews__google-review")
                    logger.info("[ServerEngine] Found %d public review cards for '%s'", len(review_cards), company_name)

                    for card in review_cards:
                        try:
                            # Check if owner already responded
                            has_response = card.query_selector("div.CDe7pd")
                            card_text = card.inner_text() if card else ""
                            if has_response or "Response from the owner" in card_text or "मालकाकडून प्रतिसाद" in card_text:
                                continue

                            reviewer_raw = page.evaluate("el => { const n = el.querySelector('.d4r55, .TSUbDb, [class*=\"reviewer\"], [class*=\"header\"], h3, a'); return n ? n.innerText : ''; }", card)
                            clean_name = sanitize_reviewer_name(reviewer_raw)

                            review_text = page.evaluate("el => { const t = el.querySelector('.wiI7Mc, .MyEned, [class*=\"snippet\"], [class*=\"comment\"], span[jsname]'); return t ? t.innerText : ''; }", card)
                            review_text = (review_text or "").strip()

                            rating_stars = page.evaluate("el => { const s = el.querySelector('[aria-label*=\"star\"], [aria-label*=\"Star\"], .Fam1ne'); return s ? s.getAttribute('aria-label') : ''; }", card)
                            rating = 5
                            if rating_stars:
                                m = re.search(r"(\d)", str(rating_stars))
                                if m:
                                    rating = int(m.group(1))

                            if not clean_name and not review_text:
                                continue

                            existing_log = db.query(ReviewReplyLog).filter(
                                ReviewReplyLog.business_key == business_key,
                                ReviewReplyLog.reviewer_name == (clean_name or "Valued Customer"),
                                ReviewReplyLog.status == "sent",
                            ).first()

                            if existing_log:
                                continue

                            unreplied_found += 1

                            # Generate AI reply draft
                            b_dict = {
                                "name": company_name,
                                "scope": biz.scope or "Software, services, and IT solutions.",
                                "mobile": getattr(biz, "mobile", "") or "",
                                "email": getattr(biz, "email", "") or "",
                                "reply_signature": getattr(biz, "reply_signature", "") or f"— Team {company_name}",
                            }
                            reply_tone = getattr(biz, "reply_tone", "professional_warm") or "professional_warm"
                            lang_mode = getattr(biz, "reply_language_mode", "auto") or "auto"

                            ai_res = generate_review_reply(
                                review_text=review_text or "Great experience",
                                reviewer_name=clean_name,
                                rating=rating,
                                business_context=b_dict,
                                business_name=company_name,
                                language=lang_mode,
                                tone=reply_tone,
                                auto_sign=True,
                            )

                            reply_str = ai_res.get("reply", "").strip()

                            log_entry = ReviewReplyLog(
                                business_key=business_key,
                                reviewer_name=clean_name or "Valued Customer",
                                rating=rating,
                                review_text=review_text,
                                reply_text=reply_str,
                                language=ai_res.get("language", "auto"),
                                tone=reply_tone,
                                status="generated",
                                source="server_engine",
                                created_at=datetime.utcnow(),
                            )
                            db.add(log_entry)
                            db.commit()

                            replied_items.append({
                                "reviewer_name": clean_name or "Valued Customer",
                                "rating": rating,
                                "review_text": review_text,
                                "reply_text": reply_str,
                            })

                        except Exception as card_err:
                            logger.warning("[ServerEngine] Error parsing review card: %s", card_err)
                            continue

                context.close()

            logger.info("[ServerEngine] Completed check for '%s'. Unreplied: %d, Replied: %d, Signed In: %s", company_name, unreplied_found, replied_count, is_signed_in)
            return {
                "success": True,
                "business_key": business_key,
                "business_name": company_name,
                "is_signed_in": is_signed_in,
                "unreplied_found": unreplied_found,
                "replied_count": replied_count,
                "items": replied_items,
                "requires_login": not is_signed_in and unreplied_found > 0 and replied_count == 0,
                "timestamp": datetime.utcnow().isoformat(),
            }

        except Exception as e:
            logger.error("[ServerEngine] Headless execution failed for '%s': %s", company_name, str(e), exc_info=True)
            return {
                "success": False,
                "business_key": business_key,
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat(),
            }


def sync_business_server_side(db: Session, business_key: str) -> Dict[str, Any]:
    """Helper to run single business server sync synchronously."""
    engine = ServerReplyEngine()
    return engine.process_business_reviews(db, business_key)


def sync_all_active_businesses(db: Session) -> List[Dict[str, Any]]:
    """
    Helper to iterate through authenticated businesses in the database.
    Only launches browser for businesses that have active 1-Time Google Login sessions.
    """
    businesses = db.query(BusinessConfigModel).all()
    results = []
    engine = ServerReplyEngine()

    for biz in businesses:
        # Strictly skip businesses without 1-Time Google Login session to avoid unnecessary delays
        if not engine.is_session_authenticated(biz.key):
            continue

        try:
            logger.info("[AutoSync] Checking reviews for authenticated business '%s' (%s)...", biz.name or biz.key, biz.key)
            res = engine.process_business_reviews(db, biz.key)
            results.append(res)
        except Exception as e:
            logger.error("[AutoSync] Error checking business '%s': %s", biz.key, e, exc_info=True)
            results.append({"business_key": biz.key, "success": False, "error": str(e)})

    return results


def launch_business_login(business_key: str) -> Dict[str, Any]:
    """Helper to launch Google Login window for a business."""
    engine = ServerReplyEngine()
    return engine.launch_login_window(business_key)
