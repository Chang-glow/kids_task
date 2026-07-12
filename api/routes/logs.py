"""积分流水 + 惩罚扣分 + 统计，按 group_id 隔离。"""

import json

from fastapi import APIRouter, HTTPException, Depends
from api.dependencies import get_group_id
from api.models.database import get_db
from api.models.schemas import PunishRequest
from api.config import now_cst

router = APIRouter(prefix="/api", tags=["logs"])


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
    """惩罚扣分（不扣成负数）"""
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
        current_points = child["total_points"]
        new_points = max(0, current_points - req.penalty_points)
        actual_deducted = current_points - new_points

        now = now_cst()
        cur.execute("UPDATE children SET total_points = %s WHERE id = %s", (new_points, child["id"]))

        description = f"{req.emoji} 惩罚「{req.name.strip()}」→ -{actual_deducted}分"
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

        return {
            "success": True,
            "deducted_points": actual_deducted,
            "total_points": new_points,
            "message": f"已扣除 {actual_deducted} 积分，请下次注意！{req.emoji}",
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise HTTPException(status_code=500, detail="服务器内部错误")
    finally:
        conn.close()


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
