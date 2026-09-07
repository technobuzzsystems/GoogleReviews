"""Optional SMTP sending for invoice PDFs."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from config import get_config


def smtp_configured() -> bool:
    cfg = get_config()
    return bool((getattr(cfg, "SMTP_HOST", "") or "").strip())


def smtp_from_addr() -> str:
    cfg = get_config()
    return (
        (getattr(cfg, "SMTP_FROM", "") or "").strip()
        or (cfg.COMPANY_EMAIL or "").strip()
        or (getattr(cfg, "SMTP_USER", "") or "").strip()
    )


def send_pdf_email(*, to_email: str, subject: str, body: str, filename: str, pdf_bytes: bytes) -> None:
    cfg = get_config()
    host = (getattr(cfg, "SMTP_HOST", "") or "").strip()
    if not host:
        raise ValueError("Email sending is not configured.")
    from_addr = smtp_from_addr()
    if not from_addr:
        raise ValueError("Set SMTP_FROM or COMPANY_EMAIL to send invoices.")

    msg = EmailMessage()
    msg["Subject"] = subject
    company = cfg.COMPANY_NAME or "TechnoBuzz"
    msg["From"] = f"{company} <{from_addr}>"
    msg["To"] = to_email
    msg.set_content(body)
    msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename=filename)

    port = int(getattr(cfg, "SMTP_PORT", 587) or 587)
    use_tls = bool(getattr(cfg, "SMTP_TLS", True))
    smtp_cls = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
    with smtp_cls(host, port, timeout=20) as smtp:
        if use_tls and port != 465:
            smtp.starttls()
        user = (getattr(cfg, "SMTP_USER", "") or "").strip()
        password = getattr(cfg, "SMTP_PASSWORD", "") or ""
        if user:
            smtp.login(user, password)
        smtp.send_message(msg)
