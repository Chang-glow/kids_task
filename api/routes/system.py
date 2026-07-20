"""系统端点：健康检查、定时任务。"""

import os

from fastapi import APIRouter, HTTPException, Query

from api.models.database import get_db
from api.config import now_cst

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/cron/refresh-loans")
def cron_refresh_loans(secret: str = Query(None)):
    """Vercel Cron 每小时触发：结算所有活跃贷款的利息和信用分衰减。"""
    expected = os.environ.get("CRON_SECRET", "")
    if not expected:
        return {"success": False, "detail": "CRON_SECRET not configured"}
    if secret != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")

    from api.services.loan_service import refresh_loans

    conn = get_db()
    cur = conn.cursor()
    try:
        stats = refresh_loans(cur, now_cst())
        conn.commit()
        return {"success": True, **stats}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
