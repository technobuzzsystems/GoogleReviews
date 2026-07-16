"""
app.py
-------
TechnoBuzz AI-Powered QR Code Feedback System — Flask Application Entry Point.

Responsibilities:
    - Create and configure the Flask application instance.
    - Register all route blueprints.
    - Set up application-wide logging.
    - Run the development server.

Design note:
    This file contains ONLY application wiring.
    All business logic, database access, and AI calls
    live in their respective modules under /services.
"""

import logging
from flask import Flask, jsonify

from config import get_config
from routes.feedback_routes import feedback_bp
from routes.admin_routes import admin_bp
from utils.network import build_lan_url

# ─── Logging Setup ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)s]  %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def create_app() -> Flask:
    """
    Application factory function.
    Creates and returns a fully configured Flask application instance.
    The factory pattern makes testing and future extensions straightforward.
    """
    app = Flask(__name__)

    # ── Load config ───────────────────────────────────────────────────────────
    config = get_config()
    app.config["SECRET_KEY"]  = config.SECRET_KEY
    app.config["DEBUG"]       = config.DEBUG
    app.config["APP_CONFIG"]  = config  # Available to services if needed

    # ── Register blueprints ───────────────────────────────────────────────────
    app.register_blueprint(feedback_bp)  # /  /feedback  /generate-feedback  /submit-feedback
    app.register_blueprint(admin_bp)     # /admin

    logger.info("[OK] Blueprints registered: feedback_bp, admin_bp")

    # ── Global error handlers ─────────────────────────────────────────────────
    @app.errorhandler(404)
    def not_found(error):
        return jsonify({"error": "Resource not found."}), 404

    @app.errorhandler(405)
    def method_not_allowed(error):
        return jsonify({"error": "Method not allowed."}), 405

    @app.errorhandler(500)
    def internal_error(error):
        logger.error("Internal server error: %s", error)
        return jsonify({"error": "An internal server error occurred. Please try again later."}), 500

    logger.info("[OK] Global error handlers registered.")
    return app


# ─── Entry Point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    config = get_config()
    app    = create_app()

    logger.info("Starting TechnoBuzz Feedback System")
    logger.info("   Company    : %s (%s)", config.COMPANY_NAME, config.COMPANY_ID)
    logger.info("   Feedback   : %s/feedback", config.APP_BASE_URL)
    logger.info("   Phone QR   : %s",          build_lan_url(config.PORT, "/feedback"))
    logger.info("   Admin      : %s/admin",    config.APP_BASE_URL)
    logger.info("   Host:Port  : %s:%s",       config.HOST, config.PORT)

    app.run(host=config.HOST, port=config.PORT, debug=config.DEBUG)
