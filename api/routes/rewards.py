"""奖励商城路由：CRUD + 兑换，按 group_id 隔离。"""

import json

from fastapi import APIRouter, HTTPException, Depends
from api.dependencies import get_group_id
from api.models.database import get_db
from api.models.schemas import AddRewardRequest, RedeemRewardRequest
from api.config import now_cst
from api.services.pricing_service import ensure_daily_pricing, get_todays_pricing

router = APIRouter(prefix="/api/rewards", tags=["rewards"])


@router.get("")
def get_rewards(group_id: int = Depends(get_group_id)):
    """获取奖励列表（按 group 过滤，积分升序），注入当前时段定价信息。"""
    conn = get_db()
    cur = conn.cursor()
    now = now_cst()
    today = now.date()
    ensure_daily_pricing(cur, group_id, today)
    pricing = get_todays_pricing(cur, group_id, today, now.hour, now.minute)

    cur.execute("SELECT * FROM rewards WHERE group_id = %s ORDER BY cost_points ASC", (group_id,))
    rewards = cur.fetchall()

    # 批量加载所有奖励的锁信息
    from api.services.lock_service import get_reward_locks
    locks_by_reward = {}
    for r in rewards:
        locks = get_reward_locks(cur, r["id"], group_id)
        pending = []
        for lk in locks:
            if lk["key_task_status"] == "done":
                cd = lk["completed_at"].date() if lk["completed_at"] else None
                if cd == today:
                    continue
            pending.append(lk["key_task_name"])
        locks_by_reward[r["id"]] = {"locked": len(pending) > 0, "pending_tasks": pending,
                                     "total_keys": len(locks)} if locks else None

    conn.commit()
    conn.close()

    result = []
    for r in rewards:
        d = dict(r)
        info = pricing.get(r["id"])
        if info is not None and not info.get("is_flat"):
            rate = info["rate"]
            d["pricing_rate"] = rate
            if rate >= 0:
                d["surged_cost"] = round(r["cost_points"] * (1 + rate))
            else:
                d["surged_cost"] = max(1, round(r["cost_points"] * (1 + rate)))
        else:
            d["pricing_rate"] = 0.0
            d["surged_cost"] = None
        d["lock"] = locks_by_reward.get(r["id"])
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

        # 奖励锁检查：所有钥匙任务今日均完成才可兑换
        from api.services.lock_service import check_reward_unlocked
        unlocked, lock_reason = check_reward_unlocked(cur, req.reward_id, group_id, now_cst().date())
        if not unlocked:
            raise HTTPException(status_code=403, detail=lock_reason)

        # Get first child's points
        cur.execute("SELECT id, total_points FROM children WHERE group_id = %s ORDER BY id LIMIT 1", (group_id,))
        child = cur.fetchone()
        if not child:
            raise HTTPException(status_code=400, detail="群组中没有孩子")
        current_points = child["total_points"]

        # 优惠券校验
        coupon = None
        if req.coupon_id is not None:
            cur.execute(
                "SELECT * FROM coupons WHERE id = %s AND child_id = %s AND group_id = %s AND used = false",
                (req.coupon_id, child["id"], group_id),
            )
            coupon = cur.fetchone()
            if not coupon:
                raise HTTPException(status_code=400, detail="优惠券不存在或已使用")

        # 检查今日时段定价
        now = now_cst()
        today = now.date()
        ensure_daily_pricing(cur, group_id, today)
        pricing = get_todays_pricing(cur, group_id, today, now.hour, now.minute)
        info = pricing.get(reward["id"])
        rate = 0.0
        if info is not None and not info.get("is_flat"):
            rate = info["rate"]
            if rate >= 0:
                cost = round(reward["cost_points"] * (1 + rate))
            else:
                cost = max(1, round(reward["cost_points"] * (1 + rate)))
        else:
            cost = reward["cost_points"]
            rate = 0.0

        # 应用优惠券调整有效价格
        coupon_desc = ""
        if coupon is not None:
            from api.services.medal_service import compute_effective_price, coupon_discount_pct
            effective_rate = compute_effective_price(
                coupon["medal_count"], rate,
            )
            cost = max(1, round(reward["cost_points"] * (1 + effective_rate)))
            adj_pct = coupon_discount_pct(coupon["medal_count"])
            coupon_desc = f"（优惠券 {coupon['medal_count']}章 -{adj_pct}%）"

        if current_points < cost:
            hint = ""
            if info is not None and not info.get("is_flat"):
                pct = int(abs(info["rate"]) * 100)
                direction = "涨价" if info["rate"] > 0 else "降价"
                hint = f"（含{direction} {pct}%）"
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

        if info is not None and not info.get("is_flat"):
            pct = int(abs(info["rate"]) * 100)
            direction = "涨价" if info["rate"] > 0 else "降价"
            description = f"兑换奖励「{reward['name']}」{reward['emoji']} → -{cost}分（原价{reward['cost_points']}，{direction}{pct}%）{coupon_desc}"
        else:
            description = f"兑换奖励「{reward['name']}」{reward['emoji']} → -{cost}分{coupon_desc}"
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
        if info is not None and not info.get("is_flat"):
            undo_data["pricing_rate"] = info["rate"]
        if coupon is not None:
            from api.services.medal_service import apply_coupon
            apply_coupon(cur, coupon["id"], reward["id"], child["id"], group_id, now)
            undo_data["coupon_id"] = coupon["id"]
            undo_data["coupon_used"] = True
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


@router.get("/pricing/today")
def get_todays_pricing_endpoint(group_id: int = Depends(get_group_id)):
    """获取今日当前时段定价映射 {reward_id: {rate, is_flat}}。"""
    conn = get_db()
    cur = conn.cursor()
    now = now_cst()
    today = now.date()
    ensure_daily_pricing(cur, group_id, today)
    pricing = get_todays_pricing(cur, group_id, today, now.hour, now.minute)
    conn.commit()
    conn.close()
    return pricing
