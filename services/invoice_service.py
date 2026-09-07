"""Create a tax invoice when a Razorpay payment is confirmed."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from config import get_config
from models.domain_models import Booking, BusinessConfigModel, Invoice, Payment
from services.plan_service import PLANS


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
    }


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
