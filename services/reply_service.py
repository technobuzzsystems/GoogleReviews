"""
services/reply_service.py
--------------------------
Google Business Profile AI Review Auto-Replier service powered by Google Gemini.

Key Responsibilities:
    - Analyze customer reviews (language, sentiment, star rating, reviewer name, key praise/issues).
    - Support Marathi (मराठी), Hindi (हिन्दी), English, Gujarati, and other Indian languages automatically.
    - Generate polite, contextual, professional business owner responses with appropriate tone.
    - SEO Intelligence: Naturally context-injects business-specific services, category, and location keywords.
    - Handle 4-5 star praise and 1-3 star critical feedback empathetically.
    - Provide local fallback replies when Gemini API is not reachable.
    - Log sent replies to the database for tracking and duplicate prevention.
"""

import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types
from dotenv import load_dotenv
from sqlalchemy.orm import Session

from config import get_config
from models.domain_models import BusinessConfigModel, ReviewReplyLog

logger = logging.getLogger(__name__)

TONE_DESCRIPTIONS = {
    "professional_warm": "Warm, professional, appreciative and courteous.",
    "casual_friendly": "Friendly, enthusiastic, approachable, and cheerful.",
    "formal": "Polite, elegant, dignified, and executive-level corporate.",
    "short": "Short, crisp, direct, and under 2 sentences.",
}

LANGUAGE_NAMES = {
    "auto": "Auto-detect (Match the language used by the customer in their review)",
    "mr": "Marathi (मराठी)",
    "hi": "Hindi (हिन्दी)",
    "en": "English",
    "gu": "Gujarati (ગુજરાતી)",
    "ta": "Tamil (தமிழ்)",
    "te": "Telugu (తెలుగు)",
    "kn": "Kannada (ಕನ್ನಡ)",
    "bn": "Bengali (বাংলা)",
    "pa": "Punjabi (ਪੰਜਾਬੀ)",
}


def _gemini_api_key() -> str:
    load_dotenv(override=True)
    return (os.getenv("GEMINI_API_KEY") or "").strip()


def _get_client() -> genai.Client:
    api_key = _gemini_api_key()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured in .env file.")
    return genai.Client(api_key=api_key)


def _detect_is_devanagari(text: str) -> bool:
    """Check if text contains Devanagari script (Marathi / Hindi)."""
    return bool(re.search(r"[\u0900-\u097F]", text or ""))


def _detect_marathi_words(text: str) -> bool:
    """Heuristic check for common Marathi marker words."""
    marathi_markers = [
        "आहे", "आहेत", "खूप", "आमच्या", "आमची", "काम", "छान", "उत्तम",
        "धन्यवाद", "कौतुकास्पद", "झाले", "केले", "होते", "नक्कीच", "सर्व्हिस",
        "अप्रतिम", "सोपा", "सुंदर", "फार", "एक नंबर", "कमी", "जास्त", "पुन्हा"
    ]
    low = (text or "").lower()
    return any(w in low for w in marathi_markers)


def sanitize_reviewer_name(name: Optional[str]) -> str:
    """
    Sanitize and validate customer reviewer name.
    Strictly filters out numbers, counter strings ('2', '3', '4', '11 reviews'),
    timestamps ('9 mins ago', '2 weeks ago', 'just now'),
    Google UI words ('Press', 'Local Guide', 'Owner', 'Reply', etc.),
    ensuring we NEVER output awkward greetings like 'Dear 2' or 'Dear 9 Mins Ago'.
    """
    if not name:
        return ""

    clean = re.sub(r"[\r\n\t]+", " ", str(name)).strip()

    # Strip any trailing metadata like " · 1 review · 0 photos" or " (Owner)" or " - Local Guide"
    clean = re.split(r"[·•|]", clean)[0].strip()
    clean = re.sub(r"[\s\-]+(?:\d+\s*(?:reviews?|photos?|stars?)|local guide|owner).*", "", clean, flags=re.I).strip()

    # Reject if empty or too short or too long
    if not clean or len(clean) < 2 or len(clean) > 40:
        return ""

    # Reject if it contains digits (real customer names on Google Business do not need digits in greetings)
    if re.search(r"\d", clean):
        return ""

    lower = clean.lower()

    # Reject if it contains ANY forbidden keyword/substring
    forbidden_tokens = [
        "star", "stars", "rating", "rated",
        "ago", "min", "mins", "minute", "minutes", "hour", "hours",
        "day", "days", "week", "weeks", "month", "months", "year", "years",
        "just now", "new", "edited", "yesterday", "today",
        "review", "reviews", "photo", "photos", "guide", "local guide",
        "press", "enter", "owner", "reply", "replying", "publicly",
        "google", "search", "customer", "valued customer", "client",
    ]
    for token in forbidden_tokens:
        if re.search(r"\b" + re.escape(token) + r"\b", lower):
            return ""

    return clean


def _build_seo_reply_prompt(
    business_name: str,
    reviewer_name: str,
    rating: int,
    review_text: str,
    business_context: Dict[str, Any],
    language: str = "auto",
    tone: str = "professional_warm",
    auto_sign: bool = True,
) -> str:
    """Construct an SEO-optimized prompt for Gemini to reply to a Google Review."""
    scope = (business_context.get("scope") or "").strip()
    category = (business_context.get("business_category") or "Local Business").strip()
    area = (business_context.get("area") or "").strip()
    seo_keywords = (business_context.get("seo_keywords") or "").strip()
    mobile = (business_context.get("mobile") or "").strip()
    clean_name = sanitize_reviewer_name(reviewer_name)

    signature = (business_context.get("reply_signature") or "").strip()
    if not signature:
        signature = f"— Team {business_name}"

    prompt = f"""You are the official customer relations manager for "{business_name}".
Your task is to write an authentic, professional, and SEO-friendly response to a customer's Google Review on Google Maps.

Business Profile:
- Business Name: "{business_name}"
- Industry / Category: {category}
- Location / City / Area: {area if area else 'Our local branch'}
- Core Services & Offerings: {scope if scope else 'Customer products and services'}
- SEO Target Keywords: {seo_keywords if seo_keywords else 'Quality service, customer satisfaction'}

Customer Review Details:
- Reviewer Name: {clean_name if clean_name else 'Valued Customer'}
- Star Rating: {rating} out of 5 stars
- Customer Comment: "{review_text if review_text else '(Customer left star rating without text)'}"

Target Response Style:
- Tone: {TONE_DESCRIPTIONS.get(tone, TONE_DESCRIPTIONS['professional_warm'])}
- Preferred Language Mode: {language}

Strict Guidelines:
1. Language:
   - If language mode is 'auto', reply in the EXACT language of the customer's comment (Marathi, Hindi, or English).
   - If the comment is in Marathi (मराठी), reply in natural, grammatically correct Marathi.
   - If the comment is in Hindi (हिन्दी), reply in natural, grammatically correct Hindi.
   - If the comment is in English or empty, reply in clear, professional English.

2. SEO & Keyword Integration:
   - Naturally mention 1-2 relevant services, specialties, or area from the business profile context where it fits naturally into the conversation.
   - DO NOT keyword stuff. The reply must read 100% human, sincere, and natural.
   - NEVER invent services, products, discounts, or facilities not present in the business profile.

3. Sentiment & Star Rating Handling:
   - For 4-Star or 5-Star Reviews: Express genuine gratitude. Thank the customer warmly for choosing "{business_name}", acknowledge any specific praise, and welcome them back.
   - For 1-Star, 2-Star, or 3-Star Reviews: Be polite, humble, empathetic, and constructive. Apologize sincerely for not meeting their expectations. Reassure them that their feedback is taken seriously and invite them to connect at {mobile if mobile else 'our customer support'} to resolve their concern. Never be defensive or argumentative.

4. Greeting:
   - If reviewer name is valid ({clean_name}), greet them naturally:
     - English: "Dear {clean_name}," or "Hi {clean_name},"
     - Marathi: "नमस्कार {clean_name} जी," or "सस्नेह नमस्कार {clean_name},"
     - Hindi: "नमस्ते {clean_name} जी,"
   - If reviewer name is empty, start politely without an awkward name placeholder.

5. Length & Sign-off:
   - Keep the reply concise: exactly 2 to 3 sentences.
   - End with the signature: "{signature}" (if auto_sign is enabled).

Output Format:
Return ONLY valid JSON (no markdown formatting, no code blocks):
{{
  "reply": "The complete reply text here",
  "language_code": "en/mr/hi",
  "detected_language": "English/Marathi/Hindi",
  "sentiment": "positive/neutral/negative"
}}"""
    return prompt


def _local_fallback_reply(
    review_text: str,
    reviewer_name: str,
    rating: int,
    business_name: str,
    language: str = "auto",
    signature: str = "",
) -> Dict[str, Any]:
    """Generate high-quality local fallback reply if Gemini is unreachable."""
    clean_name = sanitize_reviewer_name(reviewer_name)
    sign = signature or f"— Team {business_name}"

    is_mr = language == "mr" or (language == "auto" and _detect_marathi_words(review_text))
    is_hi = language == "hi" or (language == "auto" and not is_mr and _detect_is_devanagari(review_text))

    if is_mr:
        if rating >= 4:
            greeting = f"सस्नेह नमस्कार {clean_name}," if clean_name else "सस्नेह नमस्कार,"
            body = f"{greeting} {business_name} ला उत्तम रेटिंग आणि प्रतिक्रिया दिल्याबद्दल आपले मनःपूर्वक धन्यवाद! आमची सेवा आपणास आवडली याचा आम्हाला मनापासून आनंद आहे. पुन्हा नक्की भेट द्या! {sign}"
        else:
            greeting = f"नमस्कार {clean_name}," if clean_name else "नमस्कार,"
            body = f"{greeting} आपल्या अनुभवाबद्दल आम्हाला मनापासून खेद वाटतो. आपल्या समाधानासाठी सेवेत सुधारणा करणे हे आमचे पहिले कर्तव्य आहे. कृपया अधिक माहितीसाठी आमच्याशी थेट संपर्क साधावा. {sign}"
        return {"reply": body, "language": "mr", "detected_language": "Marathi", "sentiment": "positive" if rating >= 4 else "negative", "source": "local_fallback"}

    if is_hi:
        if rating >= 4:
            greeting = f"नमस्ते {clean_name} जी," if clean_name else "नमस्ते,"
            body = f"{greeting} {business_name} को शानदार रेटिंग और समीक्षा देने के लिए आपका बहुत-बहुत धन्यवाद! हमें खुशी है कि आपको हमारी सेवा पसंद आई। दोबारा आपका स्वागत करने के लिए हम उत्सुक हैं। {sign}"
        else:
            greeting = f"नमस्ते {clean_name} जी," if clean_name else "नमस्ते,"
            body = f"{greeting} आपके असंतोषजनक अनुभव के लिए हमें खेद है। हम अपनी सेवा को और बेहतर बनाने के लिए प्रतिबद्ध हैं। कृपया अपनी समस्या के समाधान के लिए हमसे संपर्क करें। {sign}"
        return {"reply": body, "language": "hi", "detected_language": "Hindi", "sentiment": "positive" if rating >= 4 else "negative", "source": "local_fallback"}

    # Default: English
    greeting = f"Hi {clean_name}," if clean_name else "Thank you for taking the time to leave us a review!"
    if rating >= 4:
        body = f"{greeting} Thank you so much for your wonderful review and support for {business_name}! We are thrilled that you had a great experience and look forward to welcoming you again soon. {sign}"
    else:
        body = f"{greeting} We sincerely apologize that your experience did not meet expectations. We take customer satisfaction very seriously and would love the opportunity to make this right. Please feel free to reach out to our team directly. {sign}"

    return {"reply": body, "language": "en", "detected_language": "English", "sentiment": "positive" if rating >= 4 else "negative", "source": "local_fallback"}


def generate_review_reply(
    business_name: str,
    reviewer_name: str = "",
    rating: int = 5,
    review_text: str = "",
    business_context: Optional[Dict[str, Any]] = None,
    language: str = "auto",
    tone: str = "professional_warm",
    auto_sign: bool = True,
) -> Dict[str, Any]:
    """
    Generate an AI-powered, SEO-friendly Google Review reply using Gemini.
    Gracefully falls back to local multilingual templates on failure.
    """
    b_ctx = business_context or {}
    company_name = business_name or b_ctx.get("name") or "Our Business"
    clean_name = sanitize_reviewer_name(reviewer_name)
    signature = (b_ctx.get("reply_signature") or "").strip() or f"— Team {company_name}"

    try:
        client = _get_client()
    except Exception as e:
        logger.warning("Could not initialize Gemini client: %s. Using local fallback.", e)
        return _local_fallback_reply(review_text, clean_name, rating, company_name, language, signature)

    prompt = _build_seo_reply_prompt(
        business_name=company_name,
        reviewer_name=clean_name,
        rating=rating,
        review_text=review_text,
        business_context=b_ctx,
        language=language,
        tone=tone,
        auto_sign=auto_sign,
    )

    candidate_models = ["gemini-3.8-flash", "gemini-2.5-flash"]
    last_error = None

    for model_name in candidate_models:
        try:
            logger.info("Generating review reply for '%s' with model %s", company_name, model_name)
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.7,
                    max_output_tokens=1024,
                    response_mime_type="application/json",
                ),
            )
            raw_text = response.text or ""
            cleaned = re.sub(r"```(?:json)?\s*", "", raw_text).strip().rstrip("`")
            data = json.loads(cleaned)

            reply_str = str(data.get("reply") or "").strip()
            if not reply_str:
                raise ValueError("Empty reply in Gemini output")

            return {
                "reply": reply_str,
                "language": data.get("language_code", "auto"),
                "detected_language": data.get("detected_language", "Auto"),
                "sentiment": data.get("sentiment", "positive"),
                "rating": rating,
                "tone": tone,
                "source": f"gemini_{model_name}",
                "success": True,
            }
        except Exception as e:
            logger.warning("Gemini model %s failed: %s", model_name, e)
            last_error = e
            continue

    logger.warning("All Gemini models failed (%s). Falling back to local template.", last_error)
    return _local_fallback_reply(review_text, clean_name, rating, company_name, language, signature)


def log_review_reply(
    db: Session,
    business_key: str,
    reviewer_name: str,
    rating: int,
    review_text: str,
    reply_text: str,
    language: str = "auto",
    tone: str = "professional_warm",
    status: str = "sent",
    source: str = "cloud_api",
) -> ReviewReplyLog:
    """Save an audit record of the generated or sent review reply to the database."""
    clean_user_name = sanitize_reviewer_name(reviewer_name) or "Valued Customer"
    log_entry = ReviewReplyLog(
        business_key=business_key,
        reviewer_name=clean_user_name,
        rating=rating,
        review_text=review_text or "",
        reply_text=reply_text or "",
        language=language or "auto",
        tone=tone or "professional_warm",
        status=status or "sent",
        source=source or "cloud_api",
        created_at=datetime.utcnow(),
    )
    db.add(log_entry)
    db.commit()
    db.refresh(log_entry)
    return log_entry


def get_review_reply_logs(
    db: Session,
    business_key: Optional[str] = None,
    limit: int = 50,
) -> List[ReviewReplyLog]:
    """Retrieve recent review reply logs from the database."""
    query = db.query(ReviewReplyLog)
    if business_key and business_key != "all":
        query = query.filter(ReviewReplyLog.business_key == business_key)
    return query.order_by(ReviewReplyLog.created_at.desc()).limit(limit).all()


def clear_review_reply_logs(db: Session, business_key: Optional[str] = None) -> int:
    """Clear review reply logs for a specific business or all businesses."""
    query = db.query(ReviewReplyLog)
    if business_key and business_key != "all":
        query = query.filter(ReviewReplyLog.business_key == business_key)
    count = query.delete(synchronize_session=False)
    db.commit()
    return count
