"""
routes/feedback_routes.py
--------------------------
All customer-facing feedback routes.

Endpoints:
    GET  /                    → Redirect to /feedback
    GET  /feedback            → Render the feedback HTML page
    POST /generate-feedback   → Call Gemini AI, return 8-10 suggestions
    POST /submit-feedback     → Validate and store feedback in MongoDB
"""

import logging
from flask import Blueprint, render_template, jsonify, request, redirect, url_for

from config import get_config
from utils.validators import validate_rating, validate_feedback_submission, sanitize_string
from services.gemini_service import generate_feedback_suggestions
from services.database_service import insert_feedback

# ─── Blueprint ────────────────────────────────────────────────────────────────
feedback_bp = Blueprint("feedback", __name__)
config      = get_config()
logger      = logging.getLogger(__name__)


# ─── GET / ────────────────────────────────────────────────────────────────────
@feedback_bp.route("/")
def index():
    """Redirect root URL to the main feedback page."""
    return redirect(url_for("feedback.feedback_page"))


# ─── GET /feedback ────────────────────────────────────────────────────────────
@feedback_bp.route("/feedback")
def feedback_page():
    """
    Serve the main customer feedback page.
    Company data injected into the template context.
    """
    context = {
        "company_name":      config.COMPANY_NAME,
        "company_id":        config.COMPANY_ID,
        "google_review_url": config.GOOGLE_REVIEW_URL,
    }
    return render_template("feedback.html", **context)


# ─── POST /generate-feedback ──────────────────────────────────────────────────
@feedback_bp.route("/generate-feedback", methods=["POST"])
def generate_feedback():
    """
    Accept a star rating, call Google Gemini AI,
    and return 8–10 human-like feedback suggestions.

    Request  JSON: { "rating": <int 1-5> }
    Response JSON: { "suggestions": ["...", "...", ...] }
    """
    data = request.get_json(silent=True)

    if not data:
        logger.warning("generate_feedback: empty or non-JSON request body")
        return jsonify({"error": "Request body must be valid JSON."}), 400

    rating_raw = data.get("rating")
    is_valid, error_message = validate_rating(rating_raw)
    if not is_valid:
        logger.warning("generate_feedback: invalid rating=%r — %s", rating_raw, error_message)
        return jsonify({"error": error_message}), 400

    rating = int(rating_raw)

    try:
        suggestions = generate_feedback_suggestions(rating)

        if not suggestions:
            return jsonify({"error": "AI returned no suggestions. Please try again."}), 500

        logger.info("generate_feedback: %d suggestions for rating=%d", len(suggestions), rating)
        return jsonify({"suggestions": suggestions}), 200

    except ValueError as e:
        logger.warning("generate_feedback: ValueError — %s", str(e))
        return jsonify({"error": str(e)}), 400

    except RuntimeError as e:
        error_str = str(e)
        logger.error("generate_feedback: RuntimeError — %s", error_str)
        if "GEMINI_API_KEY" in error_str:
            return jsonify({"error": "AI service is not configured. Please contact support."}), 503
        return jsonify({"error": "AI service is temporarily unavailable. Please try again."}), 500

    except Exception as e:
        logger.error("generate_feedback: unexpected error — %s", str(e), exc_info=True)
        return jsonify({"error": "An unexpected error occurred. Please try again."}), 500


# ─── POST /submit-feedback ────────────────────────────────────────────────────
@feedback_bp.route("/submit-feedback", methods=["POST"])
def submit_feedback():
    """
    Phase 3: Validate the selected feedback and persist it to MongoDB.

    Validation rules:
        - company     : non-empty string
        - company_id  : non-empty string
        - rating      : integer 1–5
        - feedback    : non-empty string, max 1000 chars

    Duplicate-click protection:
        The frontend disables the submit button immediately on click,
        but the server also validates all fields before writing to DB.

    Request JSON:
        {
            "company":    "TechnoBuzz",
            "company_id": "TECHNOBUZZ-001",
            "rating":     5,
            "feedback":   "Excellent service and a wonderful experience."
        }

    Success Response (201):
        {
            "success": true,
            "message": "Thank you! Your feedback has been recorded.",
            "id":      "<mongodb_document_id>"
        }

    Error Responses:
        400 → Validation failure (missing/invalid fields)
        500 → Database write failure
        503 → MongoDB not reachable
    """
    # ── Parse request body ─────────────────────────────────────────────────────
    data = request.get_json(silent=True)

    if not data:
        logger.warning("submit_feedback: empty or non-JSON request body")
        return jsonify({"error": "Request body must be valid JSON."}), 400

    # ── Server-side validation ─────────────────────────────────────────────────
    is_valid, error_message = validate_feedback_submission(data)
    if not is_valid:
        logger.warning("submit_feedback: validation failed — %s", error_message)
        return jsonify({"error": error_message}), 400

    # ── Sanitize inputs ────────────────────────────────────────────────────────
    company       = sanitize_string(data["company"],    max_length=200)
    company_id    = sanitize_string(data["company_id"], max_length=100)
    rating        = int(data["rating"])
    feedback_text = sanitize_string(data["feedback"],   max_length=1000)

    # ── Insert into MongoDB ────────────────────────────────────────────────────
    try:
        inserted_id = insert_feedback(
            company=      company,
            company_id=   company_id,
            rating=       rating,
            feedback_text=feedback_text,
        )

        if not inserted_id:
            logger.error("submit_feedback: insert returned None for company_id=%s", company_id)
            return jsonify({"error": "Failed to save feedback. Please try again."}), 500

        logger.info(
            "submit_feedback: SUCCESS — id=%s company=%s rating=%d",
            inserted_id, company_id, rating
        )
        return jsonify({
            "success": True,
            "message": "Thank you! Your feedback has been recorded.",
            "id":      inserted_id,
        }), 201

    except RuntimeError as e:
        error_str = str(e)
        logger.error("submit_feedback: RuntimeError — %s", error_str)

        # Distinguish DB connection error from general DB error
        if "connect" in error_str.lower() or "mongodb" in error_str.lower():
            return jsonify({
                "error": "Database is currently unavailable. Please try again later."
            }), 503

        return jsonify({
            "error": "Failed to save feedback due to a server error. Please try again."
        }), 500

    except Exception as e:
        logger.error("submit_feedback: unexpected error — %s", str(e), exc_info=True)
        return jsonify({
            "error": "An unexpected error occurred. Please try again."
        }), 500
