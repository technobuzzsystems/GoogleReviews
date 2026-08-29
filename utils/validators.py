"""
utils/validators.py
--------------------
Server-side input validation utilities.

All validation logic lives here — never inside route functions —
so that routes stay thin and validation is easily testable.
"""

import re


def sanitize_string(value: str, max_length: int = 500) -> str:
    """Strip whitespace and truncate a string to a safe maximum length."""
    if not isinstance(value, str):
        return ""
    return value.strip()[:max_length]


def normalize_mobile(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    return digits


def validate_mobile(value: str, *, required: bool = False, label: str = "Mobile number") -> tuple[str, str]:
    raw = (value or "").strip()
    if not raw:
        return ("", f"{label} is required.") if required else ("", "")
    digits = normalize_mobile(raw)
    if not re.fullmatch(r"[6-9]\d{9}", digits):
        return "", f"Enter a valid 10-digit Indian {label.lower()}."
    return digits, ""


def validate_aadhaar(value: str, *, required: bool = False) -> tuple[str, str]:
    raw = (value or "").strip()
    if not raw:
        return ("", "Aadhaar number is required.") if required else ("", "")
    digits = re.sub(r"\D", "", raw)
    if not re.fullmatch(r"[2-9]\d{11}", digits):
        return "", "Enter a valid 12-digit Aadhaar number."
    return digits, ""


def validate_email(value: str, *, required: bool = False) -> tuple[str, str]:
    raw = sanitize_string(value, 200).lower()
    if not raw:
        return ("", "Email is required.") if required else ("", "")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", raw):
        return "", "Enter a valid email address."
    return raw, ""


def validate_ifsc(value: str) -> tuple[str, str]:
    raw = sanitize_string(value, 11).upper()
    if not raw:
        return "", "IFSC code is required."
    if not re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", raw):
        return "", "Enter a valid 11-character IFSC code."
    return raw, ""


def validate_account_number(value: str) -> tuple[str, str]:
    digits = re.sub(r"\D", "", value or "")
    if not re.fullmatch(r"\d{9,18}", digits):
        return "", "Enter a valid bank account number (9–18 digits)."
    return digits, ""


def validate_withdraw_amount(value: str, balance: float) -> tuple[float, str]:
    raw = (value or "").strip().replace(",", "")
    if not raw:
        return 0.0, "Enter the amount to withdraw."
    try:
        amount = round(float(raw), 2)
    except ValueError:
        return 0.0, "Enter a valid amount."
    if amount <= 0:
        return 0.0, "Enter an amount greater than zero."
    if amount > round(float(balance or 0), 2):
        return 0.0, "Amount is more than the available wallet balance."
    return amount, ""
