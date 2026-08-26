"""
services/database_service.py
------------------------------
MongoDB database service layer — Phase 3 Full Implementation.

Responsibilities:
    - Maintain a singleton MongoClient connection (lazy init on first call).
    - Insert validated feedback documents into the 'feedback' collection.
    - Retrieve paginated, filtered feedback for the admin dashboard (Phase 4).
    - Compute aggregate statistics for the admin dashboard (Phase 4).

Design:
    - All MongoDB logic is isolated here — routes never import pymongo directly.
    - Connection is created once and reused across requests (singleton pattern).
    - Indexes are created on first connection to support fast queries.
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from pymongo import MongoClient, DESCENDING
from pymongo.errors import ConnectionFailure, PyMongoError

from config import get_config
from models.feedback_model import FeedbackDocument

config = get_config()
logger = logging.getLogger(__name__)

# ─── Singleton connection holders ──────────────────────────────────────────────
_client: Optional[MongoClient] = None
_db = None


def get_database():
    """
    Return a singleton MongoDB database instance.
    Creates the connection and ensures indexes on first call.

    Returns:
        pymongo.database.Database: Active MongoDB database instance.

    Raises:
        RuntimeError: If connection to MongoDB cannot be established.
    """
    global _client, _db

    if _db is not None:
        return _db

    try:
        logger.info("Connecting to MongoDB at: %s", config.MONGO_URI)
        _client = MongoClient(
            config.MONGO_URI,
            serverSelectionTimeoutMS=5000,   # 5-second connection timeout
            connectTimeoutMS=5000,
        )

        # Force an actual connection check
        _client.admin.command("ping")
        _db = _client[config.MONGO_DB_NAME]

        # ── Create indexes on the feedback collection ────────────────────────
        _ensure_indexes(_db)

        logger.info("✅  MongoDB connected — database: '%s'", config.MONGO_DB_NAME)
        return _db

    except ConnectionFailure as e:
        logger.error("❌  MongoDB connection failed: %s", str(e))
        raise RuntimeError(
            "Could not connect to MongoDB. "
            "Make sure MongoDB is running on: " + config.MONGO_URI
        ) from e


def _ensure_indexes(db) -> None:
    """
    Create performance and query indexes on the feedback collection.
    Safe to call multiple times — MongoDB ignores duplicate index creation.
    """
    collection = db["feedback"]
    collection.create_index([("created_at", DESCENDING)], name="idx_created_at")
    collection.create_index([("company_id",  1)],          name="idx_company_id")
    collection.create_index([("rating",      1)],          name="idx_rating")
    logger.info("✅  MongoDB indexes ensured on 'feedback' collection.")


def insert_feedback(
    company: str,
    company_id: str,
    rating: int,
    feedback_text: str,
    collection_name: str = "feedback"
) -> Optional[str]:
    """
    Build a FeedbackDocument and insert it into the specified collection.

    Args:
        company         (str): Company display name.
        company_id      (str): Unique company / QR code identifier.
        rating          (int): Star rating selected by user (1–5).
        feedback_text   (str): The AI-generated feedback sentence chosen by user.
        collection_name (str): MongoDB collection name (default 'feedback').

    Returns:
        str: The MongoDB-assigned _id of the inserted document (as string).
        None: If insertion fails.

    Raises:
        RuntimeError: If the database connection cannot be established.
    """
    try:
        db         = get_database()
        collection = db[collection_name]

        # Build the document using our typed model
        doc = FeedbackDocument(
            company=    company,
            company_id= company_id,
            rating=     rating,
            feedback=   feedback_text,
            created_at= datetime.now(timezone.utc),
        )

        result = collection.insert_one(doc.to_dict())
        inserted_id = str(result.inserted_id)

        logger.info(
            "✅  Feedback inserted — id=%s company=%s rating=%d",
            inserted_id, company_id, rating
        )
        return inserted_id

    except RuntimeError:
        raise   # Re-raise connection errors for the route to handle

    except PyMongoError as e:
        logger.error("MongoDB insert_feedback failed: %s", str(e), exc_info=True)
        raise RuntimeError(f"Database error during insert: {str(e)}") from e


def get_all_feedback(
    page: int = 1,
    per_page: int = 20,
    rating_filter: Optional[int] = None,
    search_query: Optional[str] = None,
    collection_name: str = "feedback"
) -> Dict[str, Any]:
    """
    Retrieve paginated feedback records with optional filters.
    Used by the admin dashboard (Phase 4).

    Args:
        page            (int): 1-based page number.
        per_page        (int): Records per page.
        rating_filter   (int): If set, filter to only this star rating.
        search_query    (str): If set, text-search within feedback field.
        collection_name (str): MongoDB collection name (default 'feedback').

    Returns:
        dict: {
            "records": [list of feedback dicts],
            "total"  : total matching record count,
            "page"   : current page,
            "pages"  : total page count
        }
    """
    try:
        db         = get_database()
        collection = db[collection_name]

        # Build query filter
        query: Dict = {}
        if rating_filter is not None:
            query["rating"] = rating_filter
        if search_query:
            query["feedback"] = {"$regex": search_query, "$options": "i"}

        total   = collection.count_documents(query)
        skip    = (page - 1) * per_page
        pages   = max(1, (total + per_page - 1) // per_page)
        cursor  = collection.find(query).sort("created_at", DESCENDING).skip(skip).limit(per_page)

        records = []
        for doc in cursor:
            doc["_id"] = str(doc["_id"])      # Convert ObjectId → string
            # Serialize datetime for JSON
            if isinstance(doc.get("created_at"), datetime):
                doc["created_at"] = doc["created_at"].isoformat()
            records.append(doc)

        return {
            "records": records,
            "total":   total,
            "page":    page,
            "pages":   pages,
        }

    except RuntimeError:
        raise

    except PyMongoError as e:
        logger.error("get_all_feedback failed: %s", str(e), exc_info=True)
        return {"records": [], "total": 0, "page": page, "pages": 0}


def get_feedback_stats(collection_name: str = "feedback") -> Dict[str, Any]:
    """
    Aggregate statistics for the admin dashboard (Phase 4).

    Returns:
        dict: {
            "total"         : int,
            "average_rating": float,
            "by_rating"     : { "1": int, "2": int, ... "5": int }
        }
    """
    try:
        db         = get_database()
        collection = db[collection_name]

        total = collection.count_documents({})
        if total == 0:
            return {
                "total": 0,
                "average_rating": 0.0,
                "by_rating": {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0},
            }

        # Aggregation pipeline for avg + per-rating counts
        pipeline = [
            {
                "$group": {
                    "_id":    None,
                    "avg":    {"$avg": "$rating"},
                    "count1": {"$sum": {"$cond": [{"$eq": ["$rating", 1]}, 1, 0]}},
                    "count2": {"$sum": {"$cond": [{"$eq": ["$rating", 2]}, 1, 0]}},
                    "count3": {"$sum": {"$cond": [{"$eq": ["$rating", 3]}, 1, 0]}},
                    "count4": {"$sum": {"$cond": [{"$eq": ["$rating", 4]}, 1, 0]}},
                    "count5": {"$sum": {"$cond": [{"$eq": ["$rating", 5]}, 1, 0]}},
                }
            }
        ]
        result = list(collection.aggregate(pipeline))

        if not result:
            return {
                "total": total,
                "average_rating": 0.0,
                "by_rating": {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0},
            }

        row = result[0]
        return {
            "total":          total,
            "average_rating": round(row.get("avg", 0.0), 1),
            "by_rating": {
                "1": row.get("count1", 0),
                "2": row.get("count2", 0),
                "3": row.get("count3", 0),
                "4": row.get("count4", 0),
                "5": row.get("count5", 0),
            },
        }

    except RuntimeError:
        raise

    except PyMongoError as e:
        logger.error("get_feedback_stats failed: %s", str(e), exc_info=True)
        return {
            "total": 0,
            "average_rating": 0.0,
            "by_rating": {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0},
        }
