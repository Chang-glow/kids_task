"""任务路由：CRUD + 完成，按 group_id 隔离。"""

import json

from fastapi import APIRouter, HTTPException, Depends
from api.dependencies import get_group_id
from api.models.database import get_db
from api.models.schemas import AddTaskRequest, CompleteTaskRequest, AcceptConditionRequest
from api.config import now_cst, STAR_MULTIPLIERS
from api.services.point_service import calculate_final_points
from api.services.boost_service import get_todays_boosts, ensure_daily_boosts
from api.services.condition_service import (
    ensure_daily_conditions, get_task_conditions,
    accept_condition, calculate_condition_result,
)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("")
def get_tasks(group_id: int = Depends(get_group_id)):
    """获取任务列表（懒清理过期非重复任务 + 懒生成每日翻倍和条件）"""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "DELETE FROM tasks WHERE status = 'done' AND is_repeatable = false"
            " AND completed_at IS NOT NULL AND DATE(completed_at) < CURRENT_DATE"
            " AND group_id = %s",
            (group_id,),
        )
        today = now_cst().date()
        ensure_daily_boosts(cur, group_id, today)
        ensure_daily_conditions(cur, group_id, today)
        cur.execute(
            "SELECT * FROM tasks WHERE group_id = %s ORDER BY created_at DESC",
            (group_id,),
        )
        tasks = cur.fetchall()
        conn.commit()
        return [dict(t) for t in tasks]
    except Exception:
        conn.rollback()
        raise HTTPException(status_code=500, detail="服务器内部错误")
    finally:
        conn.close()


@router.post("")
def add_task(req: AddTaskRequest, group_id: int = Depends(get_group_id)):
    """添加新任务"""
    if req.base_points <= 0:
        raise HTTPException(status_code=400, detail="基础积分必须大于0")
    if len(req.name.strip()) == 0:
        raise HTTPException(status_code=400, detail="任务名称不能为空")
    conn = get_db()
    cur = conn.cursor()
    now = now_cst()
    cur.execute(
        "INSERT INTO tasks (name, emoji, base_points, status, is_repeatable, created_at, group_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (req.name.strip(), req.emoji, req.base_points, "pending", req.is_repeatable, now, group_id),
    )
    task_id = cur.fetchone()["id"]
    conn.commit()
    cur.execute("SELECT * FROM tasks WHERE id = %s", (task_id,))
    task = cur.fetchone()
    conn.close()
    return dict(task)


@router.post("/complete")
def complete_task(req: CompleteTaskRequest, group_id: int = Depends(get_group_id)):
    """完成任务并评级，应用每日翻倍倍率和悬赏附加条件。"""
    if req.star_rating not in range(1, 6):
        raise HTTPException(status_code=400, detail="星级评分必须在1到5之间")
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM tasks WHERE id = %s AND group_id = %s", (req.task_id, group_id))
        task = cur.fetchone()
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        if task["status"] != "pending":
            raise HTTPException(status_code=400, detail="该任务已经完成，不能重复提交")

        today = now_cst().date()
        now = now_cst()

        # 获取每日翻倍倍率
        cur.execute(
            "SELECT multiplier FROM daily_task_boosts WHERE task_id = %s AND boost_date = %s",
            (req.task_id, today),
        )
        boost_row = cur.fetchone()
        daily_multiplier = float(boost_row["multiplier"]) if boost_row else None

        # 获取并处理条件
        ensure_daily_conditions(cur, group_id, today)
        conditions = get_task_conditions(cur, req.task_id, group_id, today)
        condition_extra = ""
        condition_data = []
        if conditions and req.condition_results:
            result_map = {r.condition_id: r.passed for r in req.condition_results}
            for c in conditions:
                cid = c["id"]
                if cid in result_map:
                    c["passed"] = result_map[cid]
                    condition_data.append(c)
            final_points, condition_extra = calculate_condition_result(
                task["base_points"], req.star_rating, daily_multiplier, condition_data,
            )
        elif daily_multiplier:
            final_points = calculate_final_points(task["base_points"], req.star_rating)
            final_points = round(final_points * daily_multiplier)
        else:
            final_points = calculate_final_points(task["base_points"], req.star_rating)

        multiplier_pct = int(STAR_MULTIPLIERS[req.star_rating] * 100)

        if task["is_repeatable"]:
            cur.execute("UPDATE tasks SET completed_at = %s WHERE id = %s", (now, req.task_id))
            result_message = f"任务完成！获得 {final_points} 积分，明天还能继续 🔄"
        else:
            cur.execute("UPDATE tasks SET status = 'done', completed_at = %s WHERE id = %s", (now, req.task_id))
            result_message = f"太棒了！获得 {final_points} 积分 🎉"

        if task["child_id"]:
            cur.execute("UPDATE children SET total_points = total_points + %s WHERE id = %s", (final_points, task["child_id"]))
        else:
            cur.execute(
                "UPDATE children SET total_points = total_points + %s WHERE group_id = %s AND id = (SELECT MIN(id) FROM children WHERE group_id = %s)",
                (final_points, group_id, group_id),
            )

        desc_parts = [f"完成任务「{task['name']}」{req.star_rating}⭐（{multiplier_pct}%）"]
        if daily_multiplier:
            desc_parts.append(f"🔥翻倍×{daily_multiplier}")
        desc_parts.append(f"→ +{final_points}分")
        if condition_extra:
            desc_parts.append(f"| {condition_extra}")
        description = " " .join(desc_parts)

        cur.execute(
            "INSERT INTO point_logs (action, amount, description, created_at, group_id, child_id)"
            " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            ("earn", final_points, description, now, group_id, task["child_id"]),
        )
        log_id = cur.fetchone()["id"]

        undo_data = {
            "task_id": req.task_id,
            "previous_status": task["status"],
            "log_id": log_id,
            "points_added": final_points,
            "child_id": task["child_id"],
        }
        if daily_multiplier:
            undo_data["daily_multiplier"] = daily_multiplier
        if condition_data:
            undo_data["conditions"] = [
                {"condition_id": c["id"], "passed": c["passed"]} for c in condition_data
            ]
        cur.execute(
            "INSERT INTO undo_operations (group_id, child_id, operation_type, description, undo_data, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (group_id, task["child_id"], "task_complete", description,
             json.dumps(undo_data), now),
        )

        cur.execute("UPDATE users SET total_points = total_points + %s WHERE id = 1", (final_points,))

        conn.commit()

        if task["child_id"]:
            cur.execute("SELECT total_points FROM children WHERE id = %s", (task["child_id"],))
        else:
            cur.execute("SELECT total_points FROM children WHERE group_id = %s ORDER BY id LIMIT 1", (group_id,))
        child = cur.fetchone()

        return {
            "success": True,
            "earned_points": final_points,
            "total_points": child["total_points"] if child else 0,
            "message": result_message,
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise HTTPException(status_code=500, detail="服务器内部错误")
    finally:
        conn.close()


@router.delete("/{task_id}")
def delete_task(task_id: int, group_id: int = Depends(get_group_id)):
    """删除任务（需属于指定群组）"""
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


@router.get("/boosts/today")
def get_todays_boosts_endpoint(group_id: int = Depends(get_group_id)):
    """获取今日翻倍任务映射 {task_id: multiplier}。"""
    conn = get_db()
    cur = conn.cursor()
    today = now_cst().date()
    ensure_daily_boosts(cur, group_id, today)
    boosts = get_todays_boosts(cur, group_id, today)
    conn.commit()
    conn.close()
    return boosts


@router.get("/conditions/today")
def get_todays_conditions(group_id: int = Depends(get_group_id)):
    """获取今天群组所有活跃条件（含任务名和孩子接受状态）。"""
    conn = get_db()
    cur = conn.cursor()
    today = now_cst().date()
    ensure_daily_conditions(cur, group_id, today)
    cur.execute(
        """SELECT c.id, c.name, c.reward_type, c.bonus_value, c.multiplier_value,
                  array_agg(DISTINCT t.name ORDER BY t.name) AS task_names,
                  array_agg(DISTINCT ctb.task_id) AS task_ids,
                  bool_or(cca.accepted) AS accepted
           FROM conditions c
           JOIN daily_condition_selections dcs ON c.id = dcs.condition_id
           LEFT JOIN condition_task_bindings ctb ON c.id = ctb.condition_id
           LEFT JOIN tasks t ON ctb.task_id = t.id
           LEFT JOIN child_condition_acceptances cca
             ON c.id = cca.condition_id AND cca.group_id = %s AND cca.acceptance_date = %s
           WHERE dcs.group_id = %s AND dcs.selection_date = %s
           GROUP BY c.id
           ORDER BY c.id""",
        (group_id, today, group_id, today),
    )
    rows = []
    for r in cur.fetchall():
        d = dict(r)
        d["task_names"] = r["task_names"] if r["task_names"] != [None] else []
        d["task_ids"] = r["task_ids"] if r["task_ids"] != [None] else []
        rows.append(d)
    conn.commit()
    conn.close()
    return rows


@router.get("/{task_id}/conditions")
def get_task_conditions_today(task_id: int, group_id: int = Depends(get_group_id)):
    """获取绑定到某任务的今天活跃条件列表。"""
    conn = get_db()
    cur = conn.cursor()
    today = now_cst().date()
    cur.execute("SELECT * FROM tasks WHERE id = %s AND group_id = %s", (task_id, group_id))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="任务不存在")
    ensure_daily_conditions(cur, group_id, today)
    conds = get_task_conditions(cur, task_id, group_id, today)
    conn.close()
    return [dict(c) for c in conds]


@router.post("/conditions/accept")
def accept_task_condition(req: AcceptConditionRequest, group_id: int = Depends(get_group_id)):
    """孩子接受某任务的附加条件挑战。"""
    conn = get_db()
    cur = conn.cursor()
    child_id = req.child_id
    if not child_id:
        cur.execute("SELECT id FROM children WHERE group_id = %s ORDER BY id LIMIT 1", (group_id,))
        child_row = cur.fetchone()
        if not child_row:
            conn.close()
            raise HTTPException(status_code=400, detail="群组中没有孩子")
        child_id = child_row["id"]
    now = now_cst()
    today = now.date()
    accept_condition(cur, child_id, group_id, req.condition_id, req.task_id, today, now)
    conn.commit()
    conn.close()
    return {"success": True}
