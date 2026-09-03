"""Razorpay orders, signature verification, and marking sales-book collections."""

from __future__ import annotations

import hmac
import json
import logging
import urllib.error
import urllib.request
from base64 import b64encode
from datetime import datetime
from hashlib import sha256
from typing import Optional

from sqlalchemy.orm import Session

from config import get_config
from models.domain_models import Booking, BusinessConfigModel, Payment
from services.plan_service import CLIENT_PLAN_NOTE, credit_plan_to_wallet
from services.sales_service import round_money, upsert_plan_booking

logger = logging.getLogger(__name__)

RAZORPAY_ORDERS_URL = "https://api.razorpay.com/v1/orders"


def razorpay_configured() -> bool:
    cfg = get_config()
    return bool(cfg.RAZORPAY_KEY_ID and cfg.RAZORPAY_KEY_SECRET)


def _auth_headers() -> dict:
    cfg = get_config()
    token = b64encode(f"{cfg.RAZORPAY_KEY_ID}:{cfg.RAZORPAY_KEY_SECRET}".encode()).decode()
    return {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
    }


def _create_razorpay_order(amount_paise: int, receipt: str, notes: dict) -> dict:
    payload = json.dumps(
        {
            "amount": amount_paise,
            "currency": "INR",
            "receipt": receipt[:40],
            "payment_capture": 1,
            "notes": notes,
        }
    ).encode()
    req = urllib.request.Request(
        RAZORPAY_ORDERS_URL,
        data=payload,
        headers=_auth_headers(),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="ignore")[:400]
        logger.error("Razorpay order failed: %s %s", exc.code, detail)
        raise ValueError("Could not create Razorpay order. Check RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET.") from exc
    except urllib.error.URLError as exc:
        logger.error("Razorpay order network error: %s", exc)
        raise ValueError("Could not reach Razorpay. Check your internet connection.") from exc


def verify_checkout_signature(order_id: str, payment_id: str, signature: str) -> bool:
    cfg = get_config()
    if not (order_id and payment_id and signature and cfg.RAZORPAY_KEY_SECRET):
        return False
    expected = hmac.new(
        cfg.RAZORPAY_KEY_SECRET.encode(),
        f"{order_id}|{payment_id}".encode(),
        sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_webhook_signature(body: bytes, signature: str) -> bool:
    cfg = get_config()
    secret = cfg.RAZORPAY_WEBHOOK_SECRET or cfg.RAZORPAY_KEY_SECRET
    if not (body and signature and secret):
        return False
    expected = hmac.new(secret.encode(), body, sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def unpaid_booking(db: Session, *, booking_id: int = 0, business_key: str = "") -> Optional[Booking]:
    q = db.query(Booking).filter(Booking.status != "cancelled", Booking.amount > Booking.collected_amount)
    if booking_id:
        return q.filter(Booking.id == booking_id).first()
    if business_key:
        return (
            q.filter(Booking.business_key == business_key)
            .order_by(Booking.id.desc())
            .first()
        )
    return None


def due_amount(booking: Booking) -> float:
    return round_money(max((booking.amount or 0) - (booking.collected_amount or 0), 0))


def checkout_payload(payment: Payment, business: BusinessConfigModel, due: float) -> dict:
    cfg = get_config()
    return {
        "key_id": cfg.RAZORPAY_KEY_ID,
        "order_id": payment.razorpay_order_id,
        "amount_paise": int(round(due * 100)),
        "currency": "INR",
        "name": cfg.COMPANY_NAME or "TechnoBuzz",
        "description": f"{business.plan_code or 'Plan'} · {business.name}",
        "prefill": {
            "name": business.name or "",
            "email": business.email or "",
            "contact": business.mobile or "",
        },
        "notes": {
            "business_key": business.key,
            "booking_id": str(payment.booking_id or ""),
        },
        "theme": {"color": "#00B4D8"},
    }


def create_checkout_order(
    db: Session,
    *,
    business_key: str = "",
    booking_id: int = 0,
) -> dict:
    if not razorpay_configured():
        raise ValueError(
            "Razorpay is not configured. Add RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET to .env, then restart."
        )

    booking = unpaid_booking(db, booking_id=booking_id, business_key=business_key)
    business = None
    if booking:
        business = db.query(BusinessConfigModel).filter(BusinessConfigModel.key == booking.business_key).first()
    elif business_key:
        business = db.query(BusinessConfigModel).filter(BusinessConfigModel.key == business_key).first()
        if business and business.plan_amount and business.sales_executive_id:
            booking = upsert_plan_booking(db, business, commit=True)
            if booking and due_amount(booking) <= 0:
                raise ValueError("This plan is already paid.")

    if not booking or not business:
        raise ValueError("No unpaid plan or booking was found for this client.")

    due = due_amount(booking)
    if due <= 0:
        raise ValueError("This plan is already paid.")

    amount_paise = int(round(due * 100))
    if amount_paise < 100:
        raise ValueError("Amount is too small to collect with Razorpay.")

    existing = (
        db.query(Payment)
        .filter(
            Payment.booking_id == booking.id,
            Payment.status == "created",
            Payment.amount == due,
        )
        .order_by(Payment.id.desc())
        .first()
    )
    if existing:
        return checkout_payload(existing, business, due)

    notes = {
        "business_key": business.key,
        "booking_id": str(booking.id),
        "plan_code": business.plan_code or "",
    }
    order = _create_razorpay_order(amount_paise, f"bk{booking.id}", notes)
    order_id = order.get("id") or ""
    if not order_id:
        raise ValueError("Razorpay did not return an order id.")

    payment = Payment(
        business_key=business.key,
        booking_id=booking.id,
        plan_code=business.plan_code or "",
        amount=due,
        currency="INR",
        razorpay_order_id=order_id,
        status="created",
        payer_name=business.name or "",
        payer_email=business.email or "",
        payer_phone=business.mobile or "",
        notes=json.dumps(notes),
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    logger.info("Created Razorpay order %s for booking %s (₹%.2f)", order_id, booking.id, due)
    return checkout_payload(payment, business, due)


def mark_booking_paid(db: Session, booking: Booking) -> None:
    booking.collected_amount = round_money(booking.amount or 0)
    booking.status = "collected"
    business = db.query(BusinessConfigModel).filter(BusinessConfigModel.key == booking.business_key).first()
    if business and (booking.notes or "").strip() == CLIENT_PLAN_NOTE:
        credit_plan_to_wallet(
            db,
            booking.sales_executive_id,
            business.key,
            business.plan_code or "",
            float(business.plan_amount or booking.amount or 0),
            business.join_date,
        )


def apply_paid_order(
    db: Session,
    *,
    order_id: str,
    payment_id: str = "",
    signature: str = "",
    require_checkout_signature: bool = False,
) -> dict:
    payment = db.query(Payment).filter(Payment.razorpay_order_id == order_id).first()
    if not payment:
        raise ValueError("Unknown Razorpay order.")

    if payment.status == "paid":
        return {"success": True, "already_paid": True, "booking_id": payment.booking_id}

    if require_checkout_signature and not verify_checkout_signature(order_id, payment_id, signature):
        payment.status = "failed"
        db.commit()
        raise ValueError("Payment signature was invalid.")

    booking = db.query(Booking).filter(Booking.id == payment.booking_id).first() if payment.booking_id else None
    if not booking:
        raise ValueError("The booking for this payment was not found.")

    payment.razorpay_payment_id = payment_id or payment.razorpay_payment_id
    payment.razorpay_signature = signature or payment.razorpay_signature
    payment.status = "paid"
    payment.paid_at = datetime.utcnow()
    mark_booking_paid(db, booking)
    db.commit()
    logger.info("Payment captured order=%s payment=%s booking=%s", order_id, payment_id, booking.id)
    return {"success": True, "already_paid": False, "booking_id": booking.id}


def apply_webhook_event(db: Session, event: dict) -> None:
    event_type = event.get("event") or ""
    payload = ((event.get("payload") or {}).get("payment") or {}).get("entity") or {}
    order_id = payload.get("order_id") or ""
    payment_id = payload.get("id") or ""
    if event_type == "order.paid":
        order_ent = ((event.get("payload") or {}).get("order") or {}).get("entity") or {}
        order_id = order_id or order_ent.get("id") or ""
    if event_type not in {"payment.captured", "order.paid"} and not order_id:
        return
    if not order_id:
        return
    try:
        apply_paid_order(db, order_id=order_id, payment_id=payment_id, require_checkout_signature=False)
    except ValueError as exc:
        logger.warning("Webhook ignored for %s: %s", order_id, exc)
