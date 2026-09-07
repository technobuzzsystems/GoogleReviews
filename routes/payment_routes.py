"""Razorpay checkout (public pay page, admin collect, webhook)."""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from config import get_config
from database import get_db
from models.domain_models import BusinessConfigModel, Payment
from services.auth_service import can_view_sales_book, get_session_user, require_sales_book
from services.payment_service import (
    apply_paid_order,
    apply_webhook_event,
    create_checkout_order,
    due_amount,
    razorpay_configured,
    unpaid_booking,
    verify_webhook_signature,
)
from services.email_service import smtp_configured
from services.invoice_service import (
    create_invoice_for_payment,
    get_invoice,
    invoice_pdf_bytes,
    invoice_pdf_filename,
    invoice_view_data,
    latest_invoice_for_business,
    send_invoice,
)
from services.plan_service import PLANS
from services.storage_service import public_logo_url

router = APIRouter()
templates = Jinja2Templates(directory="templates")
logger = logging.getLogger(__name__)


class CheckoutVerifyBody(BaseModel):
    razorpay_order_id: str = Field(..., min_length=8, max_length=80)
    razorpay_payment_id: str = Field(..., min_length=8, max_length=80)
    razorpay_signature: str = Field(..., min_length=8, max_length=200)


class InvoiceSendBody(BaseModel):
    channel: str = Field(..., min_length=5, max_length=12)
    to: str = Field(default="", max_length=200)


class AdminOrderBody(BaseModel):
    business_key: str = Field(default="", max_length=120)
    booking_id: int = Field(default=0, ge=0)


def _invoice_or_404(db: Session, invoice_no: str):
    invoice = get_invoice(db, invoice_no)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    return invoice


def _invoice_no_for_paid_business(db: Session, business: BusinessConfigModel) -> str:
    invoice = latest_invoice_for_business(db, business.key)
    if invoice:
        return invoice.invoice_no
    payment = (
        db.query(Payment)
        .filter(Payment.business_key == business.key, Payment.status == "paid")
        .order_by(Payment.id.desc())
        .first()
    )
    if not payment:
        return ""
    invoice = create_invoice_for_payment(db, payment, commit=True)
    return invoice.invoice_no if invoice else ""


def _pay_context(request: Request, business: BusinessConfigModel, db: Session, *, paid: bool, error: str = ""):
    cfg = get_config()
    booking = unpaid_booking(db, business_key=business.key)
    due = due_amount(booking) if booking else 0.0
    plan = PLANS.get(business.plan_code or "", {})
    return {
        "request": request,
        "business": business,
        "logo_url": public_logo_url(business.logo_filename or ""),
        "plan_label": plan.get("label") or (business.plan_code or "Plan"),
        "amount": due or float(business.plan_amount or 0),
        "paid": paid,
        "error": error,
        "razorpay_ready": razorpay_configured(),
        "company_name": cfg.COMPANY_NAME,
        "invoice_no": _invoice_no_for_paid_business(db, business) if paid else "",
    }


@router.get("/invoice/{invoice_no}")
def invoice_page(request: Request, invoice_no: str, db: Session = Depends(get_db)):
    invoice = _invoice_or_404(db, invoice_no)
    return templates.TemplateResponse(
        request=request,
        name="invoice.html",
        context={
            "request": request,
            "invoice": invoice_view_data(invoice),
            "smtp_ready": smtp_configured(),
        },
    )


@router.get("/invoice/{invoice_no}/download")
def invoice_download(invoice_no: str, db: Session = Depends(get_db)):
    invoice = _invoice_or_404(db, invoice_no)
    filename = invoice_pdf_filename(invoice.invoice_no)
    return Response(
        content=invoice_pdf_bytes(invoice),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/invoice/{invoice_no}/send")
def invoice_send(
    request: Request,
    invoice_no: str,
    body: InvoiceSendBody,
    db: Session = Depends(get_db),
):
    invoice = _invoice_or_404(db, invoice_no)
    user = get_session_user(request, db)
    try:
        return send_invoice(
            invoice,
            channel=body.channel,
            to=body.to,
            as_staff=can_view_sales_book(user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/pay/{business_key}")
def pay_page(request: Request, business_key: str, db: Session = Depends(get_db)):
    business = db.query(BusinessConfigModel).filter(BusinessConfigModel.key == business_key).first()
    if not business:
        raise HTTPException(status_code=404, detail="This payment page is not available.")
    booking = unpaid_booking(db, business_key=business.key)
    if booking:
        paid, error = False, ""
    elif float(business.plan_amount or 0) > 0:
        paid, error = True, ""
    else:
        paid, error = False, "This client has no plan amount to collect."
    return templates.TemplateResponse(
        request=request,
        name="pay.html",
        context=_pay_context(request, business, db, paid=paid, error=error)
    )


@router.post("/pay/{business_key}/order")
def public_create_order(business_key: str, db: Session = Depends(get_db)):
    try:
        return create_checkout_order(db, business_key=business_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/pay/{business_key}/verify")
def public_verify(business_key: str, body: CheckoutVerifyBody, db: Session = Depends(get_db)):
    try:
        return apply_paid_order(
            db,
            order_id=body.razorpay_order_id,
            payment_id=body.razorpay_payment_id,
            signature=body.razorpay_signature,
            require_checkout_signature=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/admin/api/payments/order")
def admin_create_order(
    body: AdminOrderBody,
    db: Session = Depends(get_db),
    _user=Depends(require_sales_book),
):
    try:
        return create_checkout_order(db, business_key=body.business_key, booking_id=body.booking_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/admin/api/payments/verify")
def admin_verify(
    body: CheckoutVerifyBody,
    db: Session = Depends(get_db),
    _user=Depends(require_sales_book),
):
    try:
        return apply_paid_order(
            db,
            order_id=body.razorpay_order_id,
            payment_id=body.razorpay_payment_id,
            signature=body.razorpay_signature,
            require_checkout_signature=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/webhooks/razorpay")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    if not verify_webhook_signature(body, signature):
        logger.warning("Rejected Razorpay webhook with invalid signature")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook signature")
    try:
        event = json.loads(body.decode() or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc
    apply_webhook_event(db, event)
    return JSONResponse({"ok": True})
