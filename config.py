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


class Config:
    """Base configuration class shared across all environments."""

    # ─── App ──────────────────────────────────────────────────────────────────
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-in-production")
    DEBUG: bool     = os.getenv("FLASK_DEBUG", "False").lower() == "true"
    HOST: str       = os.getenv("FLASK_HOST", "0.0.0.0")
    PORT: int       = int(os.getenv("FLASK_PORT", 5001))

    # ─── PostgreSQL ───────────────────────────────────────────────────────────
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql://postgres:root@localhost:5432/googlereviews",
    )


    # ─── Google Gemini ────────────────────────────────────────────────────────
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str   = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    # ─── Company (single-company setup — extend for multi-company later) ──────
    COMPANY_NAME: str = os.getenv("COMPANY_NAME", "TechnoBuzz")
    COMPANY_ID: str   = os.getenv("COMPANY_ID", "TECHNOBUZZ-001")

    # ─── Application ──────────────────────────────────────────────────────────
    # Base URL used for QR code generation
    APP_BASE_URL: str = os.getenv("APP_BASE_URL", "http://localhost:5001")

    # ─── Google Business Profile ───────────────────────────────────────────────
    # URL where customers can post their review on Google.
    # Set this to the Google Review link for your business.
    # Leave blank to disable the "Post on Google" button on the success page.
    GOOGLE_REVIEW_URL: str = os.getenv("GOOGLE_REVIEW_URL", "")

    # ─── Amazon S3 (business logos) ──────────────────────────────────────────
    AWS_ACCESS_KEY_ID: str = os.getenv("AWS_ACCESS_KEY_ID", "")
    AWS_SECRET_ACCESS_KEY: str = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    AWS_REGION: str = os.getenv("AWS_REGION", "ap-south-1")
    S3_BUCKET: str = os.getenv("S3_BUCKET", "")
    S3_PUBLIC_BASE_URL: str = os.getenv("S3_PUBLIC_BASE_URL", "")
    S3_LOGO_PREFIX: str = os.getenv("S3_LOGO_PREFIX", "logos")
    S3_OBJECT_ACL: str = os.getenv("S3_OBJECT_ACL", "")


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
