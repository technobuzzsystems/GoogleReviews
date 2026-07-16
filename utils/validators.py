"""
utils/validators.py
--------------------
Server-side input validation utilities.

All validation logic lives here — never inside route functions —
so that routes stay thin and validation is easily testable.
"""

from typing import Tuple, Optional


def validate_rating(rating) -> Tuple[bool, Optional[str]]:
    """
    Validate that a rating is a whole integer between 1 and 5.

    Args:
        rating: Raw value from the request body (may be any type).

    Returns:
        (True, None)                       — if valid
        (False, "human-readable message")  — if invalid
    """
    if rating is None:
        return False, "Rating is required."
    if not isinstance(rating, int):
        try:
            rating = int(rating)
        except (ValueError, TypeError):
            return False, "Rating must be a whole number between 1 and 5."
    if rating < 1 or rating > 5:
        return False, "Rating must be between 1 and 5."
    return True, None


def validate_feedback_submission(data: dict) -> Tuple[bool, Optional[str]]:
    """
    Validate the complete feedback submission payload.

    Expected keys: company, company_id, rating, feedback

    Args:
        data (dict): Parsed JSON request body.

    Returns:
        (True, None) or (False, error_message)
    """
    if not data:
        return False, "Request body is empty."

    # Check all required fields are present
    required_fields = ["company", "company_id", "rating", "feedback"]
    for f in required_fields:
        if f not in data:
            return False, f"Missing required field: '{f}'."

    # Company name must be non-empty
    if not str(data.get("company", "")).strip():
        return False, "Company name cannot be empty."

    # Company ID must be non-empty
    if not str(data.get("company_id", "")).strip():
        return False, "Company ID cannot be empty."

    # Rating must be valid
    is_valid, rating_error = validate_rating(data.get("rating"))
    if not is_valid:
        return False, rating_error

    # Feedback text must be non-empty and within length limit
    feedback = str(data.get("feedback", "")).strip()
    if not feedback:
        return False, "Feedback text cannot be empty."
    if len(feedback) > 1000:
        return False, "Feedback text is too long (max 1000 characters)."

    return True, None


def sanitize_string(value: str, max_length: int = 500) -> str:
    """
    Strip whitespace and truncate a string to a safe maximum length.

    Args:
        value      (str): Raw string input.
        max_length (int): Maximum allowed character length.

    Returns:
        str: Sanitized string.
    """
    if not isinstance(value, str):
        return ""
    return value.strip()[:max_length]
