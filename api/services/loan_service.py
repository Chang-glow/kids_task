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
