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

If Gemini cannot generate suggestions, this module raises an error. It does
not return static fallback responses.
"""

import json
import logging
import re
import time
from typing import List

from google import genai
from google.genai import types
from google.genai.errors import ClientError, ServerError

from config import get_config

config = get_config()
logger = logging.getLogger(__name__)

# If the primary model is rate-limited or unavailable, try the next one.
_MODEL_FALLBACK_CHAIN = [
    config.GEMINI_MODEL,
    "gemini-2.5-flash",
    "gemini-2.0-flash-lite",
    "gemini-flash-latest",
]

_MAX_RETRIES = 2
_RETRY_DELAYS = [0.3, 0.5]
_REQUEST_TIMEOUT = 25
_TOTAL_BUDGET_SEC = 45

def _get_client() -> genai.Client:
    """Return a Gemini API client. Raises RuntimeError if key is missing."""
    if not config.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set. Please add it to your .env file.")

    return genai.Client(
        api_key=config.GEMINI_API_KEY,
        http_options=types.HttpOptions(timeout=_REQUEST_TIMEOUT * 1000),
    )


def _build_prompt(rating: int, business_context: dict) -> str:
    """Build a rating-aware prompt for the Gemini model."""
    company_name = business_context.get("name", config.COMPANY_NAME)
    scope = business_context.get("scope", "Software development, web design, cloud infrastructure, network architecture, cybersecurity, and managed IT support.")
    
    return f"""
You are an expert at writing realistic customer feedback for a service provider.

Your task is to generate exactly 10 customer feedback suggestions based on the provided company name and star rating.

Company Name:
{company_name}

Star Rating:
{rating}/5

Scope of Services:
{scope}

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


def generate_feedback_suggestions(rating: int, business_context: dict = None) -> List[str]:
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

    prompt = _build_prompt(rating, business_context)
    client = _get_client()

    seen = set()
    models = []
    for model in _MODEL_FALLBACK_CHAIN:
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
