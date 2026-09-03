"""S3 uploads for business logos. Client pages load them via /media/logo/."""

import io
import logging
import uuid
from pathlib import Path
from urllib.parse import urlparse

from fastapi import UploadFile

from config import get_config
from utils.slugs import slugify

logger = logging.getLogger(__name__)

ALLOWED_LOGO_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
ALLOWED_LOGO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
EXT_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _max_logo_bytes() -> int:
    return max(1, int(get_config().MAX_FILE_SIZE_MB or 10)) * 1024 * 1024


def s3_configured() -> bool:
    cfg = get_config()
    return bool(cfg.S3_BUCKET and cfg.AWS_ACCESS_KEY_ID and cfg.AWS_SECRET_ACCESS_KEY)


def _public_base(cfg) -> str:
    custom = (cfg.S3_PUBLIC_BASE_URL or "").strip().rstrip("/")
    if custom:
        return custom
    region = cfg.AWS_REGION or "ap-south-1"
    return f"https://{cfg.S3_BUCKET}.s3.{region}.amazonaws.com"


def _s3_client(cfg):
    import boto3

    return boto3.client(
        "s3",
        aws_access_key_id=cfg.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=cfg.AWS_SECRET_ACCESS_KEY,
        region_name=cfg.AWS_REGION or "ap-south-1",
    )


def _logo_prefix(cfg) -> str:
    return (cfg.S3_LOGO_PREFIX or "logos").strip("/")


def _s3_key_from_stored(value: str) -> str:
    """Return the S3 object key if this stored value points at our bucket."""
    value = (value or "").strip()
    if not value:
        return ""
    cfg = get_config()
    prefix = _logo_prefix(cfg)
    if value.startswith(prefix + "/") and ".." not in value:
        return value.lstrip("/")
    if not (value.startswith("http://") or value.startswith("https://")):
        return ""
    parsed = urlparse(value)
    host = (parsed.netloc or "").lower()
    bucket = (cfg.S3_BUCKET or "").lower()
    region = (cfg.AWS_REGION or "ap-south-1").lower()
    custom = urlparse(_public_base(cfg) + "/")
    our_hosts = {
        f"{bucket}.s3.{region}.amazonaws.com",
        f"{bucket}.s3.amazonaws.com",
        f"s3.{region}.amazonaws.com",
        f"s3.amazonaws.com",
        (custom.netloc or "").lower(),
    }
    if host not in our_hosts:
        return ""
    key = parsed.path.lstrip("/")
    if host in {f"s3.{region}.amazonaws.com", "s3.amazonaws.com"}:
        parts = key.split("/", 1)
        if len(parts) == 2 and parts[0].lower() == bucket:
            key = parts[1]
        else:
            return ""
    if key.startswith(prefix + "/") and ".." not in key:
        return key
    return ""


def public_logo_url(logo_filename: str) -> str:
    value = (logo_filename or "").strip()
    if not value:
        return ""
    s3_key = _s3_key_from_stored(value)
    if s3_key:
        return "/media/logo/" + s3_key
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return "/static/" + value.lstrip("/")


def _detect_extension(filename: str, content_type: str) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext == ".jpeg":
        ext = ".jpg"
    if ext in ALLOWED_LOGO_EXTS:
        return ".jpg" if ext == ".jpeg" else ext
    mapped = ALLOWED_LOGO_TYPES.get((content_type or "").lower())
    if mapped:
        return mapped
    return ""


def _validate_image_bytes(data: bytes, content_type: str) -> None:
    from PIL import Image

    try:
        buffer = io.BytesIO(data)
        image = Image.open(buffer)
        image.verify()
        buffer.seek(0)
        image = Image.open(buffer)
        fmt = (image.format or "").upper()
    except Exception as exc:
        raise ValueError("Upload a valid PNG, JPG, WEBP, or GIF logo.") from exc
    if fmt not in {"JPEG", "PNG", "WEBP", "GIF"}:
        raise ValueError("Upload a valid PNG, JPG, WEBP, or GIF logo.")
    if content_type and content_type.lower() not in ALLOWED_LOGO_TYPES:
        raise ValueError("Logo must be a PNG, JPG, WEBP, or GIF file.")


def save_logo_upload(upload: UploadFile, business_key: str) -> str:
    """Upload a logo to S3 and return the object key (served via /media/logo/)."""
    if not upload or not (upload.filename or "").strip():
        return ""
    if not s3_configured():
        raise ValueError(
            "S3 is not configured. Set AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, "
            "AWS_REGION, and S3_BUCKET in .env."
        )

    ext = _detect_extension(upload.filename, upload.content_type or "")
    if not ext:
        raise ValueError("Logo must be a PNG, JPG, WEBP, or GIF file.")

    data = upload.file.read()
    if not data:
        raise ValueError("The selected logo file is empty.")
    max_bytes = _max_logo_bytes()
    if len(data) > max_bytes:
        raise ValueError(f"Logo must be {get_config().MAX_FILE_SIZE_MB} MB or smaller.")
    _validate_image_bytes(data, upload.content_type or "")

    cfg = get_config()
    prefix = _logo_prefix(cfg)
    folder = slugify(business_key) or "business"
    key = f"{prefix}/{folder}/{uuid.uuid4().hex}{ext}"
    extra = {
        "ContentType": (upload.content_type or "").lower()
        if (upload.content_type or "").lower() in ALLOWED_LOGO_TYPES
        else EXT_CONTENT_TYPES.get(ext, "image/jpeg"),
        "CacheControl": "public, max-age=31536000",
    }
    acl = (cfg.S3_OBJECT_ACL or "").strip()
    if acl:
        extra["ACL"] = acl

    try:
        _s3_client(cfg).put_object(Bucket=cfg.S3_BUCKET, Key=key, Body=data, **extra)
    except Exception as exc:
        logger.exception("S3 logo upload failed")
        raise ValueError("Could not upload the logo to S3. Check bucket credentials and permissions.") from exc

    logger.info("Uploaded logo to s3://%s/%s", cfg.S3_BUCKET, key)
    return key


def get_logo_object(key: str) -> tuple[bytes, str]:
    """Fetch a logo object from S3. Raises FileNotFoundError if missing/invalid."""
    cfg = get_config()
    prefix = _logo_prefix(cfg)
    key = (key or "").lstrip("/")
    if not key.startswith(prefix + "/") or ".." in key:
        raise FileNotFoundError("Invalid logo key.")
    if not s3_configured():
        raise FileNotFoundError("S3 is not configured.")
    try:
        obj = _s3_client(cfg).get_object(Bucket=cfg.S3_BUCKET, Key=key)
        body = obj["Body"].read()
        content_type = (obj.get("ContentType") or "").split(";")[0].strip()
        if content_type not in ALLOWED_LOGO_TYPES:
            ext = Path(key).suffix.lower()
            content_type = EXT_CONTENT_TYPES.get(ext, "image/jpeg")
        return body, content_type
    except FileNotFoundError:
        raise
    except Exception as exc:
        logger.warning("S3 logo fetch failed for %s: %s", key, exc)
        raise FileNotFoundError("Logo not found.") from exc
