"""积分流水 + 惩罚扣分 + 统计，按 group_id 隔离。"""

import json
import os
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Depends, Query
from api.dependencies import get_group_id
from api.models.database import get_db
from api.models.schemas import PunishRequest
from api.config import now_cst

router = APIRouter(prefix="/api", tags=["logs"])


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


@router.get("/cron/daily-boost")
def cron_daily_boost(secret: str = Query(None)):
    """Vercel Cron 每天 0 点触发：为所有群组预分配翻倍和条件（懒加载兜底）。"""
    expected = os.environ.get("CRON_SECRET", "")
    if not expected:
        return {"success": False, "detail": "CRON_SECRET not configured"}
    if secret != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")

    from api.services.boost_service import ensure_daily_boosts
    from api.services.condition_service import ensure_daily_conditions

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM family_groups")
        groups = [r["id"] for r in cur.fetchall()]
        today = now_cst().date()
        for gid in groups:
            ensure_daily_boosts(cur, gid, today)
            ensure_daily_conditions(cur, gid, today)
        conn.commit()
        return {"success": True, "groups_processed": len(groups)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# 惩罚冷静期限制
PUNISH_LIMITS = [
    (timedelta(minutes=10), 10),
    (timedelta(hours=1), 25),
    (timedelta(days=1), 100),
]


def _check_punish_limits(cur, group_id: int, wanted: int):
    """检查惩罚冷静期，返回 (可扣除分数, 限制描述)"""
    now = now_cst()
    max_allowed = wanted
    for window, limit in PUNISH_LIMITS:
        since = now - window
        cur.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM point_logs"
            " WHERE group_id = %s AND action = 'punish' AND created_at >= %s",
            (group_id, since),
        )
        recent = int(cur.fetchone()["coalesce"])
        remaining = limit - recent
        if remaining <= 0:
            return 0, "教育需要循序渐进，请冷静考虑 😊"
        if remaining < max_allowed:
            max_allowed = remaining
    return max_allowed, ""


def _window_name(window: timedelta) -> str:
    if window == timedelta(minutes=10):
        return "10 分钟"
    if window == timedelta(hours=1):
        return "1 小时"
    return "24 小时"


@router.get("/logs")
def get_logs(group_id: int = Depends(get_group_id), offset: int = 0, limit: int = 10):
    """分页获取流水记录"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM point_logs WHERE group_id = %s", (group_id,))
    total = cur.fetchone()["count"]
    cur.execute(
        "SELECT * FROM point_logs WHERE group_id = %s ORDER BY created_at DESC LIMIT %s OFFSET %s",
        (group_id, limit, offset),
    )
    logs = cur.fetchall()
    conn.close()
    return {"total": total, "logs": [dict(l) for l in logs]}


@router.post("/punish")
def punish_user(req: PunishRequest, group_id: int = Depends(get_group_id)):
    """惩罚扣分（冷静期限制 + 不扣成负数）"""
    if req.penalty_points <= 0:
        raise HTTPException(status_code=400, detail="扣分值必须大于0")
    if len(req.name.strip()) == 0:
        raise HTTPException(status_code=400, detail="惩罚原因不能为空")
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, total_points FROM children WHERE group_id = %s ORDER BY id LIMIT 1", (group_id,))
        child = cur.fetchone()
        if not child:
            raise HTTPException(status_code=400, detail="群组中没有孩子")

        # 冷静期检查
        allowed, limit_msg = _check_punish_limits(cur, group_id, req.penalty_points)
        if allowed <= 0:
            raise HTTPException(status_code=400, detail=limit_msg)

        effective_penalty = min(req.penalty_points, allowed)
        current_points = child["total_points"]
        new_points = max(0, current_points - effective_penalty)
        actual_deducted = current_points - new_points

        if actual_deducted <= 0:
            raise HTTPException(status_code=400, detail="当前无法扣除积分")

        now = now_cst()
        cur.execute("UPDATE children SET total_points = %s WHERE id = %s", (new_points, child["id"]))

        extra = ""
        if effective_penalty < req.penalty_points:
            extra = f"（受冷静期限制，实际扣除 {actual_deducted} 分）"
        description = f"{req.emoji} 惩罚「{req.name.strip()}」→ -{actual_deducted}分{extra}"
        cur.execute(
            "INSERT INTO point_logs (action, amount, description, created_at, group_id, child_id)"
            " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            ("punish", actual_deducted, description, now, group_id, child["id"]),
        )
        log_id = cur.fetchone()["id"]

        cur.execute(
            "INSERT INTO undo_operations (group_id, child_id, operation_type, description, undo_data, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (group_id, child["id"], "punish", description,
             json.dumps({"actual_deducted": actual_deducted, "log_id": log_id,
                         "child_id": child["id"]}), now),
        )

        cur.execute("UPDATE users SET total_points = GREATEST(0, total_points - %s) WHERE id = 1", (actual_deducted,))

        conn.commit()

        msg = f"已扣除 {actual_deducted} 分"
        if extra:
            msg += f"（{limit_msg or '受冷静期限制'}）"
        msg += f"！{req.emoji}"
        return {
            "success": True,
            "deducted_points": actual_deducted,
            "total_points": new_points,
            "message": msg,
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise HTTPException(status_code=500, detail="服务器内部错误")
    finally:
        conn.close()


@router.get("/punish-limits")
def get_punish_limits(group_id: int = Depends(get_group_id)):
    """查看当前惩罚冷静期剩余额度"""
    conn = get_db()
    cur = conn.cursor()
    now = now_cst()
    limits = []
    for window, limit in PUNISH_LIMITS:
        since = now - window
        cur.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM point_logs"
            " WHERE group_id = %s AND action = 'punish' AND created_at >= %s",
            (group_id, since),
        )
        used = int(cur.fetchone()["coalesce"])
        limits.append({
            "window": _window_name(window),
            "limit": limit,
            "used": used,
            "remaining": max(0, limit - used),
        })
    conn.close()
    return {"limits": limits}


@router.get("/stats")
def get_stats(group_id: int = Depends(get_group_id)):
    """积分统计（按日/周/月聚合）"""
    conn = get_db()
    cur = conn.cursor()
    try:
        result = {}
        for period in ["day", "week", "month"]:
            cur.execute(
                """
                SELECT
                    date_trunc(%s, created_at)          AS period_start,
                    SUM(CASE WHEN action = 'earn'
                             THEN amount ELSE 0 END)    AS earned,
                    SUM(CASE WHEN action IN ('spend', 'punish')
                             THEN amount ELSE 0 END)    AS spent,
                    SUM(CASE WHEN action = 'earn'
                             THEN amount ELSE -amount END) AS net
                FROM point_logs
                WHERE group_id = %s
                GROUP BY date_trunc(%s, created_at)
                ORDER BY period_start DESC
                LIMIT 30
                """,
                (period, group_id, period),
            )
            rows = cur.fetchall()
            result[period] = [
                {
                    "period_start": row["period_start"].strftime(
                        "%Y-%m-%d"
                        if period == "day"
                        else "%Y 第%W周"
                        if period == "week"
                        else "%Y-%m"
                    ),
                    "earned": int(row["earned"] or 0),
                    "spent": int(row["spent"] or 0),
                    "net": int(row["net"] or 0),
                }
                for row in rows
            ]
        return result
    except Exception:
        raise HTTPException(status_code=500, detail="服务器内部错误")
    finally:
        conn.close()
