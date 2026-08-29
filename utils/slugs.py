"""URL slug helpers for auto-generated business feedback pages."""

import re
import unicodedata

RESERVED_SLUGS = frozenset(
    {
        "admin",
        "api",
        "docs",
        "feedback",
        "generate-feedback",
        "login",
        "logout",
        "openapi.json",
        "redoc",
        "static",
        "submit-feedback",
    }
)


def slugify(value: str, max_length: int = 60) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    text = re.sub(r"_+", "_", text)
    return text[:max_length]


def feedback_path_for(business_key: str, route_slug: str) -> str:
    slug = (route_slug or "").strip().strip("/")
    if not slug:
        return "/feedback"
    return f"/feedback/{slug}"
