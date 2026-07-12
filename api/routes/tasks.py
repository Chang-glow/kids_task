"""任务路由：CRUD + 完成，按 group_id 隔离。"""

import json

from fastapi import APIRouter, HTTPException, Depends
from api.dependencies import get_group_id
from api.models.database import get_db
from api.models.schemas import AddTaskRequest, CompleteTaskRequest
from api.config import now_cst, STAR_MULTIPLIERS
from api.services.point_service import calculate_final_points

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("")
def get_tasks(group_id: int = Depends(get_group_id)):
    """获取任务列表（按 group 过滤，懒清理过期非重复任务）"""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "DELETE FROM tasks WHERE status = 'done' AND is_repeatable = false"
            " AND completed_at IS NOT NULL AND DATE(completed_at) < CURRENT_DATE"
            " AND group_id = %s",
            (group_id,),
        )
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
    """完成任务并评级"""
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

        final_points = calculate_final_points(task["base_points"], req.star_rating)
        multiplier_pct = int(STAR_MULTIPLIERS[req.star_rating] * 100)
        now = now_cst()

        if task["is_repeatable"]:
            cur.execute("UPDATE tasks SET completed_at = %s WHERE id = %s", (now, req.task_id))
            result_message = f"任务完成！获得 {final_points} 积分，明天还能继续 🔄"
        else:
            cur.execute("UPDATE tasks SET status = 'done', completed_at = %s WHERE id = %s", (now, req.task_id))
            result_message = f"太棒了！获得 {final_points} 积分 🎉"

        # Update child points if task has child_id, otherwise update group's first child
        if task["child_id"]:
            cur.execute("UPDATE children SET total_points = total_points + %s WHERE id = %s", (final_points, task["child_id"]))
        else:
            cur.execute(
                "UPDATE children SET total_points = total_points + %s WHERE group_id = %s AND id = (SELECT MIN(id) FROM children WHERE group_id = %s)",
                (final_points, group_id, group_id),
            )

        description = f"完成任务「{task['name']}」{req.star_rating}⭐（{multiplier_pct}%）→ +{final_points}分"
        cur.execute(
            "INSERT INTO point_logs (action, amount, description, created_at, group_id, child_id)"
            " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            ("earn", final_points, description, now, group_id, task["child_id"]),
        )
        log_id = cur.fetchone()["id"]

        cur.execute(
            "INSERT INTO undo_operations (group_id, child_id, operation_type, description, undo_data, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (group_id, task["child_id"], "task_complete", description,
             json.dumps({"task_id": req.task_id, "previous_status": task["status"],
                         "log_id": log_id, "points_added": final_points,
                         "child_id": task["child_id"]}), now),
        )

        # Also update legacy users table for backward compat
        cur.execute("UPDATE users SET total_points = total_points + %s WHERE id = 1", (final_points,))

        conn.commit()

        # Get child points for response
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
