"""Helpers to build Google review write-URLs from a Place ID."""

GOOGLE_WRITE_REVIEW_URL = "https://search.google.com/local/writereview?placeid={place_id}"


def extract_place_id(value: str) -> str:
    """Return a Place ID from a raw Place ID or a full Google review URL."""
    if not value or not isinstance(value, str):
        return ""
    value = value.strip()
    marker = "placeid="
    idx = value.lower().find(marker)
    if idx != -1:
        return value[idx + len(marker):].split("&")[0].strip()
    return value


def build_google_review_url(place_id: str) -> str:
    """Build the Google write-a-review URL from a Place ID."""
    pid = extract_place_id(place_id)
    if not pid:
        return ""
    return GOOGLE_WRITE_REVIEW_URL.format(place_id=pid)
