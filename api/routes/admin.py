"""Admin 路由：认证 + 群组/孩子管理 + 积分修改 + 日志管理 + 撤回操作。"""

import json
import traceback
from datetime import datetime

from fastapi import APIRouter, HTTPException, Header, Depends
from api.admin_auth import hash_password, verify_password, generate_token, validate_token
from api.models.database import get_db
from api.config import now_cst, set_simulated_time, get_simulated_time

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _require_admin(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    token = authorization[len("Bearer "):]
    if not validate_token(token):
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    return token


# ---- 密码状态 / 设置 / 登录 / 重置 ----

@router.get("/status")
def admin_status():
    """检查是否已设置 admin 密码（password_hash 或 password_hash_test）。"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT key FROM admin_settings WHERE key IN ('password_hash', 'password_hash_test')")
    row = cur.fetchone()
    conn.close()
    return {"configured": row is not None}


@router.post("/setup")
def admin_setup(req: dict):
    """首次设置 admin 密码（或重置后重新设置）。"""
    password = req.get("password", "")
    if len(password) < 4:
        raise HTTPException(status_code=400, detail="密码至少4位")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT key FROM admin_settings WHERE key = 'password_hash'")
    if cur.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="密码已设置，请直接登录")

    h = hash_password(password)
    cur.execute("INSERT INTO admin_settings (key, value) VALUES ('password_hash', %s)", (h,))
    conn.commit()
    conn.close()

    token = generate_token()
    return {"success": True, "token": token}


@router.post("/reset")
def admin_reset(req: dict):
    """重置密码：需提供旧密码验证，清空所有密码哈希后回到首次设置状态。"""
    old_password = req.get("old_password", "")
    if len(old_password) < 4:
        raise HTTPException(status_code=400, detail="请输入旧密码")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT key, value FROM admin_settings WHERE key IN ('password_hash', 'password_hash_test')")
    rows = cur.fetchall()
    if not rows:
        conn.close()
        raise HTTPException(status_code=400, detail="未设置密码")

    if not any(verify_password(old_password, r["value"]) for r in rows):
        conn.close()
        raise HTTPException(status_code=401, detail="旧密码错误")

    cur.execute("DELETE FROM admin_settings WHERE key IN ('password_hash', 'password_hash_test')")
    conn.commit()
    conn.close()
    return {"success": True}


@router.post("/change-password")
def admin_change_password(req: dict, _token: str = Depends(_require_admin)):
    """登录后修改密码，无需旧密码（token 已证明身份）。"""
    new_password = req.get("new_password", "")
    if len(new_password) < 4:
        raise HTTPException(status_code=400, detail="新密码至少4位")
    conn = get_db()
    cur = conn.cursor()
    h = hash_password(new_password)
    cur.execute(
        "INSERT INTO admin_settings (key, value) VALUES ('password_hash', %s)"
        " ON CONFLICT (key) DO UPDATE SET value = %s",
        (h, h),
    )
    conn.commit()
    conn.close()
    return {"success": True}


@router.post("/login")
def admin_login(req: dict):
    """登录。兼容生产密码（password_hash）和测试密码（password_hash_test）。"""
    password = req.get("password", "")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT key, value FROM admin_settings WHERE key IN ('password_hash', 'password_hash_test')")
    rows = cur.fetchall()

    if not rows:
        conn.close()
        raise HTTPException(status_code=400, detail="请先设置密码")

    matched_key = None
    matched_value = None
    for row in rows:
        if verify_password(password, row["value"]):
            matched_key = row["key"]
            matched_value = row["value"]
            break

    if matched_key is None:
        conn.close()
        raise HTTPException(status_code=401, detail="密码错误")

    # 自动升级旧版 SHA256 哈希到 PBKDF2
    if ":" not in matched_value:
        cur.execute(
            "UPDATE admin_settings SET value = %s WHERE key = %s",
            (hash_password(password), matched_key),
        )
        conn.commit()

    conn.close()
    token = generate_token()
    return {"success": True, "token": token}


# ---- 群组 & 孩子总览 ----

@router.get("/groups")
def admin_groups(_token: str = Depends(_require_admin)):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM family_groups ORDER BY id")
    groups = [dict(g) for g in cur.fetchall()]

    for g in groups:
        cur.execute("SELECT id, name, emoji, total_points FROM children WHERE group_id = %s ORDER BY id", (g["id"],))
        g["children"] = [dict(c) for c in cur.fetchall()]

    conn.close()
    return groups


# ---- 积分修改 ----

@router.post("/points")
def admin_points(req: dict, _token: str = Depends(_require_admin)):
    """直接修改孩子积分，支持 set（设为指定值）和 delta（增减）两种模式"""
    child_id = req.get("child_id")
    group_id = req.get("group_id")
    mode = req.get("mode", "set")
    description = req.get("description", "")

    if not child_id or not group_id:
        raise HTTPException(status_code=400, detail="缺少 child_id 或 group_id")
    if mode not in ("set", "delta"):
        raise HTTPException(status_code=400, detail="mode 必须是 set 或 delta")

    try:
        value = int(req.get("value", 0))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="value 必须是整数")

    if mode == "set" and (value < 0 or value > 99999):
        raise HTTPException(status_code=400, detail="积分范围 0 ~ 99999")
    if mode == "delta" and (value < -99999 or value > 99999):
        raise HTTPException(status_code=400, detail="增减范围 -99999 ~ 99999")

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM children WHERE id = %s AND group_id = %s", (child_id, group_id))
        child = cur.fetchone()
        if not child:
            raise HTTPException(status_code=404, detail="孩子不存在")

        previous_points = child["total_points"]

        if mode == "set":
            new_points = value
        else:
            new_points = max(0, previous_points + value)

        amount = new_points - previous_points
        cur.execute("UPDATE children SET total_points = %s WHERE id = %s", (new_points, child_id))

        now = now_cst()
        action = "earn" if amount >= 0 else "spend"
        log_desc = description or f"管理员{'增加' if amount >= 0 else '减少'}积分 {amount:+d} → {new_points}分"
        cur.execute(
            "INSERT INTO point_logs (action, amount, description, created_at, group_id, child_id)"
            " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (action, abs(amount), log_desc, now, group_id, child_id),
        )
        log_id = cur.fetchone()["id"]

        undo_data = json.dumps({
            "child_id": child_id,
            "previous_points": previous_points,
            "log_ids": [log_id],
        })
        cur.execute(
            "INSERT INTO undo_operations (group_id, child_id, operation_type, description, undo_data, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (group_id, child_id, "manual_edit", log_desc, undo_data, now),
        )

        conn.commit()
        return {"success": True, "new_points": new_points, "previous_points": previous_points}
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="服务器内部错误")
    finally:
        conn.close()


# ---- 日志管理 ----

@router.post("/logs")
def admin_add_log(req: dict, _token: str = Depends(_require_admin)):
    """手动添加流水日志"""
    child_id = req.get("child_id")
    group_id = req.get("group_id")
    action = req.get("action", "earn")
    amount = int(req.get("amount", 0))
    description = req.get("description", "")

    if not child_id or not group_id:
        raise HTTPException(status_code=400, detail="缺少 child_id 或 group_id")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="amount 必须大于0")
    if action not in ("earn", "spend", "punish"):
        raise HTTPException(status_code=400, detail="action 必须是 earn, spend 或 punish")

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM children WHERE id = %s AND group_id = %s", (child_id, group_id))
        child = cur.fetchone()
        if not child:
            raise HTTPException(status_code=404, detail="孩子不存在")

        now = now_cst()
        if action == "earn":
            cur.execute("UPDATE children SET total_points = total_points + %s WHERE id = %s", (amount, child_id))
        else:
            cur.execute("SELECT total_points FROM children WHERE id = %s", (child_id,))
            current = cur.fetchone()["total_points"]
            actual = min(amount, current)
            cur.execute("UPDATE children SET total_points = total_points - %s WHERE id = %s", (actual, child_id))

        cur.execute(
            "INSERT INTO point_logs (action, amount, description, created_at, group_id, child_id)"
            " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (action, amount, description, now, group_id, child_id),
        )
        log_id = cur.fetchone()["id"]

        undo_data = json.dumps({"log_id": log_id})
        cur.execute(
            "INSERT INTO undo_operations (group_id, child_id, operation_type, description, undo_data, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (group_id, child_id, "manual_log_add", description, undo_data, now),
        )

        conn.commit()
        return {"success": True, "log_id": log_id}
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="服务器内部错误")
    finally:
        conn.close()


@router.delete("/logs/{log_id}")
def admin_delete_log(log_id: int, _token: str = Depends(_require_admin)):
    """软删除流水日志并逆转积分影响"""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM point_logs WHERE id = %s", (log_id,))
        log = cur.fetchone()
        if not log:
            raise HTTPException(status_code=404, detail="日志不存在")
        if log["undone"]:
            raise HTTPException(status_code=400, detail="该日志已被撤销")

        if log["action"] == "earn":
            cur.execute(
                "UPDATE children SET total_points = GREATEST(0, total_points - %s) WHERE id = %s",
                (log["amount"], log["child_id"]),
            )
        else:
            cur.execute(
                "UPDATE children SET total_points = total_points + %s WHERE id = %s",
                (log["amount"], log["child_id"]),
            )

        cur.execute("UPDATE point_logs SET undone = true WHERE id = %s", (log_id,))

        now = now_cst()
        log_data = dict(log)
        for key in list(log_data):
            if hasattr(log_data[key], 'isoformat'):
                log_data[key] = log_data[key].isoformat()
        undo_data = json.dumps({"log_data": log_data})
        cur.execute(
            "INSERT INTO undo_operations (group_id, child_id, operation_type, description, undo_data, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (log["group_id"], log["child_id"], "manual_log_delete",
             f"删除日志: {log['description']}", undo_data, now),
        )

        conn.commit()
        return {"success": True}
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="服务器内部错误")
    finally:
        conn.close()


# ---- 操作历史 & 撤回 ----

@router.get("/operations")
def admin_operations(group_id: int, _token: str = Depends(_require_admin)):
    """列出可撤回操作（未撤回的，最新优先）"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM undo_operations WHERE group_id = %s AND undone_at IS NULL"
        " ORDER BY created_at DESC LIMIT 100",
        (group_id,),
    )
    ops = [dict(o) for o in cur.fetchall()]
    for op in ops:
        if isinstance(op["undo_data"], str):
            op["undo_data"] = json.loads(op["undo_data"])
    conn.close()
    return ops


@router.post("/undo/{operation_id}")
def admin_undo(operation_id: int, _token: str = Depends(_require_admin)):
    """撤回指定操作"""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM undo_operations WHERE id = %s", (operation_id,))
        op = cur.fetchone()
        if not op:
            raise HTTPException(status_code=404, detail="操作不存在")
        if op["undone_at"] is not None:
            raise HTTPException(status_code=400, detail="该操作已被撤回")

        undo_data = op["undo_data"] if isinstance(op["undo_data"], dict) else json.loads(op["undo_data"])
        op_type = op["operation_type"]
        now = now_cst()

        if op_type == "manual_edit":
            child_id = undo_data["child_id"]
            cur.execute(
                "UPDATE children SET total_points = %s WHERE id = %s",
                (undo_data["previous_points"], child_id),
            )
            for lid in undo_data.get("log_ids", []):
                cur.execute("UPDATE point_logs SET undone = true WHERE id = %s", (lid,))

        elif op_type == "manual_log_add":
            log_id = undo_data["log_id"]
            cur.execute("SELECT * FROM point_logs WHERE id = %s", (log_id,))
            log = cur.fetchone()
            if log and not log["undone"]:
                if log["action"] == "earn":
                    cur.execute(
                        "UPDATE children SET total_points = GREATEST(0, total_points - %s) WHERE id = %s",
                        (log["amount"], log["child_id"]),
                    )
                else:
                    cur.execute(
                        "UPDATE children SET total_points = total_points + %s WHERE id = %s",
                        (log["amount"], log["child_id"]),
                    )
                cur.execute("UPDATE point_logs SET undone = true WHERE id = %s", (log_id,))

        elif op_type == "manual_log_delete":
            log_data = undo_data["log_data"]
            if log_data["action"] == "earn":
                cur.execute(
                    "UPDATE children SET total_points = total_points + %s WHERE id = %s",
                    (log_data["amount"], log_data["child_id"]),
                )
            else:
                cur.execute(
                    "UPDATE children SET total_points = GREATEST(0, total_points - %s) WHERE id = %s",
                    (log_data["amount"], log_data["child_id"]),
                )
            cur.execute("UPDATE point_logs SET undone = false WHERE id = %s", (log_data["id"],))

        elif op_type == "task_complete":
            cur.execute(
                "UPDATE tasks SET status = %s, completed_at = NULL WHERE id = %s",
                (undo_data["previous_status"], undo_data["task_id"]),
            )
            cur.execute(
                "UPDATE children SET total_points = GREATEST(0, total_points - %s) WHERE id = %s",
                (undo_data["points_added"], undo_data["child_id"]),
            )
            cur.execute("UPDATE point_logs SET undone = true WHERE id = %s", (undo_data["log_id"],))
            for c in undo_data.get("conditions", []):
                cur.execute(
                    "UPDATE child_condition_acceptances SET passed = NULL WHERE condition_id = %s",
                    (c["condition_id"],),
                )

        elif op_type == "redeem_reward":
            cur.execute(
                "UPDATE children SET total_points = total_points + %s WHERE id = %s",
                (undo_data["cost"], undo_data["child_id"]),
            )
            cur.execute("UPDATE point_logs SET undone = true WHERE id = %s", (undo_data["log_id"],))

        elif op_type == "punish":
            cur.execute(
                "UPDATE children SET total_points = total_points + %s WHERE id = %s",
                (undo_data["actual_deducted"], undo_data["child_id"]),
            )
            cur.execute("UPDATE point_logs SET undone = true WHERE id = %s", (undo_data["log_id"],))

        elif op_type == "borrow_loan":
            cur.execute("DELETE FROM loans WHERE id = %s", (undo_data["loan_id"],))
            cur.execute(
                "UPDATE children SET total_points = GREATEST(0, total_points - %s) WHERE id = %s",
                (undo_data["amount"], undo_data["child_id"]),
            )
            cur.execute("UPDATE point_logs SET undone = true WHERE id = %s", (undo_data["log_id"],))

        elif op_type == "repay_loan":
            cur.execute(
                "UPDATE loans SET remaining_principal = %s, accrued_interest = %s,"
                " status = 'active', repaid_at = NULL WHERE id = %s",
                (undo_data["previous_remaining_principal"], undo_data["previous_accrued_interest"],
                 undo_data["loan_id"]),
            )
            cur.execute(
                "UPDATE children SET total_points = total_points + %s WHERE id = %s",
                (undo_data["repay_amount"], undo_data["child_id"]),
            )
            if undo_data.get("credit_change"):
                cur.execute(
                    "UPDATE children SET credit_score = %s WHERE id = %s",
                    (undo_data["previous_credit_score"], undo_data["child_id"]),
                )
            cur.execute("UPDATE point_logs SET undone = true WHERE id = %s", (undo_data["log_id"],))

        elif op_type == "boost_override_change":
            from api.services.boost_service import set_boost_override
            prev = undo_data.get("previous_override")
            if prev:
                set_boost_override(cur, op["group_id"], prev["task_id"],
                                   prev["override_type"], prev.get("manual_multiplier"), now)
            else:
                set_boost_override(cur, op["group_id"], undo_data["task_id"], "none", None, now)

        cur.execute("UPDATE undo_operations SET undone_at = %s WHERE id = %s", (now, operation_id))
        conn.commit()
        return {"success": True}
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="服务器内部错误")
    finally:
        conn.close()


# ---- 任务 & 奖励查询 ----

@router.get("/tasks")
def admin_tasks(group_id: int, _token: str = Depends(_require_admin)):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM tasks WHERE group_id = %s ORDER BY created_at DESC", (group_id,))
    tasks = [dict(t) for t in cur.fetchall()]
    conn.close()
    return tasks


@router.get("/rewards")
def admin_rewards(group_id: int, _token: str = Depends(_require_admin)):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM rewards WHERE group_id = %s ORDER BY cost_points ASC", (group_id,))
    rewards = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rewards


# ---- 贷款设置 ----

@router.get("/loan-settings")
def admin_get_loan_settings(_token: str = Depends(_require_admin)):
    """获取贷款设置（日利率、最高额度）。"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT key, value FROM admin_settings WHERE key IN ('loan_interest_rate', 'loan_max_amount')"
    )
    rows = cur.fetchall()
    conn.close()
    settings = {r["key"]: r["value"] for r in rows}
    return {
        "interest_rate": float(settings.get("loan_interest_rate", "5")),
        "max_amount": int(settings.get("loan_max_amount", "200")),
    }


@router.post("/loan-settings")
def admin_save_loan_settings(req: dict, _token: str = Depends(_require_admin)):
    """保存贷款设置。"""
    interest_rate = req.get("interest_rate")
    max_amount = req.get("max_amount")

    if interest_rate is not None:
        try:
            rate = float(interest_rate)
            if rate < 0 or rate > 100:
                raise HTTPException(status_code=400, detail="利率必须在 0-100% 之间")
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="利率格式无效")

    if max_amount is not None:
        try:
            amount = int(max_amount)
            if amount < 1 or amount > 99999:
                raise HTTPException(status_code=400, detail="最高额度范围 1-99999")
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="额度格式无效")

    conn = get_db()
    cur = conn.cursor()
    try:
        if interest_rate is not None:
            cur.execute(
                "INSERT INTO admin_settings (key, value) VALUES ('loan_interest_rate', %s)"
                " ON CONFLICT (key) DO UPDATE SET value = %s",
                (str(rate), str(rate)),
            )
        if max_amount is not None:
            cur.execute(
                "INSERT INTO admin_settings (key, value) VALUES ('loan_max_amount', %s)"
                " ON CONFLICT (key) DO UPDATE SET value = %s",
                (str(amount), str(amount)),
            )
        conn.commit()
        return {"success": True}
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise HTTPException(status_code=500, detail="服务器内部错误")
    finally:
        conn.close()


# ---- 删除群组 & 孩子 ----

@router.delete("/groups/{group_id}")
def admin_delete_group(group_id: int, _token: str = Depends(_require_admin)):
    """删除群组及其所有关联数据（孩子、任务、奖励、流水、撤回记录）。"""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM family_groups WHERE id = %s", (group_id,))
        if not cur.fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail="群组不存在")

        cur.execute("DELETE FROM undo_operations WHERE group_id = %s", (group_id,))
        cur.execute("DELETE FROM point_logs WHERE group_id = %s", (group_id,))
        cur.execute("DELETE FROM loans WHERE group_id = %s", (group_id,))
        cur.execute("DELETE FROM tasks WHERE group_id = %s", (group_id,))
        cur.execute("DELETE FROM rewards WHERE group_id = %s", (group_id,))
        cur.execute("DELETE FROM children WHERE group_id = %s", (group_id,))
        cur.execute("DELETE FROM family_groups WHERE id = %s", (group_id,))
        conn.commit()
        return {"success": True}
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="服务器内部错误")
    finally:
        conn.close()


@router.delete("/children/{child_id}")
def admin_delete_child(child_id: int, _token: str = Depends(_require_admin)):
    """删除孩子及其关联的流水和撤回记录。"""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, group_id FROM children WHERE id = %s", (child_id,))
        child = cur.fetchone()
        if not child:
            conn.close()
            raise HTTPException(status_code=404, detail="孩子不存在")

        cur.execute("DELETE FROM undo_operations WHERE child_id = %s", (child_id,))
        cur.execute("DELETE FROM point_logs WHERE child_id = %s", (child_id,))
        cur.execute("DELETE FROM loans WHERE child_id = %s", (child_id,))
        cur.execute("UPDATE tasks SET child_id = NULL WHERE child_id = %s", (child_id,))
        cur.execute("DELETE FROM children WHERE id = %s", (child_id,))
        conn.commit()
        return {"success": True}
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="服务器内部错误")
    finally:
        conn.close()


@router.get("/simulated-time")
def admin_get_simulated_time(_token: str = Depends(_require_admin)):
    """获取当前模拟时间设置。"""
    t = get_simulated_time()
    return {
        "simulated": t is not None,
        "time": t.isoformat() if t else None,
        "real_time": datetime.now().isoformat(),
    }


@router.post("/simulated-time")
def admin_set_simulated_time(req: dict, _token: str = Depends(_require_admin)):
    """
    设置/清除模拟时间。
    body: {"time": "2026-07-25 12:00:00"} 或 {"time": null} 清除。
    """
    conn = get_db()
    cur = conn.cursor()
    try:
        time_str = req.get("time") if req else None

        if time_str is None:
            # 清除模拟时间
            cur.execute("DELETE FROM admin_settings WHERE key = 'simulated_time'")
            set_simulated_time(None)
            conn.commit()
            return {"success": True, "simulated": False, "message": "已恢复真实时间"}

        t = datetime.fromisoformat(time_str)
        set_simulated_time(t)

        cur.execute(
            "INSERT INTO admin_settings (key, value) VALUES ('simulated_time', %s)"
            " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (time_str,),
        )
        conn.commit()
        return {"success": True, "simulated": True, "time": t.isoformat(),
                "message": f"模拟时间已设置为 {time_str}"}
    except ValueError:
        raise HTTPException(status_code=400, detail="时间格式错误，请使用 YYYY-MM-DD HH:MM:SS")
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="服务器内部错误")
    finally:
        conn.close()


# ---- 限时翻倍设置 ----


@router.get("/boost-overrides")
def admin_get_boost_overrides(group_id: int, _token: str = Depends(_require_admin)):
    """列出群组的所有翻倍覆盖设置。"""
    from api.services.boost_service import get_boost_overrides
    conn = get_db()
    cur = conn.cursor()
    rows = get_boost_overrides(cur, group_id)
    conn.close()
    return rows


@router.post("/boost-overrides")
def admin_set_boost_override(req: dict, _token: str = Depends(_require_admin)):
    """设置翻倍覆盖。"""
    from api.services.boost_service import set_boost_override, get_boost_overrides
    group_id = req.get("group_id")
    task_id = req.get("task_id")
    override_type = req.get("override_type", "")
    manual_multiplier = req.get("manual_multiplier")

    if not group_id or not task_id:
        raise HTTPException(status_code=400, detail="缺少 group_id 或 task_id")
    if override_type not in ("lock_in", "lock_out", "manual_multiplier", "none"):
        raise HTTPException(status_code=400, detail="override_type 无效")
    if override_type == "manual_multiplier" and (manual_multiplier is None or float(manual_multiplier) <= 0):
        raise HTTPException(status_code=400, detail="manual_multiplier 必须大于0")

    conn = get_db()
    cur = conn.cursor()
    try:
        # 保存旧状态用于撤回
        old_overrides = get_boost_overrides(cur, group_id)
        old_override = next((o for o in old_overrides if o["task_id"] == task_id), None)
        if old_override:
            for k in list(old_override):
                if hasattr(old_override[k], 'isoformat'):
                    old_override[k] = old_override[k].isoformat()

        now = now_cst()
        result = set_boost_override(cur, group_id, task_id, override_type,
                                    float(manual_multiplier) if manual_multiplier else None, now)
        cur.execute(
            "INSERT INTO undo_operations (group_id, operation_type, description, undo_data, created_at)"
            " VALUES (%s, %s, %s, %s, %s)",
            (group_id, "boost_override_change",
             f"翻倍覆盖: task_id={task_id} {override_type}",
             json.dumps({"task_id": task_id, "previous_override": old_override}), now),
        )
        # 清除当天翻倍缓存，下次访问时按新 override 重新生成
        cur.execute(
            "DELETE FROM daily_task_boosts WHERE group_id = %s AND boost_date = %s",
            (group_id, now.date()),
        )
        conn.commit()
        return result
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="服务器内部错误")
    finally:
        conn.close()


# ---- 悬赏附加条件管理 ----


@router.get("/conditions")
def admin_get_conditions(group_id: int, _token: str = Depends(_require_admin)):
    """列出群组的所有条件（含绑定任务名称）。"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT c.*, array_agg(t.name ORDER BY t.name) AS task_names"
        " FROM conditions c"
        " LEFT JOIN condition_task_bindings ctb ON c.id = ctb.condition_id"
        " LEFT JOIN tasks t ON ctb.task_id = t.id"
        " WHERE c.group_id = %s"
        " GROUP BY c.id ORDER BY c.created_at DESC",
        (group_id,),
    )
    rows = []
    for r in cur.fetchall():
        d = dict(r)
        d["task_names"] = r["task_names"] if r["task_names"] != [None] else []
        rows.append(d)
    conn.close()
    return rows


@router.post("/conditions")
def admin_create_condition(req: dict, _token: str = Depends(_require_admin)):
    """创建新条件并绑定任务。"""
    from api.services.condition_service import create_condition
    group_id = req.get("group_id")
    name = req.get("name", "").strip()
    reward_type = req.get("reward_type", "")
    bonus_value = req.get("bonus_value")
    multiplier_value = req.get("multiplier_value")
    task_ids = req.get("task_ids", [])

    if not group_id:
        raise HTTPException(status_code=400, detail="缺少 group_id")
    if not name:
        raise HTTPException(status_code=400, detail="条件名称不能为空")
    if reward_type not in ("bonus_points", "multiplier", "both"):
        raise HTTPException(status_code=400, detail="reward_type 无效")
    if reward_type in ("bonus_points", "both"):
        bv = int(bonus_value or 0)
        if bv < 5 or bv > 50 or bv % 5 != 0:
            raise HTTPException(status_code=400, detail="bonus_value 需为 5-50，步长 5")
    if reward_type in ("multiplier", "both"):
        mv = float(multiplier_value or 0)
        if mv < 1.25 or mv > 5.0:
            raise HTTPException(status_code=400, detail="multiplier_value 需为 1.25-5.0，步长 0.25")

    conn = get_db()
    cur = conn.cursor()
    try:
        result = create_condition(cur, group_id, name, reward_type,
                                  int(bonus_value) if bonus_value else None,
                                  float(multiplier_value) if multiplier_value else None,
                                  task_ids, now_cst())
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="服务器内部错误")
    finally:
        conn.close()


@router.delete("/conditions/{condition_id}")
def admin_delete_condition(condition_id: int, _token: str = Depends(_require_admin)):
    """删除条件（级联清除绑定和每日选择）。"""
    from api.services.condition_service import delete_condition
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT group_id, name FROM conditions WHERE id = %s", (condition_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="条件不存在")
    group_id = row["group_id"]
    try:
        delete_condition(cur, condition_id, group_id)
        conn.commit()
        return {"success": True}
    except Exception:
        conn.rollback()
        raise HTTPException(status_code=500, detail="服务器内部错误")
    finally:
        conn.close()


# ---- 跨群组任务管理 ----


@router.post("/groups/{group_id}/tasks")
def admin_add_task(group_id: int, req: dict, _token: str = Depends(_require_admin)):
    """Admin 为指定群组添加任务。"""
    name = req.get("name", "").strip()
    emoji = req.get("emoji", "📖")
    base_points = int(req.get("base_points", 20))
    is_repeatable = req.get("is_repeatable", False)
    child_id = req.get("child_id")

    if not name or base_points <= 0:
        raise HTTPException(status_code=400, detail="任务名称和积分不能为空")
    conn = get_db()
    cur = conn.cursor()
    now = now_cst()
    cur.execute(
        "INSERT INTO tasks (name, emoji, base_points, status, is_repeatable, created_at, group_id, child_id)"
        " VALUES (%s, %s, %s, 'pending', %s, %s, %s, %s) RETURNING id",
        (name, emoji, base_points, is_repeatable, now, group_id, child_id),
    )
    task_id = cur.fetchone()["id"]
    conn.commit()
    cur.execute("SELECT * FROM tasks WHERE id = %s", (task_id,))
    task = cur.fetchone()
    conn.close()
    return dict(task)


@router.delete("/groups/{group_id}/tasks/{task_id}")
def admin_delete_task(group_id: int, task_id: int, _token: str = Depends(_require_admin)):
    """Admin 删除指定群组的任务。"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM tasks WHERE id = %s AND group_id = %s", (task_id, group_id))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="任务不存在")
    cur.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
    conn.commit()
    conn.close()
    return {"success": True}


# ---- 跨群组奖励管理 ----


@router.post("/groups/{group_id}/rewards")
def admin_add_reward(group_id: int, req: dict, _token: str = Depends(_require_admin)):
    """Admin 为指定群组添加奖励。"""
    name = req.get("name", "").strip()
    emoji = req.get("emoji", "📺")
    cost_points = int(req.get("cost_points", 50))

    if not name or cost_points <= 0:
        raise HTTPException(status_code=400, detail="奖励名称和积分不能为空")
    conn = get_db()
    cur = conn.cursor()
    now = now_cst()
    cur.execute(
        "INSERT INTO rewards (name, emoji, cost_points, created_at, group_id)"
        " VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (name, emoji, cost_points, now, group_id),
    )
    reward_id = cur.fetchone()["id"]
    conn.commit()
    cur.execute("SELECT * FROM rewards WHERE id = %s", (reward_id,))
    reward = cur.fetchone()
    conn.close()
    return dict(reward)


@router.delete("/groups/{group_id}/rewards/{reward_id}")
def admin_delete_reward(group_id: int, reward_id: int, _token: str = Depends(_require_admin)):
    """Admin 删除指定群组的奖励。"""
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
