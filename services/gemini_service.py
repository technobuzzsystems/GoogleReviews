"""
services/gemini_service.py
--------------------------
Google Gemini AI service layer with retry logic, model fallback,
and strict rating-polarity verification.

Responsibilities:
    - Initialize the Gemini client with the API key from config.
    - Build rating-aware, strictly calibrated prompts (100% positive for 4-5 stars,
      balanced for 3 stars, critical/constructive for 1-2 stars).
    - Call the Gemini API with automatic retry and short backoff.
    - Try alternative models if the primary model is unavailable.
    - Parse, validate, and sanitize the JSON response.
    - Enforce a backend sentiment guard to ensure suggestions match the selected star rating.
    - Return a clean list of 10 AI-generated feedback suggestions.
"""

import json
import logging
import os
import re
import time
from typing import List

from google import genai
from google.genai import types
from google.genai.errors import ClientError, ServerError

from config import get_config
from dotenv import load_dotenv
from services.review_templates import templates_for

logger = logging.getLogger(__name__)

REVIEW_LANGUAGE_NAMES = {
    "en": "English",
    "mr": "Marathi (मराठी)",
    "hi": "Hindi (हिन्दी)",
    "gu": "Gujarati (ગુજરાતી)",
    "ta": "Tamil (தமிழ்)",
    "te": "Telugu (తెలుగు)",
    "kn": "Kannada (ಕನ್ನಡ)",
    "bn": "Bengali (বাংলা)",
    "pa": "Punjabi (ਪੰਜਾਬੀ)",
    "ml": "Malayalam (മലയാളം)",
}

_MAX_RETRIES = 2
_RETRY_DELAYS = [0.3, 0.5]
_REQUEST_TIMEOUT = 25
_TOTAL_BUDGET_SEC = 45

# Explicit negative indicators forbidden in 4-star and 5-star feedback
NEGATIVE_TRIGGER_WORDS = {
    "terrible", "horrible", "worst", "awful", "pathetic", "useless", "disaster",
    "waste of money", "waste of time", "rude staff", "unprofessional", "cheat",
    "fraud", "scam", "crashing", "crashed", "delayed", "delay", "poor service",
    "poor quality", "unresponsive", "disappointed", "disappointing", "frustrated",
    "frustrating", "regret", "not recommend", "never again", "avoid", "complaint",
    "let down", "careless", "ignored", "bad experience", "bad time",
    "खराब", "निराशा", "वाईट", "त्रासदायक", "बेजबाबदार", "दुर्लक्ष",
    "बुरा", "लापरवाह", "बेकार", "धोखा", "परेशान",
    "કચરો", "કંટાળાજનક",
    "ಕಳಪೆ", "ಕೆಟ್ಟ", "ನಿರಾಶೆ",
}

# Improvement, criticism, or shortcoming phrases forbidden in 4-star and 5-star reviews
# (4-star must be strictly and exclusively positive customer praise)
IMPROVEMENT_TRIGGER_WORDS = {
    "could be better", "needs improvement", "need improvement", "room for improvement",
    "not perfect", "could improve", "small delay", "slight delay", "minor delay",
    "slight wait", "small wait", "long wait", "waiting time", "one nitpick", "small nitpick",
    "only thing i would tweak", "only complain", "only complaint", "small miss", "minor issue",
    "could be smoother", "hope they improve", "room to improve", "tweak", "nitpick",
    "shortcoming", "shortcomings", "one minor note", "one minor suggestion", "below expectations",
    # Marathi
    "सुधारणा", "सुधारता येईल", "कमतरता", "तक्रार", "त्रुटी", "अडथळा", "अजून चांगलं", "थोडा वेळ",
    "विलंब", "उशीर", "सुधारावे", "अजून सुधारावे",
    # Hindi
    "सुधार", "कमी", "शिकायत", "बेहतर हो सकता", "सुधार सकते", "और अच्छा हो सकता", "और बेहतर हो सकता", "देरी", "इंतज़ार",
    "सुधारने की जरूरत",
    # Gujarati
    "સુધારો", "સુધારી શકાય", "ખામી", "તકલીફ", "સુધારવાની જરૂર",
    # Kannada
    "ಸುಧಾರಣೆ", "ದೂರು", "ಕಾಯುವ ಸಮಯ", "ಸುಧಾರಿಸಬೇಕಾಗಿದೆ",
}

# Explicit positive indicators required in 5-star feedback
POSITIVE_AFFIRMATIONS = {
    "excellent", "outstanding", "fantastic", "amazing", "great", "delighted",
    "wonderful", "superb", "highly recommend", "exceeded", "perfect", "loved",
    "impressed", "pleased", "satisfied", "top notch", "professional",
    "उत्कृष्ट", "अप्रतिम", "छान", "आनंद", "समाधान", "बढ़िया", "शानदार", "उत्तम",
}


def _gemini_api_key() -> str:
    load_dotenv(override=True)
    return (os.getenv("GEMINI_API_KEY") or "").strip()


def _get_client() -> genai.Client:
    """Return a Gemini API client. Reloads .env each time so key changes are picked up."""
    api_key = _gemini_api_key()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set. Please add it to your .env file.")

    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=_REQUEST_TIMEOUT * 1000),
    )


def _cap(text: str) -> str:
    text = (text or "").strip()
    return text[:1].upper() + text[1:] if text else text


def _example_phrases(business_context: dict, rating: int) -> List[str]:
    raw = business_context.get(f"examples_{rating}star") or ""
    phrases = [p.strip() for p in raw.split(",") if p.strip()]
    if phrases:
        return phrases

    # Safe rating-specific defaults if the business has no custom phrases
    name = (business_context.get("name") or "this business").strip()
    defaults = {
        5: [
            "the outstanding service",
            "the friendly and professional staff",
            "the top-notch quality",
            "the seamless and quick experience",
            "the great attention to detail",
            "the excellent overall support",
            "the prompt response and care",
            "the clean and welcoming setup",
            "the dependable and trustworthy team",
            "the wonderful atmosphere and outcome",
        ],
        4: [
            "the prompt service",
            "the helpful staff interaction",
            "the good product quality",
            "the smooth experience overall",
            "the reasonable turnaround time",
            "the courteous communication",
            "the reliable outcome",
            "the organized environment",
            "the fair value received",
            "the overall positive visit",
        ],
        3: [
            "the overall service speed",
            "the communication during busy hours",
            "the turnaround time",
            "the waiting duration",
            "the pricing clarity",
            "the staff responsiveness",
            "the process consistency",
            "the coordination",
            "the guidance provided",
            "the consistency across visits",
        ],
        2: [
            "the long wait time",
            "the lack of timely updates",
            "the uneven service quality",
            "the miscommunication on requirements",
            "the delays in response",
            "the disorganized handling",
            "the unaddressed concern",
            "the slow process",
            "the lack of attention from staff",
            "the unmet expectation",
        ],
        1: [
            "the unacceptable delays",
            "the lack of customer support",
            "the unprofessional attitude",
            "the poor handling of our request",
            "the severe communication breakdown",
            "the unfulfilled commitments",
            "the lack of responsiveness",
            "the completely disorganized experience",
            "the careless service",
            "the unresolved issues",
        ],
    }
    return defaults.get(rating, defaults[5])


def _local_suggestions(rating: int, business_context: dict, language: str = "en") -> List[str]:
    """Build 10 usable suggestions when Gemini is not configured or fails."""
    name = business_context.get("name") or "this business"
    phrases = _example_phrases(business_context, rating)
    while len(phrases) < 10:
        phrases.append(phrases[len(phrases) % max(len(phrases), 1)])

    templates = templates_for(language) or {
        1: [
            "Really disappointed with {name}. {issue} made the visit frustrating and I would not come back.",
            "Poor experience at {name}. {issue} and nobody seemed interested in fixing it.",
            "I expected better from {name}. {issue} ruined it for me.",
            "Not happy at all. {issue} and the whole process felt careless.",
            "Avoidable problems at {name}. {issue} should never happen.",
            "Very frustrating visit. {issue} and communication was almost nonexistent.",
            "This was a letdown. {issue} and I felt ignored when I asked for help.",
            "Had a bad time at {name}. {issue} and it took too long to get any response.",
            "Would not recommend {name} right now. {issue} was the main issue.",
            "One of the worst experiences I have had. {issue} and no follow-up.",
        ],
        2: [
            "It was below average at {name}. {issue} kept getting in the way.",
            "Not terrible, but {issue} made it hard to feel satisfied.",
            "I wanted to like {name}, but {issue} pulled the experience down.",
            "A bit disappointing. {issue} and things felt disorganized.",
            "Some parts were okay, yet {issue} was hard to ignore.",
            "Would give it another chance only if they fix {issue}.",
            "Service was uneven. {issue} stood out more than anything good.",
            "Left feeling underwhelmed. {issue} took the shine off the visit.",
            "Needs work. {issue} and the wait made it worse.",
            "Not what I hoped for at {name}. {issue} was frustrating.",
        ],
        3: [
            "Decent enough at {name}. {issue} kept it from being a clear yes.",
            "Mixed feelings. Some things were fine, but {issue} held it back.",
            "Average visit. {issue} and nothing really stood out.",
            "It was okay. {issue} could have been handled better.",
            "Not bad, not great. {issue} made the experience feel ordinary.",
            "Fair experience at {name}. {issue} is the part I would change.",
            "Would go again if they improve {issue}.",
            "Middle of the road. Staff tried, but {issue} was noticeable.",
            "Acceptable overall. {issue} stopped it from being better.",
            "Three stars because {issue} balanced out the good parts.",
        ],
        4: [
            "Had a wonderful experience at {name}! The service was prompt and the staff was very helpful.",
            "Really happy with the overall experience at {name}. Everything was handled professionally.",
            "Great service, friendly staff and a smooth experience at {name}. Highly recommended!",
            "Very satisfied with the quality of service at {name}. Would definitely visit again.",
            "A really pleasant experience with {name}. The team was supportive and professional.",
            "Solid and dependable service from {name}. Thoroughly satisfied with the visit.",
            "Courteous staff, well-organized environment, and good quality overall at {name}.",
            "Very pleased with how professionally everything was managed. Great visit to {name}.",
            "Good value, polite staff, and reliable support from {name}. Glad I chose them.",
            "Thoroughly enjoyed the experience at {name}. Professional and efficient team.",
        ],
        5: [
            "Excellent visit to {name}. {highlight} and I will definitely return.",
            "Loved the experience. {highlight} made it stand out.",
            "Could not ask for more from {name}. {highlight} was impressive.",
            "Fantastic from start to finish. {highlight} really showed.",
            "Highly satisfied. {highlight} and the team was great.",
            "One of the best experiences I have had. {highlight}.",
            "So glad I chose {name}. {highlight} exceeded what I expected.",
            "Five stars without hesitation. {highlight} and very professional.",
            "Will recommend {name} to others. {highlight} was exactly what I needed.",
            "Wonderful experience. {highlight} and I left really happy.",
        ],
    }

    lines = []
    for i, template in enumerate(templates[rating]):
        phrase = phrases[i]
        lines.append(
            template.format(name=name, issue=_cap(phrase), highlight=_cap(phrase))
        )
    return lines


def _build_prompt(rating: int, business_context: dict, language: str = "en", count: int = None) -> str:
    """Build a strictly calibrated rating-aware prompt for the Gemini model."""
    target_count = count or (3 if rating in (1, 2, 3) else 10)
    company_name = business_context.get("name", "TechnoBuzz")
    scope = business_context.get(
        "scope",
        "Quality service, reliable customer support, attention to detail, and professional assistance.",
    )
    language_name = REVIEW_LANGUAGE_NAMES.get(language, REVIEW_LANGUAGE_NAMES["en"])

    # Language-specific prompt blocks ensuring natural grammar, correct sentence flow, and zero robotic translation
    lang_rules = {
        "mr": f"""
LANGUAGE: MARATHI (मराठी) - CRITICAL REQUIREMENTS:
- Write EVERY suggestion strictly in natural, authentic, grammatically correct Marathi (शुद्ध, सहज आणि अस्खलित मराठी).
- Do NOT translate literally from English. Write like a real native Marathi speaker in Maharashtra posting a genuine Google review.
- Use natural Marathi sentence patterns (उदा. 'खूप छान अनुभव आला', 'उत्कृष्ट सेवा आणि सहकार्य', 'स्टाफ अत्यंत नम्र आणि तत्पर आहे', 'नक्की पुन्हा भेट देईन').
- Maintain correct gender and verbal agreements in Marathi.
- You may keep the business name "{company_name}" in its original or Devanagari script.
- Do NOT mix English sentences.
""",
        "hi": f"""
LANGUAGE: HINDI (हिन्दी) - CRITICAL REQUIREMENTS:
- Write EVERY suggestion strictly in natural, authentic, grammatically correct Hindi (सहज, स्पष्ट और प्रामाणिक हिंदी).
- Do NOT translate word-by-word from English. Write like a real native Hindi speaker posting a genuine Google review.
- Use natural Hindi phrasing (e.g. 'बहुत बढ़िया अनुभव रहा', 'सेवा बहुत अच्छी और समय पर थी', 'स्टाफ काफी मददगार और विनम्र है', 'निश्चित रूप से सिफारिश करूँगा').
- Maintain correct Hindi grammar, sentence structure, and respectful tone.
- You may keep the business name "{company_name}" in its original or Devanagari script.
- Do NOT mix English sentences.
""",
        "gu": f"""
LANGUAGE: GUJARATI (ગુજરાતી) - CRITICAL REQUIREMENTS:
- Write EVERY suggestion strictly in natural, authentic, grammatically correct Gujarati (સરળ, શુદ્ધ અને અસલ ગુજરાતી).
- Write like a real native Gujarati speaker posting a genuine Google review.
- Use natural Gujarati expressions (e.g. 'ખૂબ જ સારો અનુભવ રહ્યો', 'સેવા ઉત્તમ અને સમયસર હતી', 'સ્ટાફ ખૂબ મદદરૂપ છે').
- Maintain correct Gujarati grammar.
- You may keep the business name "{company_name}" in its original form.
""",
        "kn": f"""
LANGUAGE: KANNADA (ಕನ್ನಡ) - CRITICAL REQUIREMENTS:
- Write EVERY suggestion strictly in natural, authentic, grammatically correct Kannada (ಸಹಜ ಮತ್ತು ವ್ಯಾಕರಣಬದ್ಧ ಕನ್ನಡ).
- Write like a real native Kannada speaker posting a genuine Google review.
- Use natural Kannada expressions (e.g. 'ತುಂಬಾ ಉತ್ತಮವಾದ ಅನುಭವ', 'ಸೇವೆ ಅತ್ಯುತ್ತಮವಾಗಿದೆ', 'ಸಿಬ್ಬಂದಿ ತುಂಬಾ ಸಹಕಾರಿಯಾಗಿದ್ದಾರೆ').
- Maintain correct Kannada grammar.
- You may keep the business name "{company_name}" in its original form.
""",
        "en": """
LANGUAGE: ENGLISH - CRITICAL REQUIREMENTS:
- Write fluent, natural and grammatically correct English.
- Write like a genuine real customer sharing their authentic experience.
""",
    }

    language_block = lang_rules.get(language, f"""
LANGUAGE (CRITICAL):
Write EVERY suggestion entirely in {language_name}.
- Use natural everyday {language_name} that a real local customer would type.
- Do NOT write in English. Do NOT mix English sentences.
- You may keep the business name "{company_name}" in its original form.
""")

    # Rating-exclusive tone instructions & strictly matched few-shot examples
    if rating == 5:
        tone_instruction = f"""
TONE: 5 STAR — EXCELLENT, DELIGHTED, 100% ENTHUSIASTIC PRAISE.
The customer had an outstanding, flawless experience.
Key highlights: {business_context.get('examples_5star', 'exceptional service, warm and professional staff, top quality, highly recommended')}
ABSOLUTE RULE: Every single review MUST be 100% positive praise. Zero complaints, zero criticisms, zero negatives.
Length: 1 to 2 natural sentences per review.

FEW-SHOT EXAMPLES (MATCH THIS EXCELLENT POSITIVE TONE):
- "Absolutely fantastic experience at {company_name}! Excellent service and outstanding customer support."
- "One of the best experiences I have had. Top-notch quality and professional staff. Highly recommended!"
- "Completely satisfied! Everything was perfect from start to finish. Truly impressive work."
- "Loved my visit to {company_name}. Outstanding quality and friendly support. Will definitely recommend them!"
"""
    elif rating == 4:
        tone_instruction = f"""
TONE: 4 STAR — VERY GOOD EXPERIENCE, EXCLUSIVELY POSITIVE FEEDBACK.
CRITICAL MANDATE:
- The customer is VERY HAPPY with the business.
- Generate ONLY positive feedback that highlights a great experience.
- ABSOLUTELY FORBIDDEN: Do NOT mention any improvement suggestions, shortcomings, complaints, criticisms, or statements such as "could be better", "needs improvement", "not perfect", "room for improvement", "only small delay", or "slight wait".
- The review must make the business look good and reflect a very good, pleasant customer experience.
Key highlights: {business_context.get('examples_4star', 'great service, helpful staff, high quality, smooth experience, professional team')}
Length: 1 to 2 natural sentences per review.

FEW-SHOT EXAMPLES (MATCH THIS VERY GOOD POSITIVE TONE — ZERO COMPLAINTS, ZERO IMPROVEMENT SUGGESTIONS):
- "Had a wonderful experience at {company_name}! The service was excellent, and the staff was very helpful."
- "Really happy with the overall experience. Everything was handled professionally and smoothly."
- "Great service, friendly staff and a smooth experience at {company_name}. Highly recommended!"
- "Very satisfied with the service and quality. Would definitely visit again."
- "A really pleasant experience with {company_name}. The team was supportive and attentive."
"""
    elif rating == 3:
        tone_instruction = f"""
TONE: 3 STAR — BALANCED, AVERAGE, NEUTRAL (SATISFACTORY WITH ROOM FOR IMPROVEMENT).
The experience was average or okay, with minor areas that could be better.
Key themes: {business_context.get('examples_3star', 'average service, satisfactory outcome, some room for improvement')}
Length: 1 to 2 natural sentences per review.

FEW-SHOT EXAMPLES (MATCH THIS BALANCED/AVERAGE TONE):
- "The experience at {company_name} was average. There is some room for improvement."
- "The service was okay, but I expected a little more from the team."
- "Overall it was a satisfactory experience, although some aspects could be better."
- "Decent service for the price. Does the job, but there is noticeable room for process improvement."
"""
    elif rating == 2:
        tone_instruction = f"""
TONE: 2 STAR — DISAPPOINTED, BELOW AVERAGE, CLEAR SHORTCOMINGS.
The customer felt let down and had unmet expectations.
Key themes: {business_context.get('examples_2star', 'slow service, disorganized process, poor responsiveness, unmet expectations')}
Length: 1 to 2 natural sentences per review.

FEW-SHOT EXAMPLES (MATCH THIS DISAPPOINTED TONE):
- "The overall experience at {company_name} was below my expectations."
- "I expected better service and was disappointed with the delays and lack of updates."
- "The service quality could have been much better. The process felt quite disorganized."
"""
    else:  # rating == 1
        tone_instruction = f"""
TONE: 1 STAR — HIGHLY CRITICAL, FRUSTRATED, VERY POOR EXPERIENCE.
The customer had a very disappointing experience with critical service failures.
Key themes: {business_context.get('examples_1star', 'unacceptable service, rude behavior, poor quality, zero accountability')}
Length: 1 to 2 natural sentences per review.

FEW-SHOT EXAMPLES (MATCH THIS CRITICAL TONE):
- "Very disappointing experience with {company_name}. The service did not meet my expectations at all."
- "I was not satisfied with the overall experience. Communication and customer support were very poor."
- "The service quality needs significant improvement. Would not recommend."
"""

    if target_count == 3:
        format_example = """[
  "Review 1...",
  "Review 2...",
  "Review 3..."
]"""
    else:
        format_example = """[
  "Review 1...",
  "Review 2...",
  "Review 3...",
  "Review 4...",
  "Review 5...",
  "Review 6...",
  "Review 7...",
  "Review 8...",
  "Review 9...",
  "Review 10..."
]"""

    return f"""
You are an expert review curator writing authentic customer feedback.

Your task is to generate exactly {target_count} distinct, natural customer reviews based on:
Company Name: {company_name}
Star Rating: {rating} out of 5
Business Scope: {scope}
{language_block}

{tone_instruction}

CRITICAL RULES:
1. Every review must sound like it was typed by a different real customer on Google Reviews.
2. Vary sentence structure, vocabulary, and phrasing across all {target_count} suggestions.
3. No robotic, overly formal, or generic AI language.
4. Do NOT number the suggestions.
5. Return ONLY a valid JSON array of strings containing exactly {target_count} suggestions.
6. No markdown, no notes, no commentary.

OUTPUT FORMAT:
{format_example}
""".strip()


def _parse_response(raw_text: str, expected_count: int = 10) -> List[str]:
    """
    Parse the raw Gemini text response into a clean list of strings.
    Handles markdown code fences and validates the JSON array.
    """
    cleaned = re.sub(r"```(?:json)?\s*", "", raw_text).strip()
    cleaned = cleaned.replace("```", "").strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error("Gemini response is not valid JSON.\nRaw: %s", raw_text[:500])
        raise ValueError(f"Gemini returned invalid JSON: {e}") from e

    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON array, got {type(data).__name__}.")

    suggestions = [str(item).strip() for item in data if str(item).strip()]

    min_needed = 2 if expected_count <= 3 else min(10, expected_count)
    if len(suggestions) < min_needed:
        raise ValueError(
            f"Gemini returned only {len(suggestions)} suggestion(s). Expected at least {min_needed}."
        )

    return suggestions[:expected_count]


def validate_and_sanitize_suggestions(
    suggestions: List[str],
    rating: int,
    business_context: dict,
    language: str = "en",
    target_count: int = None,
) -> List[str]:
    """
    Backend Polarity Safety Guard:
    Scans generated suggestions against forbidden words for the given rating.
    - For 5 Stars: Replaces any review containing negative words with verified positive reviews.
    - For 1-2 Stars: Replaces any overly cheerful/5-star reviews with constructive negative reviews.
    - Enforces target_count (3 for 1-3 stars, 10 for 4-5 stars).
    Guarantees that 5-star ratings NEVER display negative feedback suggestions.
    """
    target_count = target_count or (3 if rating in (1, 2, 3) else 10)
    if not suggestions:
        fallback_all = _local_suggestions(rating, business_context, language)
        return fallback_all[:target_count]

    sanitized: List[str] = []
    fallback_pool = _local_suggestions(rating, business_context, language)
    fallback_idx = 0

    for item in suggestions:
        text = str(item).strip()
        lower = text.lower()
        is_invalid = False

        if rating in (4, 5):
            # 4-star and 5-star check: NO negative words and NO improvement/shortcoming phrases allowed
            for bad_word in NEGATIVE_TRIGGER_WORDS:
                if bad_word in lower:
                    logger.warning(
                        "Polarity Guard: rejected negative word '%s' in %d-star review: %s",
                        bad_word,
                        rating,
                        text,
                    )
                    is_invalid = True
                    break

            if not is_invalid:
                for imp_phrase in IMPROVEMENT_TRIGGER_WORDS:
                    if imp_phrase in lower:
                        logger.warning(
                            "Polarity Guard: rejected improvement phrase '%s' in %d-star review: %s",
                            imp_phrase,
                            rating,
                            text,
                        )
                        is_invalid = True
                        break

        elif rating in (1, 2):
            # 1-2 star check: overly ecstatic praise should not appear in 1-star
            for ecstatic in ["loved it", "best experience ever", "perfect from start to finish", "five stars without hesitation"]:
                if ecstatic in lower:
                    is_invalid = True
                    break

        if is_invalid:
            # Replace with a safe, verified fallback suggestion for this rating
            replacement = fallback_pool[fallback_idx % len(fallback_pool)]
            fallback_idx += 1
            sanitized.append(replacement)
        else:
            sanitized.append(text)

    # Ensure target_count suggestions
    while len(sanitized) < target_count:
        sanitized.append(fallback_pool[len(sanitized) % len(fallback_pool)])

    return sanitized[:target_count]


def _call_model(client: genai.Client, model: str, prompt: str, expected_count: int = 10) -> List[str]:
    """
    Attempt to call a single model with fast retry logic.
    Gives up quickly on 429/503 to preserve the total time budget.
    """
    for attempt in range(_MAX_RETRIES):
        try:
            logger.info("Gemini call: model=%s attempt=%d", model, attempt + 1)

            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.6,
                    max_output_tokens=4096,
                    response_mime_type="application/json",
                ),
            )

            suggestions = _parse_response(response.text, expected_count=expected_count)
            logger.info("Gemini [%s] returned %d suggestions.", model, len(suggestions))
            return suggestions

        except ValueError:
            raise

        except (ClientError, ServerError) as e:
            status = getattr(e, "status_code", None) or getattr(e, "code", 0)
            if status in (429, 503) and attempt < _MAX_RETRIES - 1:
                wait = _RETRY_DELAYS[attempt]
                logger.warning("Gemini [%s] %s; retrying in %.1fs.", model, status, wait)
                time.sleep(wait)
                continue

            logger.warning("Gemini [%s] failed: %s.", model, status)
            raise RuntimeError(f"Model {model} failed ({status})") from e

        except Exception as e:
            logger.warning("Gemini [%s] error: %s", model, str(e))
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_DELAYS[attempt])
                continue

            raise RuntimeError(f"Model {model} error: {str(e)}") from e

    raise RuntimeError(f"All retries failed for model {model}.")


def generate_feedback_suggestions(
    rating: int,
    business_context: dict = None,
    language: str = "en",
    count: int = None,
) -> List[str]:
    """
    Call Google Gemini AI and return validated feedback suggestion strings.
    - 1-3 Stars: returns 3 suggestions.
    - 4-5 Stars: returns 10 suggestions.
    Speed guarantee with polarity safety gate.
    """
    if business_context is None:
        business_context = {}

    if not isinstance(rating, int) or rating < 1 or rating > 5:
        raise ValueError(f"Invalid rating '{rating}'. Must be an integer between 1 and 5.")

    language = (language or "en").strip().lower()
    if language not in REVIEW_LANGUAGE_NAMES:
        language = "en"

    target_count = count or (3 if rating in (1, 2, 3) else 10)
    prompt = _build_prompt(rating, business_context, language, count=target_count)

    if not _gemini_api_key():
        logger.warning("GEMINI_API_KEY is not set; returning verified local suggestions.")
        return _local_suggestions(rating, business_context, language)[:target_count]

    client = _get_client()

    load_dotenv(override=True)
    _current_config = get_config()
    _raw_chain = [
        _current_config.GEMINI_MODEL,
        "gemini-2.0-flash",
        "gemini-1.5-flash",
        "gemini-2.5-flash",
        "gemini-1.5-flash-8b",
    ]
    seen = set()
    models = []
    for model in _raw_chain:
        if model and model not in seen:
            seen.add(model)
            models.append(model)

    deadline = time.monotonic() + _TOTAL_BUDGET_SEC
    last_error = None

    for model in models:
        if time.monotonic() >= deadline:
            logger.warning("Time budget exhausted before model %s.", model)
            break

        try:
            raw_suggestions = _call_model(client, model, prompt, expected_count=target_count)
            return validate_and_sanitize_suggestions(
                raw_suggestions, rating, business_context, language, target_count=target_count
            )
        except ValueError:
            raise
        except RuntimeError as e:
            last_error = e
            logger.warning("Model %s failed (%s); trying next fallback.", model, e)
            continue

    elapsed = round(time.monotonic() - (deadline - _TOTAL_BUDGET_SEC), 2)
    logger.error(
        "All AI models failed for rating=%d after %.2fs. Last error: %s. Falling back to local templates.",
        rating,
        elapsed,
        last_error,
    )
    fallback = _local_suggestions(rating, business_context, language)
    return validate_and_sanitize_suggestions(fallback, rating, business_context, language, target_count=target_count)


def _local_star_examples(name: str, scope: str) -> dict:
    """Phrase lists when Gemini is unavailable — scoped cleanly to the business."""
    parts = [p.strip(" .") for p in re.split(r"[,;/]|\band\b", scope or "") if len(p.strip()) > 2]
    services = parts[:5] or ["service", "staff", "quality", "support"]
    s1 = services[0]
    s2 = services[min(1, len(services) - 1)]
    s3 = services[min(2, len(services) - 1)]
    label = (name or "this business").strip() or "this business"
    return {
        "examples_1star": (
            f"terrible {s1}, rude or unhelpful staff at {label}, extremely delayed {s2}, "
            f"poor quality {s3}, ignored complaints, overpriced with no value"
        ),
        "examples_2star": (
            f"slow {s1}, disorganized {s2}, weak communication, long wait, "
            f"below-average {s3}, staff seemed uninterested"
        ),
        "examples_3star": (
            f"average {s1}, {s2} was okay but nothing special, mixed {s3}, "
            f"pricing felt high for what we got, decent but forgettable visit to {label}"
        ),
        "examples_4star": (
            f"good {s1}, helpful staff, solid {s2}, smooth {s3}, "
            f"thoroughly satisfied and would recommend {label}"
        ),
        "examples_5star": (
            f"excellent {s1}, outstanding {s2}, professional team at {label}, "
            f"fast and reliable {s3}, would definitely return and recommend"
        ),
    }


def _normalize_star_examples(data) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object of star examples.")
    out = {}
    for i in range(1, 6):
        val = (
            data.get(f"examples_{i}star")
            or data.get(str(i))
            or data.get(i)
            or data.get(f"{i}_star")
            or data.get(f"{i}star")
        )
        if isinstance(val, list):
            val = ", ".join(str(x).strip() for x in val if str(x).strip())
        text = re.sub(r"\s+", " ", str(val or "")).strip().strip(",")
        if not text:
            raise ValueError(f"Missing examples for {i} star.")
        out[f"examples_{i}star"] = text[:800]
    return out


def generate_star_example_prompts(name: str, scope: str) -> dict:
    """
    Generate comma-separated review themes for 1–5 stars from business scope.
    Used to auto-fill AI Prompt Examples on the add/edit business form.
    """
    name = (name or "").strip() or "this business"
    scope = (scope or "").strip()
    if len(scope) < 8:
        raise ValueError("Enter Business Scope & Services first (at least a short description).")

    if not _gemini_api_key():
        logger.warning("GEMINI_API_KEY is not set; returning local star examples.")
        return _local_star_examples(name, scope)

    prompt = f"""
You write short customer-review THEMES (not full reviews) for a Google-review assistant.

Business name: {name}
Business scope and services:
{scope}

Return ONLY JSON with these keys:
"examples_1star", "examples_2star", "examples_3star", "examples_4star", "examples_5star"

Each value is one comma-separated string of 5 to 8 short phrases.
Phrases must be specific to THIS business's services (not generic "bad service").
No numbering. No quotes inside phrases. No markdown.

Meaning of each rating:
- examples_1star: serious complaints (negative)
- examples_2star: disappointing issues
- examples_3star: mixed / average
- examples_4star: very good experience, positive praise (no complaints or improvement notes)
- examples_5star: enthusiastic praise (positive)

Example shape (do not copy the content):
{{"examples_1star": "phrase one, phrase two, phrase three", "examples_2star": "...", "examples_3star": "...", "examples_4star": "...", "examples_5star": "..."}}
""".strip()

    client = _get_client()
    load_dotenv(override=True)
    _current_config = get_config()
    _raw_chain = [
        _current_config.GEMINI_MODEL,
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-flash-latest",
    ]
    seen = set()
    models = []
    for model in _raw_chain:
        if model not in seen:
            seen.add(model)
            models.append(model)

    last_error = None
    for model in models:
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.7,
                    max_output_tokens=1200,
                    response_mime_type="application/json",
                ),
            )
            cleaned = re.sub(r"```(?:json)?\s*", "", (response.text or "")).strip()
            cleaned = cleaned.replace("```", "").strip()
            data = json.loads(cleaned)
            examples = _normalize_star_examples(data)
            logger.info("Generated star example prompts with %s", model)
            return examples
        except Exception as e:
            last_error = e
            logger.warning("Star examples via %s failed: %s", model, e)
            continue

    logger.warning("Falling back to local star examples: %s", last_error)
    return _local_star_examples(name, scope)
