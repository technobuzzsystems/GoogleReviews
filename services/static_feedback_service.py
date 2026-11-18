"""
services/static_feedback_service.py
------------------------------------
Predefined static feedback pool management and usage tracking.

Features:
- Stores 50 predefined responses per star rating (250 per business).
- Fetches available unused responses instantly (< 5ms) without calling Gemini.
- Marks a response as USED only after the customer actually submits feedback.
- Prevents previously submitted responses from appearing again for the same business & rating.
- Graceful 3-level fallback:
    Level 1: Return fresh unused responses.
    Level 2: Low pool (<10 unused) -> Return all unused + oldest used to maintain 10 suggestions.
    Level 3: Pool exhausted (all 50 used) -> Recycle random responses from pool.
- Option 2 replenishment: Background generation of additional responses via Gemini with local safety seeds.
"""

import json
import logging
import random
import re
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from config import get_config
from models.domain_models import BusinessConfigModel, StaticFeedbackResponse, StaticFeedbackUsage
from services.gemini_service import _get_client, _gemini_api_key, _local_suggestions, validate_and_sanitize_suggestions

logger = logging.getLogger(__name__)


def get_available_feedback_suggestions(
    db: Session,
    business_key: str,
    rating: int,
    language: str = "en",
    limit: int = 10,
) -> List[Dict]:
    """
    Fetch up to `limit` suggestions for (business_key, rating).
    Prioritizes UNUSED responses first so customers never see repeats.
    Returns list of dicts: [{"id": int, "text": str}, ...].
    Returns empty list if no static responses exist for this business.
    """
    business_key = (business_key or "").strip()
    language = (language or "en").strip().lower()

    # 1. Check if business has any active static responses
    base_query = db.query(StaticFeedbackResponse).filter(
        StaticFeedbackResponse.business_key == business_key,
        StaticFeedbackResponse.rating == rating,
        StaticFeedbackResponse.is_active == True,
    )

    # Try requested language first; if none, fall back to any language (usually 'en')
    lang_count = base_query.filter(StaticFeedbackResponse.language == language).count()
    active_lang = language if lang_count > 0 else None
    if active_lang:
        base_query = base_query.filter(StaticFeedbackResponse.language == active_lang)

    total_pool = base_query.count()
    if total_pool == 0:
        return []

    # 2. Query unused responses (no row in static_feedback_usage)
    used_subquery = (
        db.query(StaticFeedbackUsage.response_id)
        .filter(
            StaticFeedbackUsage.business_key == business_key,
            StaticFeedbackUsage.rating == rating,
        )
        .subquery()
    )

    unused_rows = (
        base_query.filter(~StaticFeedbackResponse.id.in_(used_subquery))
        .order_by(func.random())
        .limit(limit)
        .all()
    )

    results: List[Dict] = [{"id": r.id, "text": r.response_text} for r in unused_rows]

    # Level 1: We have enough unused responses (>= limit)
    if len(results) >= limit:
        return results[:limit]

    # Level 2 & 3: Unused pool has fewer than `limit` responses.
    # Fill remaining slots from the existing pool (oldest used / recycled)
    needed = limit - len(results)
    existing_ids = {r["id"] for r in results}

    recycled_rows = (
        base_query.filter(~StaticFeedbackResponse.id.in_(existing_ids))
        .order_by(func.random())
        .limit(needed)
        .all()
    )

    for r in recycled_rows:
        results.append({"id": r.id, "text": r.response_text})

    return results[:limit]


def record_response_usage(
    db: Session,
    response_id: Optional[int],
    business_key: str,
    rating: int,
    private_feedback_id: Optional[int] = None,
) -> bool:
    """
    Record that a customer submitted feedback using response_id.
    Ensures this response will not appear again for future customers.
    """
    if not response_id:
        return False

    try:
        usage = StaticFeedbackUsage(
            response_id=int(response_id),
            business_key=(business_key or "").strip(),
            rating=int(rating),
            private_feedback_id=private_feedback_id,
            used_at=datetime.utcnow(),
        )
        db.add(usage)
        db.commit()
        logger.info(
            "Recorded usage of response_id=%d for business=%s rating=%d",
            response_id,
            business_key,
            rating,
        )
        return True
    except Exception as e:
        logger.warning("Could not record response usage for id=%s: %s", response_id, e)
        db.rollback()
        return False


def get_static_pool_stats(db: Session, business_key: str) -> Dict:
    """
    Return statistics of the response pool for each star rating (1 to 5).
    """
    business_key = (business_key or "").strip()
    stats = {}
    total_all = 0
    used_all = 0

    for star in range(1, 6):
        total = (
            db.query(func.count(StaticFeedbackResponse.id))
            .filter(
                StaticFeedbackResponse.business_key == business_key,
                StaticFeedbackResponse.rating == star,
                StaticFeedbackResponse.is_active == True,
            )
            .scalar()
            or 0
        )

        used = (
            db.query(func.count(func.distinct(StaticFeedbackUsage.response_id)))
            .filter(
                StaticFeedbackUsage.business_key == business_key,
                StaticFeedbackUsage.rating == star,
            )
            .scalar()
            or 0
        )

        avail = max(0, total - used)
        stats[str(star)] = {
            "total": total,
            "used": used,
            "available": avail,
            "status": "exhausted" if total > 0 and avail == 0 else ("low" if 0 < avail < 10 else "healthy"),
        }
        total_all += total
        used_all += used

    stats["total"] = total_all
    stats["used"] = used_all
    stats["available"] = max(0, total_all - used_all)
    return stats


def reset_response_usage(db: Session, business_key: str, rating: Optional[int] = None) -> int:
    """
    Reset usage history so all responses become available again.
    """
    business_key = (business_key or "").strip()
    query = db.query(StaticFeedbackUsage).filter(StaticFeedbackUsage.business_key == business_key)
    if rating in (1, 2, 3, 4, 5):
        query = query.filter(StaticFeedbackUsage.rating == rating)
    count = query.delete(synchronize_session=False)
    db.commit()
    logger.info("Reset %d usage records for business=%s rating=%s", count, business_key, rating)
    return count


def save_static_responses(
    db: Session,
    business_key: str,
    rating: int,
    responses: List[str],
    language: str = "en",
) -> int:
    """Insert clean static response strings into the database."""
    business_key = (business_key or "").strip()
    language = (language or "en").strip().lower()

    # Enforce polarity safety guard: ensure responses strictly match rating tone
    sanitized = validate_and_sanitize_suggestions(responses, rating, {"name": business_key}, language)
    clean_responses = [str(r).strip() for r in sanitized if str(r).strip()]
    added = 0
    for resp in clean_responses:
        # Avoid exact duplicate insertion in same pool
        exists = (
            db.query(StaticFeedbackResponse.id)
            .filter(
                StaticFeedbackResponse.business_key == business_key,
                StaticFeedbackResponse.rating == rating,
                StaticFeedbackResponse.response_text == resp,
            )
            .first()
        )
        if not exists:
            row = StaticFeedbackResponse(
                business_key=business_key,
                rating=rating,
                language=language,
                response_text=resp,
                is_active=True,
                created_at=datetime.utcnow(),
            )
            db.add(row)
            added += 1

    db.commit()
    return added


def _generate_star_responses_gemini(
    business_context: dict,
    rating: int,
    count: int = 50,
    language: str = "en",
) -> List[str]:
    """
    Generate `count` unique realistic customer feedback reviews for a specific rating
    using Gemini API in batches, with automatic local fallback.
    """
    company_name = business_context.get("name") or "this business"
    scope = business_context.get("scope") or "Service and retail"

    tone_descriptions = {
        1: "Very negative, frustrated customer complaints focusing on critical service failures, delays, or poor quality.",
        2: "Disappointed, dissatisfied customer feedback mentioning specific unmet expectations or poor service.",
        3: "Average, mixed, neutral review mentioning both positive points and areas needing improvement.",
        4: "Positive and satisfied customer review highlighting good service with one minor note.",
        5: "Highly enthusiastic, delighted customer praise highlighting outstanding service, quality, and strong recommendation.",
    }

    examples_key = f"examples_{rating}star"
    themes = business_context.get(examples_key) or ""

    prompt = f"""
You are an expert review curator.
Generate exactly {count} distinct, natural customer reviews for:
Company: {company_name}
Business Scope: {scope}
Star Rating: {rating} out of 5
Tone: {tone_descriptions.get(rating, tone_descriptions[5])}
Relevant Keywords/Themes: {themes}

CRITICAL RULES:
1. Every review must sound like it was typed by a different real customer on Google Reviews.
2. Vary sentence length, vocabulary, and phrasing.
3. Length: 1 to 3 natural sentences per review.
4. No robotic or AI language.
5. Return ONLY a valid JSON array of strings containing {count} reviews.
6. No markdown fences, no notes, no numbering.

Example format:
["review 1", "review 2", ..., "review {count}"]
""".strip()

    collected: List[str] = []

    if _gemini_api_key():
        try:
            client = _get_client()
            cfg = get_config()
            models_to_try = [cfg.GEMINI_MODEL, "gemini-2.0-flash", "gemini-1.5-flash"]
            for model_name in models_to_try:
                try:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                    )
                    text = (response.text or "").strip()
                    cleaned = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
                    parsed = json.loads(cleaned)
                    if isinstance(parsed, list):
                        items = [str(x).strip() for x in parsed if str(x).strip()]
                        if len(items) >= 20:
                            collected = items
                            break
                except Exception as ex:
                    logger.warning("Gemini pool gen model %s failed: %s", model_name, ex)
                    continue
        except Exception as e:
            logger.warning("Gemini pool generation encountered error: %s", e)

    # If Gemini couldn't generate enough, synthesize high-quality variations using local templates
    if len(collected) < count:
        local_seed = _local_suggestions(rating, business_context, language)
        variations = []
        name = company_name

        modifiers_pos = [
            "Really appreciated the support from {name}.",
            "Good attention to detail by the {name} team.",
            "Definitely one of the best in this category.",
            "Smooth and reliable experience overall.",
            "Prompt service and courteous staff.",
            "Met all our expectations cleanly.",
            "Great value and pleasant interaction.",
            "Very pleased with how things were handled at {name}.",
        ]

        modifiers_neg = [
            "Needs serious improvement at {name}.",
            "Did not match what was promised.",
            "Customer service was unresponsive.",
            "Quite a frustrating delay throughout.",
            "Hope management takes feedback seriously.",
            "Disorganized process from start to finish.",
        ]

        mods = modifiers_pos if rating >= 4 else (modifiers_neg if rating <= 2 else modifiers_pos + modifiers_neg)

        for s in local_seed:
            variations.append(s)
            for m in mods:
                variations.append(f"{s} {m.format(name=name)}")

        random.shuffle(variations)
        for v in variations:
            if v not in collected:
                collected.append(v)
            if len(collected) >= count:
                break

    return collected[:count]


def generate_and_store_business_pool(
    db: Session,
    business_key: str,
    responses_per_star: int = 50,
    language: str = "en",
) -> Dict:
    """
    Generate and save the full 250 static response pool (50 per star rating)
    for a given business.
    """
    business_key = (business_key or "").strip()
    biz = db.query(BusinessConfigModel).filter(BusinessConfigModel.key == business_key).first()
    if not biz:
        raise ValueError(f"Business '{business_key}' does not exist.")

    biz_context = {
        "name": biz.name,
        "scope": biz.scope,
        "examples_1star": biz.examples_1star,
        "examples_2star": biz.examples_2star,
        "examples_3star": biz.examples_3star,
        "examples_4star": biz.examples_4star,
        "examples_5star": biz.examples_5star,
    }

    report = {"business_key": business_key, "generated_by_star": {}}
    total_added = 0

    for star in range(1, 6):
        responses = _generate_star_responses_gemini(
            business_context=biz_context,
            rating=star,
            count=responses_per_star,
            language=language,
        )
        added = save_static_responses(
            db=db,
            business_key=business_key,
            rating=star,
            responses=responses,
            language=language,
        )
        report["generated_by_star"][str(star)] = added
        total_added += added

    report["total_added"] = total_added
    logger.info("Successfully generated %d static responses for '%s'", total_added, business_key)
    return report
