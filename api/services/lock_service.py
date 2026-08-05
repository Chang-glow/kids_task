"""奖励锁服务：reward ↔ task 多对多绑定，当日完成所有钥匙任务前禁止兑换。"""


def add_reward_lock(cur, reward_id: int, task_id: int, group_id: int) -> dict:
    """为奖励添加一个钥匙任务绑定。已存在则忽略。"""
    cur.execute(
        """INSERT INTO reward_locks (reward_id, task_id, group_id)
           VALUES (%s, %s, %s)
           ON CONFLICT (reward_id, task_id) DO NOTHING""",
        (reward_id, task_id, group_id),
    )
    cur.execute("SELECT name FROM tasks WHERE id = %s", (task_id,))
    row = cur.fetchone()
    return {"success": True, "reward_id": reward_id, "task_id": task_id,
            "key_task_name": row["name"] if row else ""}


def remove_reward_lock(cur, reward_id: int, task_id: int, group_id: int) -> dict:
    """解除某个钥匙任务绑定。"""
    cur.execute(
        "DELETE FROM reward_locks WHERE reward_id = %s AND task_id = %s AND group_id = %s",
        (reward_id, task_id, group_id),
    )
    return {"success": True}


def remove_all_reward_locks(cur, reward_id: int, group_id: int) -> dict:
    """解除奖励的所有钥匙绑定。"""
    cur.execute(
        "DELETE FROM reward_locks WHERE reward_id = %s AND group_id = %s",
        (reward_id, group_id),
    )
    return {"success": True}


def get_reward_locks(cur, reward_id: int, group_id: int) -> list[dict]:
    """获取奖励的所有锁信息。无锁返回空列表。"""
    cur.execute(
        """SELECT rl.*, t.name AS key_task_name, t.status AS key_task_status,
                  t.completed_at
           FROM reward_locks rl
           JOIN tasks t ON rl.task_id = t.id
           WHERE rl.reward_id = %s AND rl.group_id = %s""",
        (reward_id, group_id),
    )
    return [dict(r) for r in cur.fetchall()]


def check_reward_unlocked(cur, reward_id: int, group_id: int, today) -> tuple[bool, str]:
    """检查奖励是否可兑换。返回 (unlocked, reason)。

    无锁 → unlocked
    所有钥匙任务今日均完成过 → unlocked
    任一钥匙任务今日未完成 → locked，列出所有未完成的钥匙
    """
    locks = get_reward_locks(cur, reward_id, group_id)
    if not locks:
        return True, ""

    pending = []
    for lock in locks:
        task_name = lock["key_task_name"]
        completed_date = lock["completed_at"].date() if lock["completed_at"] else None
        if completed_date == today:
            continue
        pending.append(task_name)

    if pending:
        names = "、".join(pending)
        return False, f"🔒 需先完成「{names}」才能兑换此奖励"

    return True, ""


def get_all_locks(cur, group_id: int) -> list[dict]:
    """获取群组所有奖励锁（admin 用）。"""
    cur.execute(
        """SELECT rl.*, r.name AS reward_name, t.name AS key_task_name
           FROM reward_locks rl
           JOIN rewards r ON rl.reward_id = r.id
           JOIN tasks t ON rl.task_id = t.id
           WHERE rl.group_id = %s
           ORDER BY rl.reward_id, rl.created_at DESC""",
        (group_id,),
    )
    return [dict(r) for r in cur.fetchall()]
