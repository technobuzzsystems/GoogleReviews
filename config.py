"""
config.py
---------
Central configuration module for the TechnoBuzz Feedback System.
All sensitive values are loaded from environment variables via .env file.
Never hardcode secrets here.
"""

import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

BUSINESS_REGISTRY = {
    "technobuzz": {
        "name": "TechnoBuzz",
        "id": "TECHNOBUZZ-001",
        "route_slug": "",
        "collection": "feedback",
        "google_review_url": os.getenv("GOOGLE_REVIEW_URL", ""),
        "logo_filename": "images/technobuzz_logo.jpg",
        "scope": "Software development, web design, cloud infrastructure, network architecture, cybersecurity, and managed IT support.",
        "examples_1star": "buggy deployments or broken code, critical downtime due to poor infrastructure planning, missed project deadlines, lack of communication from the support team, security vulnerabilities left unpatched, unprofessional conduct or incompetence",
        "examples_2star": "slow response to support tickets, complex UI/UX in web design",
        "examples_3star": "technical team is skilled but the project management is disorganized, billing confusion or slow response",
        "examples_4star": "better documentation, more frequent status updates",
        "examples_5star": "seamless cloud migration, reliable network uptime, intuitive design interface, proactive security measures, knowledgeable engineering team, project delivered ahead of schedule"
    },
    "boardwale": {
        "name": "Boardwale",
        "id": "BOARDWALE-001",
        "route_slug": "board_001",
        "collection": "boardwale_feedback",
        "google_review_url": os.getenv("BOARDWALE_GOOGLE_REVIEW_URL", "https://share.google/V6qgD9X4o48zQ3XZf"),
        "logo_filename": "images/boardwale_logo.png",
        "scope": "3D signage, indoor boards, outdoor boards, custom business signage solutions, printing, finishing, and installation.",
        "examples_1star": "poor material quality, delayed installation, spelling errors on prints, peeling signs, unresponsive customer service",
        "examples_2star": "incorrect colors used, rough finishing on edges, slow communication",
        "examples_3star": "design is good but installation was messy, decent boards but pricing was confusing",
        "examples_4star": "better communication during the design phase, faster installation",
        "examples_5star": "excellent 3D signage quality, professional finishing, on-time installation, great custom design, high-quality materials, team understood requirements perfectly"
    },
    "jawa_showroom": {
        "name": "Jawa Showroom",
        "id": "JAWA-SHOWROOM-001",
        "route_slug": "showroom_001",
        "collection": "jawa_showroom_feedback",
        "google_review_url": os.getenv("JAWA_GOOGLE_REVIEW_URL", ""),
        "logo_filename": "images/jawa_logo.png",
        "scope": "Jawa motorcycle showroom and dealership experience including showroom experience, motorcycle consultation, sales support, test rides, purchase assistance, finance/EMI assistance, booking, delivery and customer service.",
        "examples_1star": "rude sales executives, terrible customer service, bikes unavailable for test ride, extremely delayed delivery, finance team was unhelpful, zero product knowledge",
        "examples_2star": "showroom was too crowded and disorganized, sales staff ignored us for a long time, booking process was confusing, test ride was rushed",
        "examples_3star": "average experience, staff was polite but didn't know much about the bike specs, delivery took longer than promised but the bike is good",
        "examples_4star": "great test ride experience, helpful staff during the finance process, good overall showroom ambiance, just a slight delay in paperwork",
        "examples_5star": "excellent dealership experience, very knowledgeable and polite sales executives, smooth EMI and exchange process, fantastic test ride arrangements, memorable delivery ceremony"
    },
    "rutuja_battery": {
        "name": "Rutuja Battery",
        "id": "RUTUJA-BATTERY-001",
        "route_slug": "1",
        "collection": "rutuja_battery_feedback",
        "google_review_url": os.getenv("RUTUJA_GOOGLE_REVIEW_URL", ""),
        "logo_filename": "images/rutuja_logo.png",
        "scope": "Battery retail, inverter battery and automotive battery sales and service business in Dhayari and Manaji Nagar, Pune. Authorized Exide dealer offering two-wheeler, car, and commercial vehicle batteries, inverter/UPS batteries, installation, replacement, testing, jump-start assistance, and warranty support.",
        "examples_1star": "poor service, delayed installation, overpriced, sold a faulty battery, communication issues, unhelpful staff, ignored warranty claims",
        "examples_2star": "mixed experience, battery is fine but installation was delayed, average product, communication could be better, wait time was high",
        "examples_3star": "average experience, acceptable service but improvements needed, decent pricing but staff lacked deep knowledge",
        "examples_4star": "good service, genuine Exide battery, helpful staff, quick installation, reasonable and transparent pricing",
        "examples_5star": "excellent service, genuine product, lightning-fast installation, very knowledgeable and polite staff, transparent pricing, excellent after-sales support and warranty assistance, trusted local dealer"
    }
}


class Config:
    """Base configuration class shared across all environments."""

    # ─── Flask ────────────────────────────────────────────────────────────────
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-in-production")
    DEBUG: bool     = os.getenv("FLASK_DEBUG", "False").lower() == "true"
    HOST: str       = os.getenv("FLASK_HOST", "0.0.0.0")
    PORT: int       = int(os.getenv("FLASK_PORT", 5000))

    # ─── MongoDB ──────────────────────────────────────────────────────────────
    MONGO_URI: str     = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    MONGO_DB_NAME: str = os.getenv("MONGO_DB_NAME", "technobuzz_feedback")

    # ─── Google Gemini ────────────────────────────────────────────────────────
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str   = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    # ─── Company (single-company setup — extend for multi-company later) ──────
    COMPANY_NAME: str = os.getenv("COMPANY_NAME", "TechnoBuzz")
    COMPANY_ID: str   = os.getenv("COMPANY_ID", "TECHNOBUZZ-001")

    # ─── Application ──────────────────────────────────────────────────────────
    # Base URL used for QR code generation
    APP_BASE_URL: str = os.getenv("APP_BASE_URL", "http://localhost:5000")

    # ─── Google Business Profile ───────────────────────────────────────────────
    # URL where customers can post their review on Google.
    # Set this to the Google Review link for your business.
    # Leave blank to disable the "Post on Google" button on the success page.
    GOOGLE_REVIEW_URL: str = os.getenv("GOOGLE_REVIEW_URL", "")


class DevelopmentConfig(Config):
    """Development-specific configuration."""
    DEBUG = True


class ProductionConfig(Config):
    """Production-specific configuration."""
    DEBUG = False


# Map environment name → config class
config_map = {
    "development": DevelopmentConfig,
    "production":  ProductionConfig,
}


def get_config() -> Config:
    """Return the appropriate config object based on FLASK_ENV."""
    env = os.getenv("FLASK_ENV", "development")
    return config_map.get(env, DevelopmentConfig)()
