"""QR codes for a business feedback page URL."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import qrcode
from fastapi import Request
from PIL import Image, ImageDraw, ImageFont

from config import get_config

CLR_BG = "#07111C"
CLR_PANEL = "#0B1824"
CLR_QR_FILL = "#E8F4FD"
CLR_QR_BACK = "#0B1824"
CLR_ACCENT = "#00B4D8"
CLR_TITLE = "#F4FBFF"
CLR_MUTED = "#8FB0C6"
CLR_LINE = "#1A3A4E"

_FONT_BOLD = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)
_FONT_REG = (
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)

PUBLIC_SITE_URL = "https://reviewhub.technobuzzsystems.com"
_ROOT = Path(__file__).resolve().parents[1]
_STATIC = _ROOT / "static"
_TB_LOGO = _STATIC / "images" / "technobuzz_logo.jpg"


def is_phone_reachable_base_url(url: str) -> bool:
    lowered = (url or "").lower()
    return not any(host in lowered for host in ("localhost", "127.0.0.1", "0.0.0.0"))


def public_feedback_url(request: Request, feedback_path: str) -> str:
    """Absolute URL encoded in the QR — live ReviewHub host, same path as Open page."""
    path = feedback_path if (feedback_path or "").startswith("/") else f"/{feedback_path or 'feedback'}"
    cfg = get_config()
    configured = (cfg.APP_BASE_URL or "").rstrip("/")
    if configured and is_phone_reachable_base_url(configured):
        base = configured
    else:
        base = PUBLIC_SITE_URL
    return f"{base.rstrip('/')}{path}"


def _load_font(candidates: tuple[str, ...], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in candidates:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _hex_rgb(value: str) -> tuple[int, int, int]:
    raw = value.lstrip("#")
    return tuple(int(raw[i : i + 2], 16) for i in (0, 2, 4))


def _logo_file(stored: str) -> Path | None:
    value = (stored or "").strip()
    if not value or value.startswith("http://") or value.startswith("https://"):
        return None
    rel = value.lstrip("/").replace("\\", "/")
    if rel.startswith("static/"):
        rel = rel[7:]
    if ".." in rel:
        return None
    path = _STATIC / rel
    return path if path.is_file() else None


def _open_client_logo(stored: str) -> Image.Image | None:
    """Load the business's own logo from static files or S3. Never falls back to TechnoBuzz."""
    value = (stored or "").strip()
    if not value:
        return None
    path = _logo_file(value)
    if path:
        try:
            return Image.open(path).convert("RGBA")
        except OSError:
            return None
    from services.storage_service import _s3_key_from_stored, get_logo_object

    key = _s3_key_from_stored(value)
    if not key:
        rel = value.lstrip("/")
        if rel.startswith("logos/") and ".." not in rel:
            key = rel
    if not key:
        return None
    try:
        data, _content_type = get_logo_object(key)
        return Image.open(BytesIO(data)).convert("RGBA")
    except Exception:
        return None


def _open_logo(stored: str = "", *, fallback_tb: bool = False) -> Image.Image | None:
    path = _logo_file(stored)
    if path is None and fallback_tb and _TB_LOGO.is_file():
        path = _TB_LOGO
    if path is None:
        return None
    try:
        return Image.open(path).convert("RGBA")
    except OSError:
        return None


def _circle_logo(src: Image.Image, size: int) -> Image.Image:
    src = src.convert("RGBA")
    src = src.resize((size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((1, 1, size - 2, size - 2), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(src, (0, 0), mask)
    return out


def _badge(src: Image.Image, size: int, ring: str = CLR_ACCENT) -> Image.Image:
    inner = max(8, size - 16)
    logo = _circle_logo(src, inner)
    badge = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(badge)
    draw.ellipse((1, 1, size - 2, size - 2), fill=(255, 255, 255, 255), outline=_hex_rgb(ring) + (255,), width=5)
    inset = (size - inner) // 2
    badge.paste(logo, (inset, inset), logo)
    return badge


def qr_png_bytes(url: str, company_name: str = "", logo_filename: str = "") -> bytes:
    business_logo = _open_client_logo(logo_filename)
    tb_logo = _open_logo("", fallback_tb=True)

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=11,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color=CLR_QR_FILL, back_color=CLR_QR_BACK).convert("RGBA")
    qw, qh = qr_img.size
    if business_logo is not None:
        badge_size = max(88, int(min(qw, qh) * 0.28))
        badge = _badge(business_logo, badge_size)
        qr_img.paste(badge, ((qw - badge_size) // 2, (qh - badge_size) // 2), badge)

    pad = 36
    header_h = 108
    footer_h = 78
    width = qw + pad * 2
    height = header_h + qh + footer_h + pad
    canvas = Image.new("RGB", (width, height), CLR_BG)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((10, 10, width - 11, height - 11), radius=28, fill=CLR_PANEL, outline=_hex_rgb(CLR_LINE), width=2)

    font_name = _load_font(_FONT_BOLD, 28)
    font_sub = _load_font(_FONT_REG, 15)
    font_tiny = _load_font(_FONT_REG, 13)
    font_power = _load_font(_FONT_BOLD, 14)

    header_y = 34
    logo_size = 64
    title = (company_name or "ReviewHub").strip()[:42]
    if business_logo is not None:
        header_logo = _badge(business_logo, logo_size)
        canvas.paste(header_logo, (pad, header_y), header_logo)
        text_x = pad + logo_size + 16
    else:
        text_x = pad
    draw.text((text_x, header_y + 10), title, fill=CLR_TITLE, font=font_name)
    draw.text((text_x, header_y + 46), "Scan to share your Google review", fill=CLR_MUTED, font=font_sub)

    qr_y = header_h
    canvas.paste(qr_img.convert("RGB"), (pad, qr_y))

    footer_top = qr_y + qh + 14
    draw.line((pad, footer_top, width - pad, footer_top), fill=_hex_rgb(CLR_LINE), width=1)
    power_y = footer_top + 16
    mark_size = 36
    label = "Powered by TechnoBuzz"
    if tb_logo is not None:
        mark = _badge(tb_logo, mark_size, ring="#7A9BB5")
        text_w = draw.textlength(label, font=font_power)
        block_w = mark_size + 10 + int(text_w)
        start_x = max(pad, (width - block_w) // 2)
        canvas.paste(mark, (start_x, power_y), mark)
        draw.text((start_x + mark_size + 10, power_y + 9), label, fill=CLR_ACCENT, font=font_power)
    else:
        draw.text((width // 2, power_y + 16), label, fill=CLR_ACCENT, font=font_power, anchor="mm")
    draw.text((width // 2, power_y + 44), "reviewhub.technobuzzsystems.com", fill=CLR_MUTED, font=font_tiny, anchor="mm")

    buf = BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()
