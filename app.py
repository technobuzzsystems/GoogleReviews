"""
app.py
-------
TechnoBuzz AI-Powered QR Code Feedback System — FastAPI Application Entry Point.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from config import get_config
from database import SessionLocal, init_db
from routes.admin_routes import router as admin_router
from routes.feedback_routes import router as feedback_router
from routes.google_business_routes import router as google_business_router
from routes.payment_routes import router as payment_router
from routes.review_reply_routes import router as review_reply_router
from routes.server_engine_routes import router as server_engine_router
from services.auth_service import AuthRedirect
from services.google_business_service import sync_all_active_businesses as sync_google_api_businesses
from services.server_reply_engine import sync_all_active_businesses as sync_server_engine_businesses
from utils.network import build_lan_url

# ─── Logging Setup ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)s]  %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def _run_periodic_sync_in_thread():
    """Execute synchronous database and browser operations in a clean worker thread."""
    db = SessionLocal()
    try:
        # 1. Check server-side headless engine
        sync_server_engine_businesses(db)
        # 2. Check direct Google Cloud API if connected
        sync_google_api_businesses(db)
    except Exception as e:
        logger.error("[ServerEngine] Error in 24/7 sync thread: %s", str(e), exc_info=True)
    finally:
        db.close()


async def _periodic_google_sync_worker():
    """Background worker that continuously scans and auto-replies for all businesses 24/7."""
    logger.info("[OK] 24/7 Server-Side Auto-Replier Background Engine active (60s interval)")
    while True:
        try:
            await asyncio.sleep(60)
            await asyncio.to_thread(_run_periodic_sync_in_thread)
        except asyncio.CancelledError:
            logger.info("24/7 Server-Side Auto-Replier Background Engine stopped.")
            break
        except Exception as e:
            logger.error("Error in 24/7 background review engine: %s", str(e))



@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("[OK] Database initialized")
    sync_task = asyncio.create_task(_periodic_google_sync_worker())
    yield
    sync_task.cancel()
    try:
        await sync_task
    except asyncio.CancelledError:
        pass


def create_app() -> FastAPI:
    """
    Application factory function.
    Creates and returns a fully configured FastAPI application instance.
    """
    config = get_config()
    app = FastAPI(title="TechnoBuzz Feedback System", lifespan=lifespan)
    is_prod = (os.getenv("FLASK_ENV", "development") or "").lower() == "production"

    # Allow CORS from google.com, local development, and production domains
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"https?://.*",
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_middleware(
        SessionMiddleware,
        secret_key=config.SECRET_KEY,
        session_cookie="gr_admin_session",
        same_site="lax",
        https_only=is_prod,
        max_age=60 * 60 * 8,
    )


    app.mount("/static", StaticFiles(directory="static"), name="static")

    app.include_router(feedback_router)
    app.include_router(payment_router)
    app.include_router(admin_router)
    app.include_router(review_reply_router)
    app.include_router(google_business_router)
    app.include_router(server_engine_router)

    logger.info("[OK] Routers registered: feedback, payment, admin, review_reply, google_business, server_engine")


    @app.exception_handler(AuthRedirect)
    async def auth_redirect_handler(request: Request, exc: AuthRedirect):
        return RedirectResponse(url=exc.url, status_code=302)

    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc):
        return JSONResponse(status_code=404, content={"error": "Resource not found."})

    @app.exception_handler(405)
    async def method_not_allowed_handler(request: Request, exc):
        return JSONResponse(status_code=405, content={"error": "Method not allowed."})

    @app.exception_handler(500)
    async def internal_error_handler(request: Request, exc):
        import traceback
        err = traceback.format_exc()
        logger.exception("Internal server error")
        return JSONResponse(
            status_code=500,
            content={"error": "An internal server error occurred. Please try again later.", "traceback": str(exc)},
        )

    logger.info("[OK] Global error handlers registered.")
    return app


app = create_app()

# ─── Entry Point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    config = get_config()

    logger.info("Starting TechnoBuzz Feedback System (FastAPI)")
    logger.info("   Company    : %s (%s)", config.COMPANY_NAME, config.COMPANY_ID)
    logger.info("   Feedback   : %s/feedback", config.APP_BASE_URL)
    logger.info("   Phone QR   : %s", build_lan_url(config.PORT, "/feedback"))
    logger.info("   Admin      : %s/admin", config.APP_BASE_URL)
    logger.info(
        "   Razorpay   : %s",
        "configured" if (config.RAZORPAY_KEY_ID and config.RAZORPAY_KEY_SECRET) else "not configured (plans auto-collected on save)",
    )
    logger.info("   Host:Port  : %s:%s", config.HOST, config.PORT)

    uvicorn.run("app:app", host=config.HOST, port=config.PORT, reload=config.DEBUG)
