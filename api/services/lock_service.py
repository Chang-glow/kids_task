"""奖励锁服务：reward ↔ task 多对多绑定，当日完成所有钥匙任务前禁止兑换。"""


def add_reward_lock(cur, reward_id: int, task_id: int, group_id: int,
                    lock_group: int | None = None) -> dict:
    """为奖励添加一个钥匙任务绑定。已存在则更新分组，保证幂等。"""
    cur.execute(
        """INSERT INTO reward_locks (reward_id, task_id, group_id, lock_group)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (reward_id, task_id) DO UPDATE SET lock_group = EXCLUDED.lock_group""",
        (reward_id, task_id, group_id, lock_group),
    )
    cur.execute("SELECT name FROM tasks WHERE id = %s", (task_id,))
    row = cur.fetchone()
    return {"success": True, "reward_id": reward_id, "task_id": task_id,
            "key_task_name": row["name"] if row else "", "lock_group": lock_group}


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
    钥匙按 lock_group 分组：同组内任选其一完成即可；每组都需至少一个完成。
    lock_group 为 NULL 的任务不参与锁判定（既不要求完成，也不影响解锁）。
    """
    locks = get_reward_locks(cur, reward_id, group_id)
    if not locks:
        return True, ""

    def done_today(lock) -> bool:
        return bool(lock["completed_at"]) and lock["completed_at"].date() == today

    groups: dict = {}
    for lock in locks:
        if lock["lock_group"] is None:
            continue  # 未分组的钥匙任务不参与锁判定
        key = lock["lock_group"]
        groups.setdefault(key, []).append(lock)

    pending = []
    for members in groups.values():
        if any(done_today(m) for m in members):
            continue
        names = [m["key_task_name"] for m in members]
        if len(members) > 1:
            pending.append(" / ".join(names) + "（任选其一）")
        else:
            pending.append(names[0])

    if pending:
        return False, f"🔒 需先完成「{'、'.join(pending)}」才能兑换此奖励"

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
