"""奖章 + 优惠券服务：完成任务获得奖章 → 兑换优惠券 → 兑换奖励时使用。"""


def compute_effective_price(coupon_type: str, discount_pct: int,
                            pricing_rate: float) -> float:
    """纯函数：根据优惠券类型和当前定价率，计算有效定价率。

    anti_surge: 仅在 pricing_rate > 0 时生效，按 discount_pct 抵消涨价（最低到 0）
    pro_sale:   强制应用 -(discount_pct/100) 降价率
    """
    if coupon_type == "anti_surge":
        if pricing_rate > 0:
            reduced = pricing_rate * (1 - discount_pct / 100)
            return max(0.0, reduced)
        return pricing_rate

    if coupon_type == "pro_sale":
        return -(discount_pct / 100)

    return pricing_rate


# ---- 奖章 ----

def award_medal(cur, child_id: int, group_id: int, today) -> int:
    """完成任务后奖励 1 枚奖章，返回今日累计数。"""
    cur.execute(
        """INSERT INTO daily_medals (child_id, group_id, medal_date, count)
           VALUES (%s, %s, %s, 1)
           ON CONFLICT (child_id, medal_date) DO UPDATE
           SET count = daily_medals.count + 1
           RETURNING count""",
        (child_id, group_id, today),
    )
    return cur.fetchone()["count"]


def get_today_medals(cur, child_id: int, group_id: int, today) -> int:
    """获取今日奖章数（无记录返回 0）。"""
    cur.execute(
        "SELECT count FROM daily_medals WHERE child_id = %s AND group_id = %s AND medal_date = %s",
        (child_id, group_id, today),
    )
    row = cur.fetchone()
    return row["count"] if row else 0


# ---- 优惠券 ----

def exchange_coupon(cur, child_id: int, group_id: int, coupon_type: str,
                    discount_pct: int, medal_cost: int, now) -> dict:
    """用奖章兑换优惠券。校验余额后扣减奖章，创建优惠券记录。"""
    if coupon_type not in ("anti_surge", "pro_sale"):
        raise ValueError("coupon_type 必须是 anti_surge 或 pro_sale")
    if discount_pct < 1 or discount_pct > 100:
        raise ValueError("discount_pct 必须在 1-100 之间")
    if medal_cost < 1:
        raise ValueError("medal_cost 必须大于 0")

    today = now.date()
    balance = get_today_medals(cur, child_id, group_id, today)
    if balance < medal_cost:
        raise ValueError(f"奖章不足（当前 {balance}，需要 {medal_cost}）")

    cur.execute(
        "UPDATE daily_medals SET count = count - %s"
        " WHERE child_id = %s AND medal_date = %s",
        (medal_cost, child_id, today),
    )

    cur.execute(
        """INSERT INTO coupons (child_id, group_id, coupon_type, discount_pct, created_at)
           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
        (child_id, group_id, coupon_type, discount_pct, now),
    )
    coupon_id = cur.fetchone()["id"]
    return {"success": True, "coupon_id": coupon_id, "medals_remaining": balance - medal_cost}


def get_child_coupons(cur, child_id: int, group_id: int) -> list[dict]:
    """列出孩子所有未使用的优惠券。"""
    cur.execute(
        "SELECT * FROM coupons WHERE child_id = %s AND group_id = %s AND used = false"
        " ORDER BY created_at DESC",
        (child_id, group_id),
    )
    return [dict(r) for r in cur.fetchall()]


def apply_coupon(cur, coupon_id: int, reward_id: int, child_id: int,
                 group_id: int, now) -> dict:
    """标记优惠券为已使用。调用前需已通过校验。"""
    cur.execute(
        """UPDATE coupons SET used = true, reward_id = %s, used_at = %s
           WHERE id = %s AND child_id = %s AND group_id = %s AND used = false""",
        (reward_id, now, coupon_id, child_id, group_id),
    )
    return {"success": True}


def delete_coupon(cur, coupon_id: int, child_id: int, group_id: int) -> dict:
    """丢弃未使用的优惠券。"""
    cur.execute(
        "DELETE FROM coupons WHERE id = %s AND child_id = %s AND group_id = %s AND used = false",
        (coupon_id, child_id, group_id),
    )
    return {"success": True}


def get_all_children_medals(cur, group_id: int, today) -> list[dict]:
    """Admin: 获取群组所有孩子的今日奖章和优惠券统计。"""
    cur.execute(
        """SELECT c.id AS child_id, c.name, c.emoji,
                  COALESCE(dm.count, 0) AS medals_today,
                  COALESCE(cp.total_coupons, 0) AS coupons_unused
           FROM children c
           LEFT JOIN daily_medals dm ON c.id = dm.child_id AND dm.medal_date = %s
           LEFT JOIN (
               SELECT child_id, COUNT(*) AS total_coupons
               FROM coupons WHERE used = false AND group_id = %s
               GROUP BY child_id
           ) cp ON c.id = cp.child_id
           WHERE c.group_id = %s
           ORDER BY c.id""",
        (today, group_id, group_id),
    )
    return [dict(r) for r in cur.fetchall()]
