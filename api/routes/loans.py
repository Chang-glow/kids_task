"""贷款路由：借款、还款、状态查询，按 group_id 隔离。"""

import json

from fastapi import APIRouter, HTTPException, Depends
from api.dependencies import get_group_id
from api.models.database import get_db
from api.models.schemas import BorrowRequest, RepayRequest
from api.config import now_cst
from api.services.loan_service import (
    get_interest_rate,
    get_max_amount,
    calculate_credit_change,
    calculate_repay_info,
    apply_repayment,
    check_loan_eligibility,
)

router = APIRouter(prefix="/api/loans", tags=["loans"])


@router.get("")
def list_loans(group_id: int = Depends(get_group_id)):
    """列出该群组所有贷款（含孩子信息、当前应还总额）。"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT l.*, c.name AS child_name, c.emoji AS child_emoji"
        " FROM loans l JOIN children c ON l.child_id = c.id"
        " WHERE l.group_id = %s"
        " ORDER BY l.borrowed_at DESC",
        (group_id,),
    )
    loans = cur.fetchall()
    conn.close()

    now = now_cst()
    result = []
    for loan in loans:
        info = calculate_repay_info(loan, now)
        d = dict(loan)
        d["total_owed"] = info["total_owed"]
        d["accrued_interest"] = info["accrued_interest"]
        result.append(d)
    return result


@router.get("/status")
def loan_status(group_id: int = Depends(get_group_id)):
    """每个孩子的贷款资格状态。"""
    conn = get_db()
    cur = conn.cursor()
    now = now_cst()
    base_max = get_max_amount(cur)
    base_interest = get_interest_rate(cur)

    cur.execute(
        "SELECT id, name, total_points, credit_score FROM children WHERE group_id = %s ORDER BY id",
        (group_id,),
    )
    children = cur.fetchall()
    results = []
    for child in children:
        eligibility = check_loan_eligibility(cur, child["id"], base_max, base_interest, now)
        eligibility["child_id"] = child["id"]
        eligibility["child_name"] = child["name"]
        eligibility["total_points"] = child["total_points"]
        results.append(eligibility)
    conn.close()
    return results


@router.post("")
def borrow(req: BorrowRequest, group_id: int = Depends(get_group_id)):
    """借款：校验资格 → 加积分 → 写日志 + 撤回记录。"""
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="借款金额必须大于0")

    conn = get_db()
    cur = conn.cursor()
    try:
        child_id = req.child_id
        if not child_id:
            cur.execute(
                "SELECT id FROM children WHERE group_id = %s ORDER BY id LIMIT 1",
                (group_id,),
            )
            child_row = cur.fetchone()
            if not child_row:
                raise HTTPException(status_code=400, detail="群组中没有孩子")
            child_id = child_row["id"]

        now = now_cst()
        base_max = get_max_amount(cur)
        base_interest = get_interest_rate(cur)
        eligibility = check_loan_eligibility(cur, child_id, base_max, base_interest, now)

        if not eligibility["eligible"]:
            raise HTTPException(status_code=400, detail=eligibility["reason"])

        if req.amount > eligibility["limits"]["max_amount"]:
            raise HTTPException(
                status_code=400,
                detail=f"贷款金额超过上限（当前最多 {eligibility['limits']['max_amount']} 分）",
            )

        cur.execute(
            "INSERT INTO loans (group_id, child_id, amount, remaining_principal, daily_rate,"
            " accrued_interest, last_interest_at, borrowed_at, status, created_at)"
            " VALUES (%s, %s, %s, %s, %s, 0, %s, %s, 'active', %s) RETURNING id",
            (group_id, child_id, req.amount, req.amount, base_interest, now, now, now),
        )
        loan_id = cur.fetchone()["id"]

        cur.execute(
            "UPDATE children SET total_points = total_points + %s WHERE id = %s",
            (req.amount, child_id),
        )

        description = f"💸 贷款 {req.amount} 分（日利率 {base_interest}%）"
        cur.execute(
            "INSERT INTO point_logs (action, amount, description, created_at, group_id, child_id)"
            " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            ("earn", req.amount, description, now, group_id, child_id),
        )
        log_id = cur.fetchone()["id"]

        cur.execute(
            "INSERT INTO undo_operations (group_id, child_id, operation_type, description, undo_data, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (group_id, child_id, "borrow_loan", description,
             json.dumps({"loan_id": loan_id, "amount": req.amount, "log_id": log_id,
                         "child_id": child_id}), now),
        )

        conn.commit()

        cur.execute("SELECT total_points FROM children WHERE id = %s", (child_id,))
        child = cur.fetchone()

        return {
            "success": True,
            "loan_id": loan_id,
            "amount": req.amount,
            "total_points": child["total_points"],
            "message": f"贷款成功！借到 {req.amount} 分 💸",
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise HTTPException(status_code=500, detail="服务器内部错误")
    finally:
        conn.close()


@router.post("/{loan_id}/repay")
def repay(loan_id: int, req: RepayRequest, group_id: int = Depends(get_group_id)):
    """还款：优先抵本金 → 剩余抵利息。支持部分还款。"""
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="还款金额必须大于0")

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT * FROM loans WHERE id = %s AND group_id = %s",
            (loan_id, group_id),
        )
        loan = cur.fetchone()
        if not loan:
            raise HTTPException(status_code=404, detail="贷款记录不存在")
        if loan["status"] != "active":
            raise HTTPException(status_code=400, detail="该贷款已经还清")

        child_id = loan["child_id"]
        cur.execute(
            "SELECT id, total_points, credit_score, name FROM children WHERE id = %s",
            (child_id,),
        )
        child = cur.fetchone()

        now = now_cst()
        result = apply_repayment(loan, req.amount, now)

        if child["total_points"] < req.amount:
            raise HTTPException(
                status_code=400,
                detail=f"积分不足！需要还 {req.amount} 分，当前只有 {child['total_points']} 分",
            )

        # 扣积分
        cur.execute(
            "UPDATE children SET total_points = total_points - %s WHERE id = %s",
            (req.amount, child_id),
        )

        # 更新贷款记录
        new_status = "repaid" if result["fully_repaid"] else "active"
        repaid_at = now if result["fully_repaid"] else None
        cur.execute(
            "UPDATE loans SET remaining_principal = %s, accrued_interest = %s,"
            " last_interest_at = %s, status = %s, repaid_at = %s WHERE id = %s",
            (result["new_remaining_principal"], result["new_accrued_interest"],
             now, new_status, repaid_at, loan_id),
        )

        # 全额还清时更新信用分
        credit_change = 0
        new_credit_score = child["credit_score"] or 100
        if result["fully_repaid"]:
            credit_change = calculate_credit_change(loan["borrowed_at"], now)
            new_credit_score = max(0, (child["credit_score"] or 100) + credit_change)
            cur.execute(
                "UPDATE children SET credit_score = %s WHERE id = %s",
                (new_credit_score, child_id),
            )

        cur.execute("SELECT total_points FROM children WHERE id = %s", (child_id,))
        child_after = cur.fetchone()

        # 流水
        desc_parts = [f"💸 还贷款 {req.amount} 分"]
        if result["principal_paid"] > 0:
            desc_parts.append(f"（本金 {result['principal_paid']}")
        if result["interest_paid"] > 0:
            desc_parts.append(f"利息 {result['interest_paid']}")
        desc_parts.append(f"日利率 {float(loan['daily_rate'])}%")
        if result["fully_repaid"]:
            desc_parts.append(f"信用分 {credit_change:+d}")
        description = f"{' '.join(desc_parts)}）"

        cur.execute(
            "INSERT INTO point_logs (action, amount, description, created_at, group_id, child_id)"
            " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            ("spend", req.amount, description, now, group_id, child_id),
        )
        log_id = cur.fetchone()["id"]

        cur.execute(
            "INSERT INTO undo_operations (group_id, child_id, operation_type, description, undo_data, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (group_id, child_id, "repay_loan", description,
             json.dumps({
                 "loan_id": loan_id,
                 "repay_amount": req.amount,
                 "principal_paid": result["principal_paid"],
                 "interest_paid": result["interest_paid"],
                 "credit_change": credit_change,
                 "previous_credit_score": child["credit_score"] or 100,
                 "previous_remaining_principal": loan["remaining_principal"],
                 "previous_accrued_interest": loan["accrued_interest"] or 0,
                 "log_id": log_id,
                 "child_id": child_id,
             }), now),
        )

        conn.commit()

        return {
            "success": True,
            "principal_paid": result["principal_paid"],
            "interest_paid": result["interest_paid"],
            "new_remaining_principal": result["new_remaining_principal"],
            "new_accrued_interest": result["new_accrued_interest"],
            "fully_repaid": result["fully_repaid"],
            "credit_score_change": credit_change,
            "new_credit_score": new_credit_score,
            "total_points": child_after["total_points"],
            "message": "贷款已还清！" if result["fully_repaid"] else f"已还 {req.amount} 分",
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise HTTPException(status_code=500, detail="服务器内部错误")
    finally:
        conn.close()
