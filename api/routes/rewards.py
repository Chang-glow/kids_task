"""奖励商城路由：CRUD + 兑换，按 group_id 隔离。"""

import json

from fastapi import APIRouter, HTTPException, Depends
from api.dependencies import get_group_id
from api.models.database import get_db
from api.models.schemas import AddRewardRequest, RedeemRewardRequest
from api.config import now_cst
from api.services.surge_service import ensure_daily_surges, get_todays_surges

router = APIRouter(prefix="/api/rewards", tags=["rewards"])


@router.get("")
def get_rewards(group_id: int = Depends(get_group_id)):
    """获取奖励列表（按 group 过滤，积分升序），注入今日涨价信息。"""
    conn = get_db()
    cur = conn.cursor()
    today = now_cst().date()
    ensure_daily_surges(cur, group_id, today)
    surges = get_todays_surges(cur, group_id, today)

    cur.execute("SELECT * FROM rewards WHERE group_id = %s ORDER BY cost_points ASC", (group_id,))
    rewards = cur.fetchall()
    conn.commit()
    conn.close()

    result = []
    for r in rewards:
        d = dict(r)
        info = surges.get(r["id"])
        if info is not None:
            rate = info["rate"]
            if info["type"] == "sale":
                d["surge_rate"] = None
                d["sale_rate"] = rate
                d["surged_cost"] = max(1, round(r["cost_points"] * (1 - rate)))
            else:
                d["surge_rate"] = rate
                d["sale_rate"] = None
                d["surged_cost"] = round(r["cost_points"] * (1 + rate))
        else:
            d["surge_rate"] = None
            d["sale_rate"] = None
            d["surged_cost"] = None
        result.append(d)
    return result


@router.post("")
def add_reward(req: AddRewardRequest, group_id: int = Depends(get_group_id)):
    """添加新奖励"""
    if req.cost_points <= 0:
        raise HTTPException(status_code=400, detail="所需积分必须大于0")
    if len(req.name.strip()) == 0:
        raise HTTPException(status_code=400, detail="奖励名称不能为空")
    conn = get_db()
    cur = conn.cursor()
    now = now_cst()
    cur.execute(
        "INSERT INTO rewards (name, emoji, cost_points, created_at, group_id) VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (req.name.strip(), req.emoji, req.cost_points, now, group_id),
    )
    reward_id = cur.fetchone()["id"]
    conn.commit()
    cur.execute("SELECT * FROM rewards WHERE id = %s", (reward_id,))
    reward = cur.fetchone()
    conn.close()
    return dict(reward)


@router.post("/redeem")
def redeem_reward(req: RedeemRewardRequest, group_id: int = Depends(get_group_id)):
    """兑换奖励，事务保护不扣成负数"""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM rewards WHERE id = %s AND group_id = %s", (req.reward_id, group_id))
        reward = cur.fetchone()
        if not reward:
            raise HTTPException(status_code=404, detail="奖励不存在")

        # Get first child's points
        cur.execute("SELECT id, total_points FROM children WHERE group_id = %s ORDER BY id LIMIT 1", (group_id,))
        child = cur.fetchone()
        if not child:
            raise HTTPException(status_code=400, detail="群组中没有孩子")
        current_points = child["total_points"]

        # 检查今日涨降价
        today = now_cst().date()
        ensure_daily_surges(cur, group_id, today)
        surges = get_todays_surges(cur, group_id, today)
        info = surges.get(reward["id"])
        if info is not None:
            rate = info["rate"]
            if info["type"] == "sale":
                cost = max(1, round(reward["cost_points"] * (1 - rate)))
            else:
                cost = round(reward["cost_points"] * (1 + rate))
        else:
            cost = reward["cost_points"]

        if current_points < cost:
            hint = ""
            if info:
                pct = int(info["rate"] * 100)
                hint = f"（含{'降价' if info['type'] == 'sale' else '涨价'} {pct}%）"
            raise HTTPException(
                status_code=400,
                detail=f"积分不够啦，继续加油！💪 当前积分：{current_points}，需要：{cost}{hint}，还差：{cost - current_points}",
            )

        now = now_cst()
        cur.execute("UPDATE children SET total_points = total_points - %s WHERE id = %s", (cost, child["id"]))
        cur.execute("SELECT total_points FROM children WHERE id = %s", (child["id"],))
        child_after = cur.fetchone()
        if child_after["total_points"] < 0:
            conn.rollback()
            raise HTTPException(status_code=400, detail="积分异常，兑换失败")

        if info is not None:
            pct = int(info["rate"] * 100)
            label = "降价" if info["type"] == "sale" else "涨价"
            description = f"兑换奖励「{reward['name']}」{reward['emoji']} → -{cost}分（原价{reward['cost_points']}，{label}{pct}%）"
        else:
            description = f"兑换奖励「{reward['name']}」{reward['emoji']} → -{cost}分"
        cur.execute(
            "INSERT INTO point_logs (action, amount, description, created_at, group_id, child_id)"
            " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            ("spend", cost, description, now, group_id, child["id"]),
        )
        log_id = cur.fetchone()["id"]

        undo_data = {
            "reward_name": reward["name"], "cost": cost,
            "log_id": log_id, "child_id": child["id"],
        }
        if info is not None:
            undo_data["rate"] = info["rate"]
            undo_data["type"] = info["type"]
        cur.execute(
            "INSERT INTO undo_operations (group_id, child_id, operation_type, description, undo_data, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (group_id, child["id"], "redeem_reward", description,
             json.dumps(undo_data), now),
        )

        # Also update legacy users table
        cur.execute("UPDATE users SET total_points = GREATEST(0, total_points - %s) WHERE id = 1", (cost,))

        conn.commit()

        return {
            "success": True,
            "spent_points": cost,
            "total_points": child_after["total_points"],
            "message": f"兑换成功！{reward['emoji']} 享受你的「{reward['name']}」吧！",
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise HTTPException(status_code=500, detail="服务器内部错误")
    finally:
        conn.close()


@router.delete("/{reward_id}")
def delete_reward(reward_id: int, group_id: int = Depends(get_group_id)):
    """删除奖励（需属于指定群组）"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM rewards WHERE id = %s AND group_id = %s", (reward_id, group_id))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="奖励不存在")
    cur.execute("DELETE FROM rewards WHERE id = %s", (reward_id,))
    conn.commit()
    conn.close()
    return {"success": True}


@router.get("/surges/today")
def get_todays_surges_endpoint(group_id: int = Depends(get_group_id)):
    """获取今日涨价映射 {reward_id: rate}。"""
    conn = get_db()
    cur = conn.cursor()
    today = now_cst().date()
    ensure_daily_surges(cur, group_id, today)
    surges = get_todays_surges(cur, group_id, today)
    conn.commit()
    conn.close()
    return surges
