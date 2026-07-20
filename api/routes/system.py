"""系统端点：健康检查、定时任务。"""

import os

from fastapi import APIRouter, HTTPException, Query, Request

from api.models.database import get_db
from api.config import now_cst

router = APIRouter(tags=["system"])


@router.get("/api/health")
def health():
    return {"status": "ok"}


@router.get("/api/debug-path")
def debug_path(request: Request):
    """临时：返回 FastAPI 看到的实际请求路径。"""
    return {
        "url_path": str(request.url.path),
        "root_path": request.scope.get("root_path", ""),
        "path": request.scope.get("path", ""),
        "raw_path": str(request.scope.get("raw_path", b"")),
    }


@router.get("/api/cron/refresh-loans")
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
