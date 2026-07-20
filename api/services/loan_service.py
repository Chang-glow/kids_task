"""贷款业务逻辑：利息计算、还款分摊、信用分、限额、资格检查。"""

from datetime import datetime, timedelta


def get_interest_rate(cur) -> float:
    """从 admin_settings 读取日利率(%)，默认 5.0"""
    cur.execute("SELECT value FROM admin_settings WHERE key = 'loan_interest_rate'")
    row = cur.fetchone()
    return float(row["value"]) if row else 5.0


def get_max_amount(cur) -> int:
    """从 admin_settings 读取最高贷款额度，默认 200"""
    cur.execute("SELECT value FROM admin_settings WHERE key = 'loan_max_amount'")
    row = cur.fetchone()
    return int(row["value"]) if row else 200


def compute_level(credit_score: int) -> int:
    """根据信用分计算等级，每 50 分一个台阶，0 对应 100 分基准。"""
    return (credit_score - 100) // 50


def calculate_loan_limits(credit_score: int, base_max: int, base_interest: float) -> dict:
    """
    根据信用分计算贷款限额。
    - 每高于基准 50 分: max 翻倍, 每周贷款次数翻倍
    - 每低于基准 50 分: max 减半, 冷却期翻倍
    """
    level = compute_level(credit_score)
    one_over = level >= 0
    abs_level = abs(level)

    if one_over:
        max_amount = base_max * (2 ** abs_level)
        loans_per_week = 1 * (2 ** abs_level)
        cooldown_days = 7
    else:
        max_amount = max(1, base_max // (2 ** abs_level))
        loans_per_week = 1
        cooldown_days = 7 * (2 ** abs_level)

    return {
        "max_amount": max_amount,
        "loans_per_week": loans_per_week,
        "cooldown_days": cooldown_days,
        "interest_rate": base_interest,
        "level": level,
    }


def calculate_credit_change(borrowed_at: datetime, now: datetime) -> int:
    """
    计算还款时的信用分变化。
    1 天内还清: +5，每晚 1 天: 递减 1，可为负数。
    """
    days_late = (now - borrowed_at).days
    return 5 - max(0, days_late - 1)


def calculate_repay_info(loan: dict, now: datetime) -> dict:
    """
    计算当前应还信息（单利，不滚利）。
    当期利息 = 剩余本金 × 日利率% × 距上次计息天数
    """
    last_at = loan.get("last_interest_at") or loan["borrowed_at"]
    days = (now - last_at).days
    daily_rate = float(loan["daily_rate"])

    period_interest = 0
    if days > 0 and loan["remaining_principal"] > 0:
        period_interest = int(loan["remaining_principal"] * daily_rate / 100 * days)

    total_accrued = (loan.get("accrued_interest") or 0) + period_interest
    total_days = (now - loan["borrowed_at"]).days

    return {
        "remaining_principal": loan["remaining_principal"],
        "period_interest": period_interest,
        "accrued_interest": total_accrued,
        "total_owed": loan["remaining_principal"] + total_accrued,
        "days_since_borrow": total_days,
    }


def apply_repayment(loan: dict, payment: int, now: datetime) -> dict:
    """
    还款优先抵本金，本金清零后再抵累计利息。
    返回更新 loan 所需的字段。
    """
    info = calculate_repay_info(loan, now)
    remaining = payment

    principal_paid = min(remaining, loan["remaining_principal"])
    remaining -= principal_paid
    new_principal = loan["remaining_principal"] - principal_paid

    interest_paid = min(remaining, info["accrued_interest"])
    new_accrued = info["accrued_interest"] - interest_paid

    fully_repaid = new_principal == 0 and new_accrued == 0

    return {
        "principal_paid": principal_paid,
        "interest_paid": interest_paid,
        "new_remaining_principal": new_principal,
        "new_accrued_interest": new_accrued,
        "fully_repaid": fully_repaid,
        "total_owed_before": info["total_owed"],
        "period_interest": info["period_interest"],
        "days_since_borrow": info["days_since_borrow"],
    }


def check_loan_eligibility(
    cur, child_id: int, base_max: int, base_interest: float, now: datetime
) -> dict:
    """检查孩子是否有资格贷款。"""
    cur.execute(
        "SELECT id, total_points, credit_score FROM children WHERE id = %s",
        (child_id,),
    )
    child = cur.fetchone()
    if not child:
        return {"eligible": False, "reason": "孩子不存在"}

    credit_score = child["credit_score"] or 100
    limits = calculate_loan_limits(credit_score, base_max, base_interest)

    cur.execute(
        "SELECT COUNT(*) FROM loans WHERE child_id = %s AND status = 'active'",
        (child_id,),
    )
    active_count = cur.fetchone()["count"]

    week_start = now - timedelta(days=now.weekday())
    cur.execute(
        "SELECT COUNT(*) FROM loans WHERE child_id = %s AND borrowed_at >= %s",
        (child_id, week_start),
    )
    weekly_count = cur.fetchone()["count"]

    if active_count > 0:
        return {
            "eligible": False,
            "reason": "请先还清当前贷款",
            "credit_score": credit_score,
            "limits": limits,
            "active_loans_count": active_count,
            "weekly_loan_count": weekly_count,
        }

    if weekly_count >= limits["loans_per_week"]:
        return {
            "eligible": False,
            "reason": f"本周贷款次数已达上限（{limits['loans_per_week']}次）",
            "credit_score": credit_score,
            "limits": limits,
            "active_loans_count": active_count,
            "weekly_loan_count": weekly_count,
        }

    return {
        "eligible": True,
        "credit_score": credit_score,
        "limits": limits,
        "active_loans_count": active_count,
        "weekly_loan_count": weekly_count,
    }


def refresh_single_loan(cur, loan: dict, now: datetime) -> dict:
    """
    结算单条贷款的利息和信用分衰减，写回 DB。
    返回 {"interest": True/False, "credit_decay": True/False} 指示是否有更新。
    """
    result = {"interest": False, "credit_decay": False}

    if loan["status"] != "active" or loan["remaining_principal"] <= 0:
        return result

    # ---- 利息结算 ----
    last_interest_at = loan.get("last_interest_at") or loan["borrowed_at"]
    days_since_interest = (now - last_interest_at).days

    if days_since_interest > 0:
        daily_rate = float(loan["daily_rate"])
        new_interest = int(loan["remaining_principal"] * daily_rate / 100 * days_since_interest)
        new_accrued = (loan.get("accrued_interest") or 0) + new_interest

        cur.execute(
            "UPDATE loans SET accrued_interest = %s, last_interest_at = %s WHERE id = %s",
            (new_accrued, now, loan["id"]),
        )
        loan["accrued_interest"] = new_accrued
        loan["last_interest_at"] = now
        result["interest"] = True

    # ---- 信用分衰减 ----
    # 贷款逾期（超过 cooldown_days）后，每天 -1 信用分
    cur.execute("SELECT credit_score FROM children WHERE id = %s", (loan["child_id"],))
    child = cur.fetchone()
    if not child:
        return result

    credit_score = child["credit_score"] or 100
    level = compute_level(credit_score)
    # 注：衰减使用当前信用分对应的 cooldown（即使它正在下降）
    cooldown_days = 7 if level >= 0 else 7 * (2 ** abs(level))

    overdue_start = loan["borrowed_at"] + timedelta(days=cooldown_days)
    if now <= overdue_start:
        return result  # 还没逾期

    last_decay_at = loan.get("last_credit_decay_at") or overdue_start
    decay_days = (now - last_decay_at).days

    if decay_days > 0:
        cur.execute(
            "UPDATE children SET credit_score = GREATEST(0, credit_score - %s) WHERE id = %s",
            (decay_days, loan["child_id"]),
        )
        cur.execute(
            "UPDATE loans SET last_credit_decay_at = %s WHERE id = %s",
            (now, loan["id"]),
        )
        loan["last_credit_decay_at"] = now
        result["credit_decay"] = True

    return result


def refresh_loans(cur, now: datetime) -> dict:
    """遍历所有 active 贷款结算利息和信用分衰减，返回统计信息。"""
    cur.execute(
        "SELECT l.*, c.credit_score"
        " FROM loans l JOIN children c ON l.child_id = c.id"
        " WHERE l.status = 'active'"
    )
    loans = cur.fetchall()

    stats = {"total_active": len(loans), "interest_updated": 0, "credit_decayed": 0}

    for loan in loans:
        r = refresh_single_loan(cur, loan, now)
        if r["interest"]:
            stats["interest_updated"] += 1
        if r["credit_decay"]:
            stats["credit_decayed"] += 1

    return stats
