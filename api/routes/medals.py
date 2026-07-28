"""奖章 + 优惠券路由。"""

from fastapi import APIRouter, HTTPException, Depends
from api.dependencies import get_group_id
from api.models.database import get_db
from api.models.schemas import ExchangeCouponRequest
from api.config import now_cst
from api.services.medal_service import (
    award_medal, get_today_medals,
    exchange_coupon, get_child_coupons, delete_coupon,
)

router = APIRouter(prefix="/api", tags=["medals"])


def _get_child_id(cur, group_id: int) -> int:
    """获取群组中第一个孩子的 ID。"""
    cur.execute("SELECT id FROM children WHERE group_id = %s ORDER BY id LIMIT 1", (group_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=400, detail="群组中没有孩子")
    return row["id"]


@router.get("/medals/today")
def get_medals_today(group_id: int = Depends(get_group_id)):
    """获取今日奖章数。"""
    conn = get_db()
    cur = conn.cursor()
    child_id = _get_child_id(cur, group_id)
    today = now_cst().date()
    count = get_today_medals(cur, child_id, group_id, today)
    conn.close()
    return {"count": count}


@router.post("/medals/exchange")
def exchange_medals(req: ExchangeCouponRequest, group_id: int = Depends(get_group_id)):
    """用奖章兑换优惠券。客户端自定折扣%和消耗奖章数。"""
    conn = get_db()
    cur = conn.cursor()
    try:
        child_id = _get_child_id(cur, group_id)
        now = now_cst()
        result = exchange_coupon(
            cur, child_id, group_id,
            req.coupon_type, req.discount_pct, req.medal_cost, now,
        )
        conn.commit()
        return result
    except ValueError as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise HTTPException(status_code=500, detail="服务器内部错误")
    finally:
        conn.close()


@router.get("/coupons")
def list_coupons(group_id: int = Depends(get_group_id)):
    """列出孩子所有未使用的优惠券。"""
    conn = get_db()
    cur = conn.cursor()
    child_id = _get_child_id(cur, group_id)
    coupons = get_child_coupons(cur, child_id, group_id)
    conn.close()
    return coupons


@router.delete("/coupons/{coupon_id}")
def discard_coupon(coupon_id: int, group_id: int = Depends(get_group_id)):
    """丢弃未使用的优惠券。"""
    conn = get_db()
    cur = conn.cursor()
    try:
        child_id = _get_child_id(cur, group_id)
        result = delete_coupon(cur, coupon_id, child_id, group_id)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise HTTPException(status_code=500, detail="服务器内部错误")
    finally:
        conn.close()
