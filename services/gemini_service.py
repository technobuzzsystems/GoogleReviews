"""
services/gemini_service.py
--------------------------
Google Gemini AI service layer with retry logic and model fallback.

Responsibilities:
    - Initialize the Gemini client with the API key from config.
    - Build rating-aware, structured prompts.
    - Call the Gemini API with automatic retry and short backoff.
    - Try alternative models if the primary model is unavailable.
    - Parse, validate, and sanitize the JSON response.
    - Return a clean list of 10 AI-generated feedback suggestions.

If Gemini is not configured, this module returns local rating-based
suggestions so the feedback page still works.
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
    return phrases or ["the overall experience"]


def _local_suggestions(rating: int, business_context: dict, language: str = "en") -> List[str]:
    """Build 10 usable suggestions when Gemini is not configured."""
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
            "Good experience at {name}. Only thing I would tweak is {issue}.",
            "Pretty happy with the visit. {issue} was a small miss.",
            "Solid service. {issue} is the one area they could improve.",
            "I liked {name}. A little more attention to {issue} would make it perfect.",
            "Went well overall. {issue} was minor compared to the rest.",
            "Would recommend {name}. Just a note on {issue}.",
            "Really close to excellent. {issue} is the only nitpick.",
            "Positive visit. {issue} did not spoil it, but it is worth mentioning.",
            "Good value and friendly team. {issue} could be smoother next time.",
            "Enjoyed it. If they sort {issue}, I would give five stars.",
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


def _build_prompt(rating: int, business_context: dict, language: str = "en") -> str:
    """Build a rating-aware prompt for the Gemini model."""
    company_name = business_context.get("name", "TechnoBuzz")
    scope = business_context.get("scope", "Software development, web design, cloud infrastructure, network architecture, cybersecurity, and managed IT support.")
    language_name = REVIEW_LANGUAGE_NAMES.get(language, REVIEW_LANGUAGE_NAMES["en"])
    language_block = ""
    if language != "en":
        language_block = f"""
LANGUAGE (CRITICAL)

Write EVERY suggestion entirely in {language_name}.
The customer chose this language for their Google review.
- Use natural everyday {language_name} that a real local customer would type.
- Do NOT write the reviews in English.
- You may keep the business name "{company_name}" in its original form.
- Do not mix English sentences. A few common loanwords are OK if locals actually use them.
"""

    return f"""
You are an expert at writing realistic customer feedback for a service provider.

Your task is to generate exactly 10 customer feedback suggestions based on the provided company name and star rating.

Company Name:
{company_name}

Star Rating:
{rating}/5

Scope of Services:
{scope}
{language_block}
IMPORTANT REQUIREMENTS

1. Every response MUST sound like it was written by a real client.
2. NEVER sound like an AI assistant.
3. NEVER use robotic, overly formal, or generic corporate language.
4. Write naturally, reflecting real-world business interactions with a vendor.
5. Every suggestion should have a different writing style and wording.
6. Mix sentence lengths naturally.
7. Avoid repeating phrases.
8. Some reviews can focus on specific service areas, some on delivery, some on communication or support.
9. Do NOT number the suggestions.
10. Return ONLY a JSON array of strings.
11. No markdown.
12. No explanations.
13. No headings.
14. Each suggestion should feel original.

TONE BASED ON STAR RATING

1 STAR

Generate detailed negative feedback.
The client is clearly frustrated. Mention specific grievances like:
- {business_context.get("examples_1star", "buggy deployments or broken code, critical downtime due to poor infrastructure planning, missed project deadlines, lack of communication from the support team, security vulnerabilities left unpatched, unprofessional conduct or incompetence")}
Length: 2-4 natural sentences.

2 STAR

Mostly negative.
The client had high expectations for their project, but felt let down. 
Mention specific issues like:
- {business_context.get("examples_2star", "slow response to support tickets, complex UI/UX in web design")}
Length: 2-3 sentences.

3 STAR

Mixed opinion.
Balanced feedback. Perhaps the team is skilled, but project management is disorganized, or vice-versa.
Mention both pros and cons like:
- {business_context.get("examples_3star", "technical team is skilled but the project management is disorganized, billing confusion or slow response")}
Length: 2-3 sentences.

4 STAR

Mostly positive.
The client is satisfied with the service or support provided.
Mention one small area for improvement like:
- {business_context.get("examples_4star", "better documentation, more frequent status updates")}
Length: 2-3 sentences.

5 STAR

Generate enthusiastic and genuine positive reviews.
The client should sound happy with the partnership. Mention things like:
- {business_context.get("examples_5star", "seamless cloud migration, reliable network uptime, intuitive design interface, proactive security measures, knowledgeable engineering team, project delivered ahead of schedule")}
Length: 2-4 natural sentences.

WRITING STYLE

Pretend every suggestion is written by a different client.
Avoid making every review have the same structure.
Avoid repeating:
"Great service."
"The team is excellent."
"Overall..."

Do not use AI phrases such as:
"The company demonstrates excellence in IT solutions."
"The technical team exhibits deep knowledge."
"It would be beneficial for the company to..."
"I highly recommend their services."

Instead write naturally like:
"We had some serious issues with the new web deployment, it kept crashing under load."
"The cloud migration went surprisingly smoothly, minimal downtime for our users."
"They really understood our security requirements for the firewall setup."
"Communication on the backend project was a bit patchy, but the final build works great."
"I'm not happy with how long it took them to resolve our server issue."
"Finally, a team that actually knows what they are doing with AWS."

OUTPUT FORMAT

Return ONLY this:
[
  "...",
  "...",
  "...",
  "...",
  "...",
  "...",
  "...",
  "...",
  "...",
  "..."
]

Nothing else.
""".strip()


def _parse_response(raw_text: str) -> List[str]:
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

    if len(suggestions) < 10:
        raise ValueError(
            f"Gemini returned only {len(suggestions)} suggestion(s). Expected 10."
        )

    return suggestions[:10]


def _call_model(client: genai.Client, model: str, prompt: str) -> List[str]:
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
                    temperature=0.95,
                    max_output_tokens=4096,
                    response_mime_type="application/json",
                ),
            )

            suggestions = _parse_response(response.text)
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
) -> List[str]:
    """
    Call Google Gemini AI and return 10 feedback suggestion strings.

    Speed guarantee:
        - Each Gemini HTTP call times out after 25 seconds.
        - Total budget across all models/retries is 45 seconds.
        - If AI generation fails, an error is raised instead of static suggestions.
    """
    if business_context is None:
        business_context = {}

    if not isinstance(rating, int) or rating < 1 or rating > 5:
        raise ValueError(f"Invalid rating '{rating}'. Must be an integer between 1 and 5.")

    language = (language or "en").strip().lower()
    if language not in REVIEW_LANGUAGE_NAMES:
        language = "en"

    prompt = _build_prompt(rating, business_context, language)

    if not _gemini_api_key():
        logger.warning("GEMINI_API_KEY is not set; returning local suggestions.")
        return _local_suggestions(rating, business_context, language)

    client = _get_client()

    # Build model list fresh from current config (avoids stale module-level cache)
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

    deadline = time.monotonic() + _TOTAL_BUDGET_SEC
    last_error = None

    for model in models:
        if time.monotonic() >= deadline:
            logger.warning("Time budget exhausted before model %s.", model)
            break

        try:
            return _call_model(client, model, prompt)
        except ValueError:
            raise
        except RuntimeError as e:
            last_error = e
            logger.warning("Model %s failed; trying next fallback.", model)
            continue

    elapsed = round(time.monotonic() - (deadline - _TOTAL_BUDGET_SEC), 2)
    logger.error(
        "AI feedback generation failed for rating=%d after %.2fs (budget=%.1fs). Last error: %s",
        rating,
        elapsed,
        _TOTAL_BUDGET_SEC,
        last_error,
    )
    raise RuntimeError("AI feedback generation failed. Please try again.") from last_error


def _local_star_examples(name: str, scope: str) -> dict:
    """Phrase lists when Gemini is unavailable — still scoped to the business."""
    parts = [p.strip(" .") for p in re.split(r"[,;/]|\band\b", scope or "") if len(p.strip()) > 2]
    services = parts[:5] or ["service", "staff", "quality", "wait time"]
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
            f"good {s1}, helpful staff, solid {s2}, only a small delay on {s3}, "
            f"would recommend {label} with a minor improvement"
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
- examples_4star: mostly good, small improvements
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
