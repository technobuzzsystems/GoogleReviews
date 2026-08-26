"""
routes/admin_routes.py
-----------------------
Admin dashboard routes — Phase 4 Full Implementation.

Endpoints:
    GET  /admin                 → Render the admin dashboard HTML page
    GET  /admin/api/stats       → Return aggregate stats as JSON
    GET  /admin/api/feedback    → Return paginated + filtered feedback as JSON
    GET  /admin/api/export      → Download all feedback as CSV
"""

import csv
import io
import logging
from flask import Blueprint, render_template, jsonify, request, Response

from config import get_config, BUSINESS_REGISTRY
from services.database_service import get_all_feedback, get_feedback_stats

# ─── Blueprint ─────────────────────────────────────────────────────────────────
admin_bp = Blueprint("admin", __name__, url_prefix="/admin")
config   = get_config()
logger   = logging.getLogger(__name__)


# ─── GET /admin ────────────────────────────────────────────────────────────────
@admin_bp.route("")
@admin_bp.route("/")
def admin_dashboard():
    """Render the admin dashboard page."""
    business_id = request.args.get("business", "technobuzz")
    b_config = BUSINESS_REGISTRY.get(business_id, BUSINESS_REGISTRY["technobuzz"])

    return render_template(
        "admin.html",
        company_name=b_config["name"],
        company_id=  b_config["id"],
        business_id= business_id,
        businesses=  BUSINESS_REGISTRY,
    )


# ─── GET /admin/api/stats ──────────────────────────────────────────────────────
@admin_bp.route("/api/stats")
def api_stats():
    """
    Return aggregate feedback statistics as JSON.

    Response: {
        "total":          int,
        "average_rating": float,
        "by_rating":      { "1": int, ..., "5": int }
    }
    """
    try:
        business_id = request.args.get("business", "technobuzz")
        b_config = BUSINESS_REGISTRY.get(business_id, BUSINESS_REGISTRY["technobuzz"])
        stats = get_feedback_stats(collection_name=b_config["collection"])
        return jsonify(stats), 200
    except RuntimeError as e:
        logger.error("api_stats error: %s", str(e))
        return jsonify({"error": "Database unavailable."}), 503
    except Exception as e:
        logger.error("api_stats unexpected error: %s", str(e), exc_info=True)
        return jsonify({"error": "Failed to load stats."}), 500


# ─── GET /admin/api/feedback ───────────────────────────────────────────────────
@admin_bp.route("/api/feedback")
def api_feedback():
    """
    Return paginated, filtered feedback records as JSON.

    Query params:
        page   (int):  Page number, default 1
        limit  (int):  Records per page, default 10
        rating (int):  Filter by star rating (1-5), optional
        search (str):  Text search in feedback field, optional
        business (str): Business ID to filter collections

    Response: {
        "records": [...],
        "total":   int,
        "page":    int,
        "pages":   int
    }
    """
    try:
        page     = max(1, int(request.args.get("page",  1)))
        limit    = min(50, max(1, int(request.args.get("limit", 10))))
        rating   = request.args.get("rating", None)
        search   = request.args.get("search", "").strip() or None
        business_id = request.args.get("business", "technobuzz")
        
        b_config = BUSINESS_REGISTRY.get(business_id, BUSINESS_REGISTRY["technobuzz"])

        rating_filter = None
        if rating and rating.isdigit():
            r = int(rating)
            if 1 <= r <= 5:
                rating_filter = r

        result = get_all_feedback(
            page=         page,
            per_page=     limit,
            rating_filter=rating_filter,
            search_query= search,
            collection_name=b_config["collection"],
        )
        return jsonify(result), 200

    except RuntimeError as e:
        logger.error("api_feedback error: %s", str(e))
        return jsonify({"error": "Database unavailable."}), 503
    except Exception as e:
        logger.error("api_feedback unexpected: %s", str(e), exc_info=True)
        return jsonify({"error": "Failed to load feedback."}), 500


# ─── GET /admin/api/export ────────────────────────────────────────────────────
@admin_bp.route("/api/export")
def api_export():
    """
    Export all feedback records as a downloadable CSV file.
    """
    try:
        business_id = request.args.get("business", "technobuzz")
        b_config = BUSINESS_REGISTRY.get(business_id, BUSINESS_REGISTRY["technobuzz"])
        
        result  = get_all_feedback(page=1, per_page=10000, collection_name=b_config["collection"])
        records = result.get("records", [])

        output = io.StringIO()
        writer = csv.writer(output)

        # Header row
        writer.writerow(["ID", "Company", "Company ID", "Rating", "Feedback", "Date", "Time"])

        for r in records:
            writer.writerow([
                r.get("_id",        ""),
                r.get("company",    ""),
                r.get("company_id", ""),
                r.get("rating",     ""),
                r.get("feedback",   ""),
                r.get("created_date", ""),
                r.get("created_time", ""),
            ])

        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=technobuzz_feedback.csv"}
        )

    except Exception as e:
        logger.error("api_export error: %s", str(e), exc_info=True)
        return jsonify({"error": "Export failed."}), 500
