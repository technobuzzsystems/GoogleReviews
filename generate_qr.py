"""
generate_qr.py
---------------
Standalone utility to generate the TechnoBuzz feedback QR code.

Usage:
    python generate_qr.py                  # uses IP from .env / auto-detect
    python generate_qr.py --ip 192.168.1.48
    python generate_qr.py --url http://yourdomain.com/feedback

The QR code is saved to:
    static/images/qrcode.png

Scan this QR with any phone camera to open the feedback page.
"""

import argparse
import os
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv
from utils.network import build_lan_url

load_dotenv()

# ─── Defaults ─────────────────────────────────────────────────────────────────
DEFAULT_PORT = os.getenv("FLASK_PORT", "5000")
OUTPUT_PATH  = Path(__file__).parent / "static" / "images" / "qrcode.png"
COMPANY_NAME = os.getenv("COMPANY_NAME", "TechnoBuzz")

# ─── Design tokens ────────────────────────────────────────────────────────────
CLR_BG        = "#060D1A"
CLR_QR_FILL   = "#00B4D8"
CLR_QR_BACK   = "#0D1B2A"
CLR_TITLE     = "#00B4D8"
CLR_SUBTITLE  = "#7A9BB5"
CLR_URL       = "#3E6480"


def is_phone_reachable_base_url(url: str) -> bool:
    """Return True when APP_BASE_URL is useful from another device."""
    lowered = url.lower()
    return not any(host in lowered for host in ("localhost", "127.0.0.1", "0.0.0.0"))


def generate_qr(url: str, output_path: Path = OUTPUT_PATH, company_name: str = COMPANY_NAME) -> Path:
    """
    Generate a branded QR code image for the given URL.

    Args:
        url          (str): The feedback page URL to encode.
        output_path  (Path): Where to save the generated PNG.
        company_name (str): The name of the company for the label.

    Returns:
        Path: The path to the saved QR code image.
    """
    print(f"Generating QR code for: {url}")

    # ── Build QR ──────────────────────────────────────────────────────────────
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=12,
        border=3,
    )
    qr.add_data(url)
    qr.make(fit=True)

    qr_img  = qr.make_image(fill_color=CLR_QR_FILL, back_color=CLR_QR_BACK).convert("RGB")
    w, h    = qr_img.size
    pad     = 40
    label_h = 90
    total_w = w + pad * 2
    total_h = h + pad * 2 + label_h

    # ── Canvas ────────────────────────────────────────────────────────────────
    canvas = Image.new("RGB", (total_w, total_h), CLR_BG)
    canvas.paste(qr_img, (pad, pad))

    # ── Typography ────────────────────────────────────────────────────────────
    draw = ImageDraw.Draw(canvas)
    try:
        font_big = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 24)
        font_sm  = ImageFont.truetype("C:/Windows/Fonts/arial.ttf",   14)
    except Exception:
        font_big = ImageFont.load_default()
        font_sm  = font_big

    cy = h + pad * 2
    draw.text((total_w // 2, cy),      company_name,             fill=CLR_TITLE,    font=font_big, anchor="mm")
    draw.text((total_w // 2, cy + 30), "Scan to Share Feedback", fill=CLR_SUBTITLE, font=font_sm,  anchor="mm")
    draw.text((total_w // 2, cy + 55), url,                      fill=CLR_URL,      font=font_sm,  anchor="mm")

    # ── Save ──────────────────────────────────────────────────────────────────
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(str(output_path))
    print(f"[OK] QR code saved to: {output_path}")
    return output_path


from config import BUSINESS_REGISTRY

def main():
    parser = argparse.ArgumentParser(description="Generate Feedback QR Codes")
    parser.add_argument("--ip",  type=str, help="Your local network IP address")
    parser.add_argument("--url", type=str, help="Full feedback URL (overrides --ip)")
    args = parser.parse_args()

    app_base_url = os.getenv("APP_BASE_URL", "").rstrip("/")

    for b_id, b_config in BUSINESS_REGISTRY.items():
        route_slug = b_config.get("route_slug")
        if route_slug == "":
            route = "feedback"
        elif route_slug:
            route = f"feedback/{route_slug}"
        else:
            route = f"feedback/{b_id}"
        
        if args.url:
            url = args.url if b_id == "technobuzz" else f"{args.url.rsplit('/', 1)[0]}/{route}"
        elif app_base_url and is_phone_reachable_base_url(app_base_url):
            url = f"{app_base_url}/{route}"
        else:
            url = f"http://{args.ip}:{DEFAULT_PORT}/{route}" if args.ip else build_lan_url(DEFAULT_PORT, f"/{route}")

        company_name = b_config["name"]
        output_path = Path(__file__).parent / "static" / "images" / f"{b_id}_qrcode.png"

        print(f"Company : {company_name}")
        print(f"URL     : {url}")
        print(f"Output  : {output_path}")
        print()

        generate_qr(url, output_path=output_path, company_name=company_name)

    print()
    print("How to use:")
    print("   1. Make sure Flask server is running:  python app.py")
    print("   2. Connect your phone to the same WiFi network")
    print("   3. Scan the QR code with your phone camera")


if __name__ == "__main__":
    main()
