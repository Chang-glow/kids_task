"""投资系统服务：投资章 → 解锁券 → 跳过锁兑换。"""


def award_investment_medal(cur, child_id: int, group_id: int, task_id: int | None, today) -> int:
    """完成任务后奖励 1 枚投资章（同一任务当天只给 1 枚），返回今日累计数。"""
    cur.execute(
        """INSERT INTO investment_medals (child_id, group_id, task_id, medal_date)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (child_id, task_id, medal_date) DO NOTHING""",
        (child_id, group_id, task_id, today),
    )
    cur.execute(
        "SELECT COUNT(*) as cnt FROM investment_medals WHERE child_id = %s AND medal_date = %s",
        (child_id, today),
    )
    return cur.fetchone()["cnt"]


def get_today_investment_medals(cur, child_id: int, group_id: int, today) -> int:
    """获取今日投资章数（无记录返回 0）。"""
    cur.execute(
        "SELECT COUNT(*) as cnt FROM investment_medals WHERE child_id = %s AND group_id = %s AND medal_date = %s",
        (child_id, group_id, today),
    )
    row = cur.fetchone()
    return row["cnt"] if row else 0


def compute_unlock_extra_pct(medal_count: int) -> float:
    """解锁券额外支付比例：5 章 = 50%，每多一章 -10%，10 章 = 0%"""
    raw = 50 - (medal_count - 5) * 10
    return max(0, raw) / 100.0


def exchange_investment_coupon(cur, child_id: int, group_id: int,
                               medal_count: int, today, now) -> dict:
    """用投资章兑换解锁券。≥5 章起兑，券的 medal_count 为实际消耗章数。"""
    if medal_count < 5:
        raise ValueError("至少需要 5 枚投资章才能兑换解锁券")

    balance = get_today_investment_medals(cur, child_id, group_id, today)
    if balance < medal_count:
        raise ValueError(f"投资章不足（当前 {balance}，需要 {medal_count}）")

    cur.execute(
        """DELETE FROM investment_medals
           WHERE id IN (
               SELECT id FROM investment_medals
               WHERE child_id = %s AND medal_date = %s
               ORDER BY created_at ASC LIMIT %s
           )""",
        (child_id, today, medal_count),
    )

    cur.execute(
        """INSERT INTO investment_coupons (child_id, group_id, medal_count, created_at)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        (child_id, group_id, medal_count, now),
    )
    coupon_id = cur.fetchone()["id"]

    extra_pct = compute_unlock_extra_pct(medal_count)
    return {
        "success": True,
        "coupon_id": coupon_id,
        "medal_count": medal_count,
        "unlock_extra_pct": round(extra_pct * 100),
        "medals_remaining": balance - medal_count,
    }


def get_child_investment_coupons(cur, child_id: int, group_id: int) -> list[dict]:
    """列出孩子所有未使用的投资券。"""
    cur.execute(
        "SELECT * FROM investment_coupons WHERE child_id = %s AND group_id = %s AND used = false"
        " ORDER BY created_at DESC",
        (child_id, group_id),
    )
    return [dict(r) for r in cur.fetchall()]


def use_unlock_coupon(cur, child_id: int, group_id: int,
                      coupon_id: int, now) -> dict:
    """使用解锁券绕过奖励锁：验证券有效 → 标记已用 → 返回额外支付比例。"""
    cur.execute(
        """SELECT * FROM investment_coupons
           WHERE id = %s AND child_id = %s AND group_id = %s AND used = false""",
        (coupon_id, child_id, group_id),
    )
    coupon = cur.fetchone()
    if not coupon:
        raise ValueError("解锁券不存在或已使用")

    extra_pct = compute_unlock_extra_pct(coupon["medal_count"])

    cur.execute(
        "UPDATE investment_coupons SET used = true, used_at = %s WHERE id = %s",
        (now, coupon_id),
    )

    cur.execute(
        """INSERT INTO point_logs (action, amount, description, created_at, group_id, child_id)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        ("use_unlock_coupon", 0,
         f"使用解锁券（{coupon['medal_count']}章）→ 额外支付 {int(extra_pct * 100)}% 绕过奖励锁",
         now, group_id, child_id),
    )

    return {
        "success": True,
        "coupon_id": coupon_id,
        "medal_count": coupon["medal_count"],
        "unlock_extra_pct": round(extra_pct * 100),
    }


def get_active_investments(cur, child_id: int, group_id: int) -> list[dict]:
    """列出孩子所有活跃投资。"""
    cur.execute(
        """SELECT * FROM investments
           WHERE child_id = %s AND group_id = %s AND status = 'active'
           ORDER BY created_at DESC""",
        (child_id, group_id),
    )
    return [dict(r) for r in cur.fetchall()]


def process_daily_payouts(cur, today) -> dict:
    """处理所有活跃投资的每日收益发放。

    收益 0.5 分/天累积在 total_earned 中，每攒满 1 整分才转入孩子余额。
    50 天 × 0.5 = 25 分总收益。
    """
    cur.execute(
        """SELECT * FROM investments WHERE status = 'active'
           AND (last_payout_date IS NULL OR last_payout_date < %s)""",
        (today,),
    )
    active = [dict(r) for r in cur.fetchall()]

    total_paid = 0
    completed = 0
    for inv in active:
        income = float(inv["daily_income"])
        old_earned = float(inv["total_earned"])
        new_earned = old_earned + income

        old_whole = int(old_earned)
        new_whole = int(new_earned)
        points_to_add = new_whole - old_whole

        if points_to_add > 0:
            cur.execute(
                "UPDATE children SET total_points = total_points + %s WHERE id = %s",
                (points_to_add, inv["child_id"]),
            )
            cur.execute(
                """INSERT INTO point_logs (action, amount, description, created_at, group_id, child_id)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                ("invest_income", points_to_add,
                 f"投资收益 +{points_to_add} 分（剩余 {inv['days_remaining'] - 1} 天）",
                 today, inv["group_id"], inv["child_id"]),
            )
            total_paid += points_to_add

        new_remaining = inv["days_remaining"] - 1
        if new_remaining <= 0:
            cur.execute(
                """UPDATE investments SET days_remaining = 0, total_earned = %s,
                   last_payout_date = %s, status = 'completed' WHERE id = %s""",
                (new_earned, today, inv["id"]),
            )
            completed += 1
        else:
            cur.execute(
                """UPDATE investments SET days_remaining = %s, total_earned = %s,
                   last_payout_date = %s WHERE id = %s""",
                (new_remaining, new_earned, today, inv["id"]),
            )

    return {
        "investments_processed": len(active),
        "total_paid": total_paid,
        "completed": completed,
    }


def get_all_investment_stats(cur, group_id: int, today) -> list[dict]:
    """Admin: 获取群组所有孩子的投资章、投资券和活跃投资统计。"""
    cur.execute(
        """SELECT c.id AS child_id, c.name, c.emoji,
                  COALESCE(im.medal_count, 0) AS investment_medals_today,
                  COALESCE(ic.unused_coupons, 0) AS investment_coupons_unused,
                  COALESCE(iv.active_count, 0) AS active_investments
           FROM children c
           LEFT JOIN (
               SELECT child_id, COUNT(*) AS medal_count
               FROM investment_medals WHERE medal_date = %s AND group_id = %s
               GROUP BY child_id
           ) im ON c.id = im.child_id
           LEFT JOIN (
               SELECT child_id, COUNT(*) AS unused_coupons
               FROM investment_coupons WHERE used = false AND group_id = %s
               GROUP BY child_id
           ) ic ON c.id = ic.child_id
           LEFT JOIN (
               SELECT child_id, COUNT(*) AS active_count
               FROM investments WHERE status = 'active' AND group_id = %s
               GROUP BY child_id
           ) iv ON c.id = iv.child_id
           WHERE c.group_id = %s
           ORDER BY c.id""",
        (today, group_id, group_id, group_id, group_id),
    )
    return [dict(r) for r in cur.fetchall()]
