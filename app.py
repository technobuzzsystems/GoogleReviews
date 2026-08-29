"""
app.py
-------
TechnoBuzz AI-Powered QR Code Feedback System — FastAPI Application Entry Point.
"""

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from config import get_config
from database import init_db
from routes.admin_routes import router as admin_router
from routes.feedback_routes import router as feedback_router
from services.auth_service import AuthRedirect
from utils.network import build_lan_url

# ─── Logging Setup ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)s]  %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("[OK] Database initialized")
    yield


def create_app() -> FastAPI:
    """
    Application factory function.
    Creates and returns a fully configured FastAPI application instance.
    """
    config = get_config()
    app = FastAPI(title="TechnoBuzz Feedback System", lifespan=lifespan)

    app.add_middleware(
        SessionMiddleware,
        secret_key=config.SECRET_KEY,
        session_cookie="gr_admin_session",
        same_site="lax",
        https_only=False,
        max_age=60 * 60 * 8,
    )

    app.mount("/static", StaticFiles(directory="static"), name="static")

    app.include_router(feedback_router)
    app.include_router(admin_router)

    logger.info("[OK] Routers registered: feedback_router, admin_router")

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
        logger.exception("Internal server error")
        return JSONResponse(
            status_code=500,
            content={"error": "An internal server error occurred. Please try again later."},
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
    logger.info("   Host:Port  : %s:%s", config.HOST, config.PORT)

    uvicorn.run("app:app", host=config.HOST, port=config.PORT, reload=config.DEBUG)
