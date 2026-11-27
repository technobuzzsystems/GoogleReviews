"""
routes/server_engine_routes.py
--------------------------------
FastAPI routes for controlling and monitoring the 24/7 Server-Side Headless Review Auto-Replier Engine.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models.domain_models import BusinessConfigModel
from services.server_reply_engine import (
    ServerReplyEngine,
    get_chrome_executable,
    sync_all_active_businesses,
    sync_business_server_side,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/server-engine", tags=["Server-Side Engine"])


class ToggleEngineRequest(BaseModel):
    enabled: bool


@router.get("/status/{business_key}")
def get_server_engine_status(
    business_key: str,
    db: Session = Depends(get_db),
):
    """Return health, environment, and status of the Server-Side Engine for a business."""
    chrome_path = get_chrome_executable()
    biz = db.query(BusinessConfigModel).filter(BusinessConfigModel.key == business_key).first()

    return {
        "business_key": business_key,
        "business_name": biz.name if biz else business_key,
        "is_ready": bool(chrome_path),
        "chrome_executable": chrome_path or "Not found",
        "mode": "24/7 Zero-Browser Server-Side",
        "auto_sync_interval": "60 seconds",
        "auto_send_enabled": getattr(biz, "auto_send_enabled", True) if biz else True,
    }


@router.post("/sync/{business_key}")
def trigger_server_sync(
    business_key: str,
    db: Session = Depends(get_db),
):
    """Trigger an immediate server-side headless review check and AI auto-reply."""
    try:
        result = sync_business_server_side(db, business_key)
        return result
    except Exception as e:
        logger.exception("Error in server sync for '%s'", business_key)
        return {
            "success": False,
            "business_key": business_key,
            "error": str(e),
            "unreplied_found": 0,
            "replied_count": 0,
            "items": [],
        }


@router.post("/login/{business_key}")
def trigger_server_login(
    business_key: str,
):
    """Launch 1-time visible Google login window for the server profile."""
    from services.server_reply_engine import launch_business_login
    result = launch_business_login(business_key)
    return result


@router.post("/sync-all")
def trigger_sync_all_businesses(
    db: Session = Depends(get_db),
):
    """Trigger server-side review checks for all businesses configured in the system."""
    results = sync_all_active_businesses(db)
    return {"count": len(results), "results": results}
