"""投资系统 API：投资章 → 解锁券 → 绕过奖励锁。"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from api.dependencies import get_group_id
from api.models.database import get_db
from api.config import now_cst
from api.services.investment_service import (
    get_today_investment_medals,
    exchange_investment_coupon,
    get_child_investment_coupons,
    use_unlock_coupon,
    get_active_investments,
    get_all_investment_stats,
)

router = APIRouter(prefix="/api/investments", tags=["investments"])


class ExchangeRequest(BaseModel):
    child_id: int
    medal_count: int = 5


class UseCouponRequest(BaseModel):
    child_id: int
    coupon_id: int


# ---- 投资章 ----

@router.get("/medals")
def list_medals(child_id: int, group_id: int = Depends(get_group_id)):
    """查询某孩子的今日投资章数。"""
    conn = get_db()
    cur = conn.cursor()
    try:
        today = now_cst().date()
        count = get_today_investment_medals(cur, child_id, group_id, today)
        return {"child_id": child_id, "medals_today": count}
    finally:
        conn.close()


# ---- 投资券 ----

@router.post("/exchange")
def exchange(req: ExchangeRequest, group_id: int = Depends(get_group_id)):
    """用投资章兑换解锁券。≥5 章起兑，章数越多额外支付比例越低。"""
    conn = get_db()
    cur = conn.cursor()
    try:
        now = now_cst()
        today = now.date()
        result = exchange_investment_coupon(
            cur, req.child_id, group_id, req.medal_count, today, now,
        )
        conn.commit()
        return result
    except ValueError as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        conn.rollback()
        raise HTTPException(status_code=500, detail="服务器内部错误")
    finally:
        conn.close()


@router.get("/coupons")
def list_coupons(child_id: int, group_id: int = Depends(get_group_id)):
    """列出某孩子所有未使用的投资券。"""
    conn = get_db()
    cur = conn.cursor()
    try:
        coupons = get_child_investment_coupons(cur, child_id, group_id)
        return {"child_id": child_id, "coupons": coupons}
    finally:
        conn.close()


@router.post("/use")
def use_coupon(req: UseCouponRequest, group_id: int = Depends(get_group_id)):
    """使用解锁券：标记券已使用，返回解锁比例。"""
    conn = get_db()
    cur = conn.cursor()
    try:
        now = now_cst()
        result = use_unlock_coupon(cur, req.child_id, group_id, req.coupon_id, now)
        conn.commit()
        return result
    except ValueError as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        conn.rollback()
        raise HTTPException(status_code=500, detail="服务器内部错误")
    finally:
        conn.close()


# ---- 活跃投资 ----

@router.get("/active")
def list_active(child_id: int, group_id: int = Depends(get_group_id)):
    """列出某孩子所有活跃投资。"""
    conn = get_db()
    cur = conn.cursor()
    try:
        investments = get_active_investments(cur, child_id, group_id)
        return {"child_id": child_id, "investments": investments}
    finally:
        conn.close()


# ---- Admin 统计 ----

@router.get("/stats")
def stats(group_id: int = Depends(get_group_id)):
    """Admin: 群组所有孩子的投资统计。"""
    conn = get_db()
    cur = conn.cursor()
    try:
        today = now_cst().date()
        rows = get_all_investment_stats(cur, group_id, today)
        return {"children": rows}
    finally:
        conn.close()
