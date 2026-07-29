"""奖章 + 优惠券服务：完成任务获得奖章 → 兑换优惠券 → 兑换奖励时使用。"""


def compute_effective_price(medal_count: int, pricing_rate: float) -> float:
    """纯函数：每枚奖章 = 2% 涨降价额度，从当前定价率中扣除。

    effective_rate = pricing_rate - (medal_count * 0.02)

    自然统一了旧 anti_surge 和 pro_sale 逻辑：
    - 涨价时（rate > 0）：抵消涨价，奖章够多可转为降价
    - 平价时（rate = 0）：直接产生降价
    - 降价时（rate < 0）：进一步加大降价力度
    """
    return pricing_rate - (medal_count * 0.02)


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

def exchange_coupon(cur, child_id: int, group_id: int,
                    medal_count: int, now) -> dict:
    """用奖章兑换优惠券。5 章起兑，每枚奖章 = 2% 涨降价额度。"""
    if medal_count < 5:
        raise ValueError("至少需要 5 枚奖章才能兑换优惠券")
    if medal_count < 1:
        raise ValueError("奖章数量必须大于 0")

    today = now.date()
    balance = get_today_medals(cur, child_id, group_id, today)
    if balance < medal_count:
        raise ValueError(f"奖章不足（当前 {balance}，需要 {medal_count}）")

    cur.execute(
        "UPDATE daily_medals SET count = count - %s"
        " WHERE child_id = %s AND medal_date = %s",
        (medal_count, child_id, today),
    )

    cur.execute(
        """INSERT INTO coupons (child_id, group_id, medal_count, created_at)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        (child_id, group_id, medal_count, now),
    )
    coupon_id = cur.fetchone()["id"]
    return {
        "success": True,
        "coupon_id": coupon_id,
        "medals_remaining": balance - medal_count,
        "adjustment_pct": medal_count * 2,
    }


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
