"""Create a tax invoice when a Razorpay payment is confirmed."""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from urllib.parse import quote

from fpdf import FPDF
from sqlalchemy.orm import Session

from config import get_config
from models.domain_models import Booking, BusinessConfigModel, Invoice, Payment
from services.email_service import send_pdf_email, smtp_configured
from services.plan_service import PLANS
from utils.validators import normalize_mobile, validate_email


def _round_money(value) -> float:
    return round(float(value or 0), 2)


def next_invoice_no(db: Session) -> str:
    today = datetime.utcnow().strftime("%Y%m%d")
    prefix = f"RH-{today}-"
    last = (
        db.query(Invoice)
        .filter(Invoice.invoice_no.like(f"{prefix}%"))
        .order_by(Invoice.id.desc())
        .first()
    )
    seq = 1
    if last:
        try:
            seq = int(str(last.invoice_no).rsplit("-", 1)[-1]) + 1
        except ValueError:
            seq = 1
    return f"{prefix}{seq:04d}"


def create_invoice_for_payment(db: Session, payment: Payment, *, commit: bool = False) -> Optional[Invoice]:
    """One invoice per paid payment. Safe to call more than once."""
    if not payment or payment.status != "paid":
        return None
    existing = db.query(Invoice).filter(Invoice.payment_id == payment.id).first()
    if existing:
        return existing

    business = db.query(BusinessConfigModel).filter(BusinessConfigModel.key == payment.business_key).first()
    booking = db.query(Booking).filter(Booking.id == payment.booking_id).first() if payment.booking_id else None
    plan_code = (payment.plan_code or (business.plan_code if business else "") or "").strip()
    plan_label = PLANS.get(plan_code, {}).get("label") or plan_code or "ReviewHub plan"
    amount = _round_money(payment.amount or (booking.amount if booking else 0) or (business.plan_amount if business else 0))
    paid_at = payment.paid_at or datetime.utcnow()
    invoice = Invoice(
        invoice_no=next_invoice_no(db),
        payment_id=payment.id,
        booking_id=payment.booking_id,
        business_key=payment.business_key or (business.key if business else ""),
        bill_to_name=(payment.payer_name or (business.name if business else "") or "").strip(),
        bill_to_email=(payment.payer_email or (business.email if business else "") or "").strip(),
        bill_to_phone=(payment.payer_phone or (business.mobile if business else "") or "").strip(),
        bill_to_address=(business.address if business else "") or "",
        plan_code=plan_code,
        plan_label=plan_label,
        description=f"{plan_label} subscription",
        amount=amount,
        currency=payment.currency or "INR",
        razorpay_payment_id=payment.razorpay_payment_id or "",
        razorpay_order_id=payment.razorpay_order_id or "",
        paid_at=paid_at,
    )
    db.add(invoice)
    db.flush()
    if commit:
        db.commit()
        db.refresh(invoice)
    return invoice


def get_invoice(db: Session, invoice_no: str) -> Optional[Invoice]:
    if not invoice_no:
        return None
    return db.query(Invoice).filter(Invoice.invoice_no == invoice_no.strip()).first()


def latest_invoice_for_business(db: Session, business_key: str) -> Optional[Invoice]:
    if not business_key:
        return None
    return (
        db.query(Invoice)
        .filter(Invoice.business_key == business_key)
        .order_by(Invoice.id.desc())
        .first()
    )


def invoice_nos_for_business_keys(db: Session, keys: list[str]) -> dict[str, str]:
    clean = [k for k in keys if k]
    if not clean:
        return {}
    rows = (
        db.query(Invoice.business_key, Invoice.invoice_no, Invoice.id)
        .filter(Invoice.business_key.in_(clean))
        .order_by(Invoice.id.asc())
        .all()
    )
    return {key: no for key, no, _ in rows}


def invoice_nos_for_booking_ids(db: Session, booking_ids: list[int]) -> dict[int, str]:
    ids = [i for i in booking_ids if i]
    if not ids:
        return {}
    rows = (
        db.query(Invoice.booking_id, Invoice.invoice_no)
        .filter(Invoice.booking_id.in_(ids))
        .order_by(Invoice.id.asc())
        .all()
    )
    return {bid: no for bid, no in rows if bid}


def invoice_view_data(invoice: Invoice) -> dict:
    cfg = get_config()
    paid_at = invoice.paid_at or invoice.created_at
    return {
        "invoice_no": invoice.invoice_no,
        "issued_on": paid_at.strftime("%d %b %Y") if paid_at else "",
        "issued_at": paid_at.strftime("%d %b %Y, %I:%M %p") if paid_at else "",
        "seller_name": cfg.COMPANY_NAME or "TechnoBuzz",
        "seller_id": getattr(cfg, "COMPANY_ID", "") or "",
        "seller_address": getattr(cfg, "COMPANY_ADDRESS", "") or "",
        "seller_gstin": getattr(cfg, "COMPANY_GSTIN", "") or "",
        "seller_phone": getattr(cfg, "COMPANY_PHONE", "") or "",
        "seller_email": getattr(cfg, "COMPANY_EMAIL", "") or "",
        "bill_to_name": invoice.bill_to_name or "—",
        "bill_to_email": invoice.bill_to_email or "",
        "bill_to_phone": invoice.bill_to_phone or "",
        "bill_to_address": invoice.bill_to_address or "",
        "plan_label": invoice.plan_label or invoice.plan_code or "Plan",
        "description": invoice.description or "ReviewHub plan",
        "amount": _round_money(invoice.amount),
        "currency": invoice.currency or "INR",
        "razorpay_payment_id": invoice.razorpay_payment_id or "",
        "razorpay_order_id": invoice.razorpay_order_id or "",
        "business_key": invoice.business_key or "",
        "status": "Paid",
        "public_url": invoice_public_url(invoice.invoice_no),
    }


def invoice_public_url(invoice_no: str) -> str:
    base = (get_config().APP_BASE_URL or "").rstrip("/")
    return f"{base}/invoice/{invoice_no}"


def invoice_pdf_filename(invoice_no: str) -> str:
    safe = "".join(ch for ch in (invoice_no or "invoice") if ch.isalnum() or ch in "-_")
    return f"Invoice-{safe or 'invoice'}.pdf"


def _pdf_text(value: str, max_len: int = 220) -> str:
    text = (value or "").replace("\u20b9", "Rs ").replace("₹", "Rs ")
    return text.encode("latin-1", "replace").decode("latin-1")[:max_len]


def invoice_pdf_bytes(invoice: Invoice) -> bytes:
    data = invoice_view_data(invoice)
    pdf = FPDF(format="A4", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_margins(18, 18, 18)

    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(0, 119, 163)
    pdf.cell(0, 9, _pdf_text(data["seller_name"]), ln=1)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(91, 107, 120)
    for line in (data["seller_id"], data["seller_address"], f"GSTIN {data['seller_gstin']}" if data["seller_gstin"] else "", " · ".join(p for p in (data["seller_phone"], data["seller_email"]) if p)):
        if line:
            pdf.cell(0, 5, _pdf_text(line), ln=1)

    y = pdf.get_y()
    pdf.set_xy(130, 18)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(13, 138, 91)
    pdf.cell(62, 7, "PAID", ln=1, align="R")
    pdf.set_x(130)
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(18, 32, 44)
    pdf.cell(62, 9, "Tax Invoice", ln=1, align="R")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(91, 107, 120)
    pdf.set_x(130)
    pdf.cell(62, 5, _pdf_text(data["invoice_no"]), ln=1, align="R")
    pdf.set_x(130)
    pdf.cell(62, 5, _pdf_text(data["issued_at"]), ln=1, align="R")
    pdf.set_y(max(y, pdf.get_y()) + 4)
    pdf.set_draw_color(0, 180, 216)
    pdf.set_line_width(0.6)
    pdf.line(18, pdf.get_y(), 192, pdf.get_y())
    pdf.ln(8)

    col_y = pdf.get_y()
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(122, 138, 150)
    pdf.cell(90, 5, "BILL TO", ln=0)
    pdf.cell(84, 5, "PAYMENT", ln=1)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(18, 32, 44)
    left_x, right_x = 18, 108
    pdf.set_xy(left_x, pdf.get_y())
    pdf.multi_cell(84, 5, _pdf_text(data["bill_to_name"]))
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(91, 107, 120)
    for line in (data["bill_to_address"], data["bill_to_phone"], data["bill_to_email"]):
        if line:
            pdf.set_x(left_x)
            pdf.multi_cell(84, 5, _pdf_text(line))
    left_bottom = pdf.get_y()

    pdf.set_xy(right_x, col_y + 5)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(18, 32, 44)
    pdf.cell(84, 5, _pdf_text(f"Razorpay · {data['currency']}"), ln=1)
    pdf.set_text_color(91, 107, 120)
    if data["razorpay_payment_id"]:
        pdf.set_x(right_x)
        pdf.cell(84, 5, _pdf_text(f"Payment ID {data['razorpay_payment_id']}"), ln=1)
    if data["razorpay_order_id"]:
        pdf.set_x(right_x)
        pdf.cell(84, 5, _pdf_text(f"Order {data['razorpay_order_id']}"), ln=1)
    pdf.set_y(max(left_bottom, pdf.get_y()) + 10)

    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(91, 107, 120)
    pdf.cell(130, 8, "DESCRIPTION", border="B")
    pdf.cell(44, 8, "AMOUNT", border="B", align="R", ln=1)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(18, 32, 44)
    pdf.cell(130, 8, _pdf_text(data["plan_label"]))
    pdf.cell(44, 8, _pdf_text(f"Rs {data['amount']:,.2f}"), align="R", ln=1)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(91, 107, 120)
    pdf.cell(174, 5, _pdf_text(data["description"]), ln=1)
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(18, 32, 44)
    pdf.cell(130, 9, "Total paid", align="R")
    pdf.cell(44, 9, _pdf_text(f"Rs {data['amount']:,.2f}"), align="R", ln=1)
    pdf.ln(10)
    pdf.set_draw_color(219, 228, 236)
    pdf.line(18, pdf.get_y(), 192, pdf.get_y())
    pdf.ln(6)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(91, 107, 120)
    pdf.multi_cell(
        174,
        5,
        "This invoice is generated after successful Razorpay collection. Amount is inclusive of the plan price. Thank you for your business.",
    )
    return bytes(pdf.output())


def invoice_whatsapp_url(invoice: Invoice, to_phone: str = "") -> str:
    data = invoice_view_data(invoice)
    phone = normalize_mobile(to_phone or invoice.bill_to_phone or "")
    text = (
        f"{data['seller_name']} invoice {data['invoice_no']}\n"
        f"{data['plan_label']} — INR {data['amount']:,.2f}\n"
        f"{data['public_url']}"
    )
    encoded = quote(text)
    if phone:
        return f"https://wa.me/91{phone}?text={encoded}"
    return f"https://wa.me/?text={encoded}"


def invoice_mailto_url(invoice: Invoice, to_email: str = "") -> str:
    data = invoice_view_data(invoice)
    dest = (to_email or invoice.bill_to_email or "").strip()
    subject = quote(f"{data['seller_name']} invoice {data['invoice_no']}")
    body = quote(
        f"Please find invoice {data['invoice_no']} for {data['plan_label']} "
        f"(INR {data['amount']:,.2f}).\n\nView / download: {data['public_url']}\n"
    )
    return f"mailto:{dest}?subject={subject}&body={body}"


def send_invoice(invoice: Invoice, *, channel: str, to: str = "", as_staff: bool = False) -> dict:
    channel = (channel or "").strip().lower()
    if channel not in {"email", "whatsapp"}:
        raise ValueError("Choose email or WhatsApp.")
    if channel == "whatsapp":
        return {"ok": True, "channel": "whatsapp", "url": invoice_whatsapp_url(invoice, to)}

    dest = (to or invoice.bill_to_email or "").strip()
    dest, err = validate_email(dest, required=True)
    if err:
        raise ValueError(err)

    billed = (invoice.bill_to_email or "").strip().lower()
    can_smtp = smtp_configured() and (as_staff or (billed and dest == billed))
    if can_smtp:
        data = invoice_view_data(invoice)
        filename = invoice_pdf_filename(invoice.invoice_no)
        send_pdf_email(
            to_email=dest,
            subject=f"{data['seller_name']} invoice {data['invoice_no']}",
            body=(
                f"Please find attached invoice {data['invoice_no']} for {data['plan_label']} "
                f"(INR {data['amount']:,.2f}).\n\nView online: {data['public_url']}\n"
            ),
            filename=filename,
            pdf_bytes=invoice_pdf_bytes(invoice),
        )
        return {"ok": True, "channel": "email", "sent": True, "to": dest}
    return {"ok": True, "channel": "email", "url": invoice_mailto_url(invoice, dest)}


def backfill_invoices_for_paid_payments(db: Session) -> int:
    paid = db.query(Payment).filter(Payment.status == "paid").order_by(Payment.id.asc()).all()
    have = {row[0] for row in db.query(Invoice.payment_id).all()}
    count = 0
    for payment in paid:
        if payment.id in have:
            continue
        if create_invoice_for_payment(db, payment, commit=False):
            count += 1
    if count:
        db.commit()
    return count
