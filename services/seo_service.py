"""
services/seo_service.py
-----------------------
Centralized SEO engine for the TechnoBuzz Google Reviews platform.
Provides:
  - Dynamic per-business metadata (Title, Description, Canonical, Robots)
  - Schema.org (JSON-LD) LocalBusiness markup
  - Open Graph & Twitter Cards for social sharing (WhatsApp/Facebook)
  - Dynamic sitemap.xml generator for all active businesses
  - Dynamic robots.txt generator
"""

import json
import logging
from datetime import datetime
from typing import Dict, Any, List
from sqlalchemy.orm import Session

from config import get_config
from models.domain_models import BusinessConfigModel

logger = logging.getLogger(__name__)

CATEGORY_SCHEMA_MAP = {
    "AutoRepair": "AutoRepair",
    "Automobile / Battery": "AutoRepair",
    "Restaurant": "Restaurant",
    "Restaurant / Cafe": "Restaurant",
    "CafeOrCoffeeShop": "CafeOrCoffeeShop",
    "Store": "Store",
    "Retail / Store": "Store",
    "MedicalBusiness": "MedicalBusiness",
    "Doctor / Clinic": "MedicalBusiness",
    "ProfessionalService": "ProfessionalService",
    "Professional Services": "ProfessionalService",
    "BeautySalon": "BeautySalon",
    "Salon / Spa": "BeautySalon",
    "LocalBusiness": "LocalBusiness",
}


def resolve_base_url(request=None) -> str:
    """Return the canonical base URL without trailing slash."""
    config = get_config()
    configured = (getattr(config, "APP_BASE_URL", "") or "").strip().rstrip("/")
    if configured and not configured.startswith("http://localhost"):
        return configured
    if request:
        proto = request.headers.get("x-forwarded-proto") or request.url.scheme
        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
        if host:
            return f"{proto}://{host}".rstrip("/")
    return configured or "https://reviewhub.technobuzzsystems.com"


def build_business_seo(b_config: Dict[str, Any], request=None) -> Dict[str, Any]:
    """
    Build complete, production-ready SEO metadata for an individual business page.
    Generates dynamic fallbacks if custom fields are not explicitly set by the owner.
    """
    base_url = resolve_base_url(request)
    name = (b_config.get("name") or "Business").strip()
    area = (b_config.get("area") or "").strip()
    address = (b_config.get("address") or "").strip()
    mobile = (b_config.get("mobile") or "").strip()
    scope = (b_config.get("scope") or "").strip()
    feedback_path = (b_config.get("feedback_path") or "/feedback").strip()

    # 1. Canonical URL
    canonical_url = f"{base_url}{feedback_path}"

    # 2. Page Title
    custom_title = (b_config.get("seo_title") or "").strip()
    if custom_title:
        title = custom_title
    elif area:
        title = f"{name} | Customer Reviews & Feedback in {area}"
    else:
        title = f"{name} | Official Customer Reviews & Ratings"

    # 3. Meta Description
    custom_desc = (b_config.get("seo_description") or "").strip()
    if custom_desc:
        description = custom_desc
    else:
        location_part = f" in {area}" if area else ""
        description = (
            f"Rate your experience and share feedback for {name}{location_part}. "
            f"Read verified customer reviews, find contact details, and submit reviews directly."
        )

    # 4. Meta Robots
    is_active = bool(b_config.get("is_active", True) if b_config.get("is_active") is not None else True)
    is_expired = bool(b_config.get("is_expired") or b_config.get("plan_status") == "expired")
    if not is_active or is_expired:
        meta_robots = "noindex, nofollow"
    else:
        meta_robots = "index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1"

    # 5. Open Graph Image
    logo_url = (b_config.get("logo_url") or "").strip()
    if logo_url:
        if logo_url.startswith("http://") or logo_url.startswith("https://"):
            og_image = logo_url
        else:
            og_image = f"{base_url}{logo_url if logo_url.startswith('/') else '/' + logo_url}"
    else:
        og_image = ""

    # 6. Category & Schema Type
    raw_cat = (b_config.get("business_category") or "").strip()
    schema_type = CATEGORY_SCHEMA_MAP.get(raw_cat, "LocalBusiness")

    # 7. About Text / Crawlable Body Content
    custom_about = (b_config.get("about_text") or "").strip()
    about_text = custom_about if custom_about else scope

    # 8. Opening Hours
    opening_hours = (b_config.get("opening_hours") or "").strip() or "Monday to Saturday: 9:00 AM – 9:00 PM"

    # 9. Schema.org JSON-LD Dictionary
    schema_dict = {
        "@context": "https://schema.org",
        "@type": schema_type,
        "name": name,
        "url": canonical_url,
    }
    if og_image:
        schema_dict["image"] = og_image
        schema_dict["logo"] = og_image
    if mobile:
        schema_dict["telephone"] = mobile
    if address or area:
        schema_dict["address"] = {
            "@type": "PostalAddress",
            "streetAddress": address if address else area,
            "addressLocality": area if area else "",
            "addressCountry": "IN",
        }
    if opening_hours:
        schema_dict["openingHours"] = opening_hours

    # Social and External Links
    same_as: List[str] = []
    if b_config.get("instagram_url") and b_config.get("instagram_enabled"):
        same_as.append(b_config["instagram_url"])
    if b_config.get("facebook_url") and b_config.get("facebook_enabled"):
        same_as.append(b_config["facebook_url"])
    if b_config.get("google_review_url"):
        same_as.append(b_config["google_review_url"])
    if same_as:
        schema_dict["sameAs"] = same_as

    if about_text:
        schema_dict["description"] = about_text[:300]

    schema_json_ld = json.dumps(schema_dict, indent=2, ensure_ascii=False)

    return {
        "title": title,
        "description": description,
        "canonical_url": canonical_url,
        "meta_robots": meta_robots,
        "og_image": og_image,
        "business_category": raw_cat or "Local Business",
        "schema_type": schema_type,
        "about_text": about_text,
        "opening_hours": opening_hours,
        "schema_json_ld": schema_json_ld,
        "area": area,
        "address": address,
        "mobile": mobile,
        "base_url": base_url,
    }


def generate_robots_txt(request=None) -> str:
    """Generate dynamic robots.txt content."""
    base_url = resolve_base_url(request)
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Allow: /feedback/\n"
        "Disallow: /admin\n"
        "Disallow: /admin/\n"
        "Disallow: /owner\n"
        "Disallow: /owner/\n"
        "Disallow: /api/\n"
        "Disallow: /generate-feedback\n"
        "Disallow: /submit-feedback\n\n"
        f"Sitemap: {base_url}/sitemap.xml\n"
    )


def generate_sitemap_xml(db: Session, request=None) -> str:
    """Generate dynamic XML sitemap of all active, unexpired business pages."""
    from datetime import date
    from services.business_service import feedback_path_for

    base_url = resolve_base_url(request)
    today_iso = date.today().isoformat()

    rows = (
        db.query(BusinessConfigModel)
        .filter(BusinessConfigModel.is_active == True)
        .order_by(BusinessConfigModel.name.asc())
        .all()
    )

    xml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        '  <url>',
        f'    <loc>{base_url}/</loc>',
        f'    <lastmod>{today_iso}</lastmod>',
        '    <changefreq>daily</changefreq>',
        '    <priority>1.0</priority>',
        '  </url>',
    ]

    for b in rows:
        # Skip expired businesses
        if b.expiry_date and b.expiry_date < date.today():
            continue
        path = feedback_path_for(b.key, b.route_slug or "")
        loc = f"{base_url}{path}"
        xml_lines.extend([
            '  <url>',
            f'    <loc>{loc}</loc>',
            f'    <lastmod>{today_iso}</lastmod>',
            '    <changefreq>weekly</changefreq>',
            '    <priority>0.8</priority>',
            '  </url>',
        ])

    xml_lines.append('</urlset>')
    return "\n".join(xml_lines)
