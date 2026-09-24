"""
services/reply_service.py
--------------------------
Google Business Profile AI Review Auto-Replier service powered by Google Gemini.

Responsibilities:
    - Analyze customer reviews (language, sentiment, star rating, reviewer name, key praise/issues).
    - Support Marathi (मराठी), Hindi (हिन्दी), English, Gujarati, and other Indian languages automatically.
    - Generate polite, contextual, professional business owner responses with appropriate tone.
    - Handle 5/4-star praise and 1-3 star critical feedback empathetically.
    - Provide local fallback replies when Gemini API is not reachable.
    - Log sent replies to the database for tracking.
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
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=8000),
    )


def _detect_is_devanagari(text: str) -> bool:
    """Check if text contains Devanagari script (Marathi / Hindi)."""
    return bool(re.search(r"[\u0900-\u097F]", text or ""))


def _detect_marathi_words(text: str) -> bool:
    """Heuristic check for common Marathi marker words."""
    marathi_markers = [
        "आहे", "आहेत", "खूप", "आमच्या", "आमची", "काम", "छान", "उत्तम",
        "धन्यवाद", "कौतुकास्पद", "झाले", "केले", "होते", "नक्कीच", "सर्व्हिस",
        "अप्रतिम", "सोपा", "सुंदर", "फार", "एक नंबर", "कमी", "जास्त"
    ]
    low = (text or "").lower()
    return any(w in low for w in marathi_markers)


def sanitize_reviewer_name(name: Optional[str]) -> str:
    """
    Sanitize and validate customer reviewer name.
    Strictly filters out numbers, counter strings ('2', '3', '4', '11 reviews'),
    timestamps ('9 mins ago', '2 weeks ago', 'just now'),
    Google UI words ('Press', 'Local Guide', 'Owner', 'Reply', 'Starstarstarstarstar', etc.),
    ensuring we NEVER output awkward greetings like 'Dear 2' or 'Dear Starstarstarstarstar 9 Mins Ago'.
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
    
    # Reject if it contains ANY forbidden keyword/substring (e.g. 'star', 'ago', 'min', 'hour', 'review', etc.)
    forbidden_tokens = [
        "star", "stars", "rating", "rated",
        "ago", "min", "mins", "minute", "minutes", "hour", "hours",
        "day", "days", "week", "weeks", "month", "months", "year", "years",
        "just now", "new", "edited", "yesterday", "today",
        "review", "reviews", "photo", "photos", "guide", "local guide",
        "press", "enter", "owner", "reply", "replying", "publicly",
        "google", "search", "customer", "valued customer", "client",
        "user", "anonymous", "null", "undefined", "translate", "view full",
        "share", "like", "feedback", "post", "comment"
    ]
    
    for token in forbidden_tokens:
        if re.search(r"\b" + re.escape(token) + r"\b", lower) or token in lower:
            return ""
            
    # Must contain at least 2 consecutive alphabetic or Devanagari characters
    if not re.search(r"[a-zA-Z\u0900-\u097F]{2,}", clean):
        return ""
    
    # Clean up double spaces
    clean = re.sub(r"\s+", " ", clean).strip()
    
    # Proper casing if entirely lowercase or uppercase
    if clean.islower() or clean.isupper():
        clean = clean.title()
        
    return clean


def _local_fallback_reply(
    review_text: str,
    reviewer_name: str,
    rating: int,
    company_name: str,
    language: str = "auto",
    signature: str = "",
) -> Dict[str, Any]:
    """Provide a tailored local fallback reply with distinct templates for 1, 2, 3, 4, and 5 stars."""
    clean_name = sanitize_reviewer_name(reviewer_name)
    sign_off = signature or f"— Team {company_name}"

    # Sentiment Heuristic Analysis
    effective_rating = rating
    if review_text:
        neg_words = [
            "joke", "outage", "crippled", "terrible", "worst", "bad", "loss", "poor",
            "pathetic", "fraud", "scam", "disaster", "awful", "horrible", "waste",
            "disappointed", "disappointing", "cheat", "frustrated", "slow", "delay",
            "crash", "bug", "broken", "issue", "problem", "unacceptable",
            "घटिया", "खराब", "बकवास", "फालतू", "वाईट", "नुकसान", "त्रास", "कंटाळवाणा", "चूक"
        ]
        low = review_text.lower()
        if any(w in low for w in neg_words) and effective_rating > 2:
            effective_rating = 1

    is_marathi = False
    is_hindi = False
    if language == "mr" or (language == "auto" and (_detect_marathi_words(review_text) or _detect_is_devanagari(review_text))):
        is_marathi = True
    elif language == "hi":
        is_hindi = True

    if is_marathi:
        greeting = f"{clean_name} जी, " if clean_name else "नमस्कार, "
        if effective_rating >= 5:
            reply = f"{greeting}आमच्या सेवेबद्दल आपला मोलाचा ५-स्टार अभिप्राय दिल्याबद्दल मनःपूर्वक धन्यवाद! आपला अनुभव सुखद राहिला हे वाचून आम्हाला अत्यंत आनंद झाला. आम्ही नेहमीच उत्कृष्ट सेवा देण्यासाठी कटिबद्ध आहोत. पुन्हा नक्की भेट द्या! {sign_off}"
        elif effective_rating == 4:
            reply = f"{greeting}आमच्या सेवेला ४-स्टार रेटिंग आणि पसंती दिल्याबद्दल मनःपूर्वक धन्यवाद! पुढच्या वेळी आपल्याला परिपूर्ण ५-स्टार अनुभव देण्यासाठी आम्ही सदैव तत्पर राहू. पुन्हा नक्की भेट द्या! {sign_off}"
        elif effective_rating == 3:
            reply = f"{greeting}आपल्या प्रामाणिक अभिप्रायाबद्दल धन्यवाद. आमची सेवा समाधानकारक असली तरी, आपल्याला ५-स्टार अनुभव देण्यासाठी आम्ही यात अजून काय सुधारणा करू शकतो हे जाणून घ्यायला आम्हाला नक्की आवडेल. {sign_off}"
        elif effective_rating == 2:
            reply = f"{greeting}आपला अनुभव अपेक्षेप्रमाणे न राहिल्याबद्दल आम्ही क्षमस्व आहोत. आपल्या फीडबॅकची आम्ही गंभीर दखल घेतली असून सेवेत सुधारणा करत आहोत. कृपया आपल्या अडचणीचे निवारण करण्यासाठी आमच्याशी थेट संपर्क साधा. {sign_off}"
        else:
            reply = f"{greeting}आपल्याला आलेल्या वाईट अनुभवाबद्दल आणि गैरसोयीबद्दल आम्ही मनापासून क्षमस्व आहोत. अशी त्रुटी आमच्याकडून होणे अस्वीकार्य आहे. कृपया आपल्या समस्येचे तातडीने निवारण करण्यासाठी आमच्याशी थेट संपर्क साधा. {sign_off}"
        detected_lang = "Marathi"
        lang_code = "mr"
    elif is_hindi:
        greeting = f"नमस्ते {clean_name} जी, " if clean_name else "नमस्ते, "
        if effective_rating >= 5:
            reply = f"{greeting}अपना बहुमूल्य 5-स्टार फीडबैक देने के लिए आपका बहुत-बहुत धन्यवाद! यह जानकर बहुत खुशी हुई कि आपको हमारी सेवाएं पसंद आईं। हम सदैव आपको सर्वश्रेष्ठ अनुभव देने के लिए तत्पर हैं। {sign_off}"
        elif effective_rating == 4:
            reply = f"{greeting}शानदार 4-स्टार रेटिंग और हम पर भरोसा जताने के लिए बहुत-बहुत धन्यवाद! अगली बार आपको पूर्ण 5-स्टार अनुभव देने के लिए हम पूरी तरह तत्पर हैं। {sign_off}"
        elif effective_rating == 3:
            reply = f"{greeting}आपके निष्पक्ष फीडबैक के लिए धन्यवाद। हम सदैव 5-स्टार अनुभव प्रदान करने का प्रयास करते हैं। अपनी सेवा को और बेहतर बनाने के लिए आपके सुझावों का स्वागत है। {sign_off}"
        elif effective_rating == 2:
            reply = f"{greeting}आपकी अपेक्षाओं पर खरा न उतरने के लिए हम क्षमाप्रार्थी हैं। हम आपके फीडबैक को गंभीरता से लेते हुए आवश्यक सुधार कर रहे हैं। कृपया अपनी समस्या साझा करने हेतु हमसे संपर्क करें। {sign_off}"
        else:
            reply = f"{greeting}आपको हुई भारी असुविधा और परेशानी के लिए हमें गहरा खेद है। कृपया हमसे तुरंत संपर्क करें ताकि हम आपकी इस समस्या का तत्काल समाधान कर सकें। {sign_off}"
        detected_lang = "Hindi"
        lang_code = "hi"
    else:
        greeting = f"Dear {clean_name}, " if clean_name else "Dear Valued Customer, "
        if effective_rating >= 5:
            reply = f"{greeting}thank you so much for your wonderful 5-star review! We are thrilled to hear you had such a great experience with our team. We look forward to serving you again! {sign_off}"
        elif effective_rating == 4:
            reply = f"{greeting}thank you so much for the great 4-star rating and for trusting us! We look forward to serving you again and delivering a full 5-star experience next time! {sign_off}"
        elif effective_rating == 3:
            reply = f"{greeting}thank you for your honest feedback. While we are glad we could assist you, we constantly strive to deliver a 5-star experience. Please let us know how we can make your next visit even better! {sign_off}"
        elif effective_rating == 2:
            reply = f"{greeting}we apologize that your experience did not meet expectations. We take your feedback seriously and are actively taking steps to improve. Please get in touch with us directly so we can make things right. {sign_off}"
        else:
            reply = f"{greeting}we sincerely apologize for the inconvenience and frustration caused. We take such issues very seriously and are actively investigating this. Please reach out to us directly so we can resolve this immediately. {sign_off}"
        detected_lang = "English"
        lang_code = "en"

    return {
        "reply": reply.strip(),
        "language": lang_code,
        "detected_language": detected_lang,
        "rating": effective_rating,
        "tone": "professional_warm",
        "source": "local_fallback",
        "success": True,
    }




def find_matching_business(db: Session, query_str: str) -> Optional[Dict[str, Any]]:
    """
    Find business by key, ID, or name (case-insensitive fuzzy match).
    Allows the auto-replier to work for ANY organization in the system!
    """
    if not query_str:
        return None
    
    q = query_str.strip().lower()
    
    # 1. Exact key match
    biz = db.query(BusinessConfigModel).filter(BusinessConfigModel.key == q).first()
    if biz:
        from services.business_service import _serialize_business
        return _serialize_business(biz)
        
    # 2. Match by exact or partial name / ID
    all_bizs = db.query(BusinessConfigModel).all()
    for b in all_bizs:
        b_name = (b.name or "").lower()
        b_key = (b.key or "").lower()
        b_id = (b.id or "").lower()
        if q in b_name or b_name in q or q in b_key or q in b_id:
            from services.business_service import _serialize_business
            return _serialize_business(b)
            
    return None


def generate_review_reply(
    review_text: str,
    reviewer_name: str = "",
    rating: int = 5,
    business_context: Optional[Dict[str, Any]] = None,
    business_name: str = "",
    language: str = "auto",
    tone: str = "professional_warm",
    signature: str = "",
    auto_sign: bool = True,
) -> Dict[str, Any]:
    """
    Generate an authentic, personalized AI reply to a customer's Google review
    for ANY organization (Technobuzz, Rutuja Battery, Boardwale, Jawa Showroom, etc.).
    """
    if business_context is None:
        business_context = {}

    company_name = business_context.get("name") or business_name or "Our Business"
    scope = business_context.get("scope") or "Retail, professional services, customer support, and quality products."
    contact_phone = business_context.get("mobile") or business_context.get("alternate_mobile") or ""
    contact_email = business_context.get("email") or ""

    if not signature and auto_sign:
        saved_sig = business_context.get("reply_signature")
        signature = saved_sig if saved_sig else f"— Team {company_name}"


    clean_name = sanitize_reviewer_name(reviewer_name)
    rating = max(1, min(5, int(rating or 5)))

    # If no Gemini API key, use rich local fallback
    api_key = _gemini_api_key()
    if not api_key:
        logger.warning("GEMINI_API_KEY not found; using local fallback review reply generator.")
        return _local_fallback_reply(review_text, clean_name, rating, company_name, language, signature)

    tone_guidance = TONE_DESCRIPTIONS.get(tone, TONE_DESCRIPTIONS["professional_warm"])

    # Language prompt logic
    if language == "auto":
        language_instruction = """
LANGUAGE REQUIREMENT (VERY CRITICAL):
- Detect the language of the customer's review automatically.
- Write your ENTIRE reply in the EXACT SAME LANGUAGE and SCRIPT as the review!
- If the review is in Marathi (मराठी) (e.g., "आमच्या नवीन वेबसाईटचं डिझाइन अप्रतिम आहे..."), your reply MUST BE IN NATURAL, POLITE MARATHI (मराठी).
- If the review is in Hindi (हिन्दी), reply in Hindi (हिन्दी).
- If the review is in English, reply in English.
- If the review has no text or language is ambiguous, reply in English or Marathi based on location context.
"""
    elif language == "mr":
        language_instruction = "LANGUAGE REQUIREMENT: Write the entire reply in pure, natural, polite Marathi (मराठी)."
    elif language == "hi":
        language_instruction = "LANGUAGE REQUIREMENT: Write the entire reply in pure, natural, polite Hindi (हिन्दी)."
    elif language == "en":
        language_instruction = "LANGUAGE REQUIREMENT: Write the entire reply in English."
    else:
        lang_name = LANGUAGE_NAMES.get(language, language)
        language_instruction = f"LANGUAGE REQUIREMENT: Write the entire reply in {lang_name}."

    if clean_name:
        greeting_rule = f"- Greet the reviewer politely by their name '{clean_name}' (e.g. in Marathi: '{clean_name} जी, मनःपूर्वक धन्यवाद!', in English: 'Dear {clean_name}, thank you so much!')."
    else:
        greeting_rule = "- Do NOT use any digit or number as a name. Greet naturally without a specific name (e.g. in Marathi: 'नमस्कार,' / 'ग्राहक मित्र,', in Hindi: 'नमस्ते,', in English: 'Dear Valued Customer,' or 'Thank you for your feedback!'). NEVER write 'Dear 2', 'Dear 3', 'Dear 4', 'Dear Press'."

    prompt = f"""
You are the business owner/manager of "{company_name}".
A customer just left a {rating}-star review on your Google Business Profile.

YOUR TASK:
Write a genuine, personalized, and professional public reply to this customer's review.

CUSTOMER REVIEW DETAILS:
- Reviewer Name: {clean_name if clean_name else "(Not specified / Valued Customer)"}
- Star Rating: {rating} out of 5 stars
- Customer's Review Text: "{review_text}"

BUSINESS DETAILS:
- Business Name: {company_name}
- Business Scope / Services: {scope}
- Contact Details (for resolving negative reviews if applicable): Phone: {contact_phone}, Email: {contact_email}

{language_instruction}

TONE & STYLE:
- Tone: {tone_guidance}
{greeting_rule}
- CRITICAL NAME RULE: NEVER use digits, numbers, or UI words like '2', '3', '4', 'Press', 'Owner', 'Review' as the person's name!
- Acknowledge specific compliments or feedback mentioned in their review (e.g., if they praised website design, UI/UX, responsiveness, IT support, battery replacement, bike service, or speed, specifically mention that!).
- For 4 or 5 stars: Express warm gratitude, appreciate their trust, and welcome them back.
- For 1, 2, or 3 stars: Apologize sincerely for their subpar experience, show empathy, maintain total professionalism, and invite them to reach out directly via contact info so you can resolve the issue immediately.
- Sign off with: "{signature}" (if appropriate).
- Keep length between 2 to 4 sentences (concise, human, never robotic or templated).
- NEVER use generic placeholders like [Your Name] or [Insert Phone].
- Return ONLY valid JSON format.

OUTPUT JSON FORMAT:
{{
  "reply": "The complete generated reply text here",
  "language_code": "mr/en/hi/gu",
  "detected_language": "Marathi/English/Hindi",
  "sentiment": "positive/neutral/negative"
}}
""".strip()

    client = _get_client()
    load_dotenv(override=True)
    _cfg = get_config()
    candidate_models = [
        _cfg.GEMINI_MODEL,
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-1.5-flash",
        "gemini-flash-latest",
    ]
    seen = set()
    models = []
    for m in candidate_models:
        if m and m not in seen:
            seen.add(m)
            models.append(m)

    last_error = None
    for model_name in models:
        try:
            logger.info("Generating review reply with Gemini model %s", model_name)
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.8,
                    max_output_tokens=1024,
                    response_mime_type="application/json",
                ),
            )
            raw_text = response.text or ""
            cleaned = re.sub(r"```(?:json)?\s*", "", raw_text).strip().rstrip("`")
            data = json.loads(cleaned)

            reply_str = str(data.get("reply") or "").strip()
            if not reply_str:
                raise ValueError("Empty reply in Gemini JSON output")

            logger.info("Successfully generated review reply (%d chars) with %s", len(reply_str), model_name)
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
            logger.warning("Gemini reply generation with %s failed: %s", model_name, str(e))
            last_error = e
            continue

    logger.warning("All Gemini models failed (%s). Falling back to local reply generator.", last_error)
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
    source: str = "extension",
) -> ReviewReplyLog:
    """Save a record of the generated or sent review reply to the database."""
    clean_user_name = sanitize_reviewer_name(reviewer_name) or "Valued Customer"
    log_entry = ReviewReplyLog(
        business_key=business_key or "technobuzz",
        reviewer_name=clean_user_name,
        rating=rating,
        review_text=review_text or "",
        reply_text=reply_text or "",
        language=language or "auto",
        tone=tone or "professional_warm",
        status=status or "sent",
        source=source or "extension",
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
    """Retrieve recent review replies from the database."""
    query = db.query(ReviewReplyLog)
    if business_key:
        query = query.filter(ReviewReplyLog.business_key == business_key)
    return query.order_by(ReviewReplyLog.created_at.desc()).limit(limit).all()


def get_business_reply_settings(db: Session, business_key: str) -> Dict[str, Any]:
    """Get reply preferences for a business."""
    biz = db.query(BusinessConfigModel).filter(BusinessConfigModel.key == business_key).first()
    if not biz:
        return {
            "business_key": business_key,
            "company_name": "TechnoBuzz Systems",
            "reply_tone": "professional_warm",
            "reply_signature": "— Team TechnoBuzz Systems",
            "reply_language_mode": "auto",
            "auto_send_enabled": True,
            "auto_send_delay": 2,
        }
    return {
        "business_key": biz.key,
        "company_name": biz.name,
        "reply_tone": getattr(biz, "reply_tone", "professional_warm") or "professional_warm",
        "reply_signature": getattr(biz, "reply_signature", "") or f"— Team {biz.name}",
        "reply_language_mode": getattr(biz, "reply_language_mode", "auto") or "auto",
        "auto_send_enabled": getattr(biz, "auto_send_enabled", True) if getattr(biz, "auto_send_enabled", True) is not None else True,
        "auto_send_delay": getattr(biz, "auto_send_delay", 2) if getattr(biz, "auto_send_delay", 2) is not None else 2,
    }


def save_business_reply_settings(
    db: Session,
    business_key: str,
    reply_tone: str = "professional_warm",
    reply_signature: str = "",
    reply_language_mode: str = "auto",
    auto_send_enabled: bool = True,
    auto_send_delay: int = 2,
) -> bool:
    """Update review reply preferences for a business."""
    biz = db.query(BusinessConfigModel).filter(BusinessConfigModel.key == business_key).first()
    if not biz:
        return False
    biz.reply_tone = reply_tone
    biz.reply_signature = reply_signature
    biz.reply_language_mode = reply_language_mode
    biz.auto_send_enabled = auto_send_enabled
    biz.auto_send_delay = auto_send_delay
    db.commit()
    return True
