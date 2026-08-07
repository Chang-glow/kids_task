"""悬赏附加条件服务：选择、奖惩计算、CRUD。"""

import random
from datetime import date, timedelta
from api.config import STAR_MULTIPLIERS


def select_daily_conditions(cur, group_id: int, count: int = 4) -> list[dict]:
    """选取当天条件，总数不超过 count。三级优先级：

    1. lock_in 强制入选（admin 覆盖）
    2. active streak 强制入选（跨天连续性 —— 已开始的打卡今天必须出现）
    3. acceptance / task_set 随机填充剩余坑位

    lock_out 强制排除。无未完成任务的"死"条件自动排除。
    """
    cur.execute(
        """SELECT condition_id, override_type FROM condition_overrides
           WHERE group_id = %s AND (expires_at IS NULL OR expires_at >= CURRENT_DATE)""",
        (group_id,),
    )
    overrides = {r["condition_id"]: r["override_type"] for r in cur.fetchall()}
    locked_out = {cid for cid, otype in overrides.items() if otype == "lock_out"}
    locked_in_ids = {cid for cid, otype in overrides.items() if otype == "lock_in"}

    # 正在进行的 streak（需要跨天 carry-over）
    cur.execute(
        "SELECT DISTINCT condition_id FROM condition_streak_progress"
        " WHERE group_id = %s AND status = 'active'",
        (group_id,),
    )
    active_streak_ids = {r["condition_id"] for r in cur.fetchall()}

    # 所有有效条件（有未完成任务 + 未 lock_out）
    cur.execute(
        """SELECT c.id, c.name, c.reward_type, c.bonus_value, c.multiplier_value,
                  c.condition_type
           FROM conditions c
           WHERE c.group_id = %s
             AND EXISTS (
               SELECT 1 FROM condition_task_bindings ctb
               JOIN tasks t ON ctb.task_id = t.id
               WHERE ctb.condition_id = c.id AND t.status != 'done'
             )""",
        (group_id,),
    )
    all_conds = [dict(r) for r in cur.fetchall()]
    all_conds = [c for c in all_conds if c["id"] not in locked_out]

    # 按优先级分桶
    lock_in_conds = [c for c in all_conds if c["id"] in locked_in_ids]
    streak_conds = [c for c in all_conds
                    if c["id"] in active_streak_ids and c["id"] not in locked_in_ids]
    pool = [c for c in all_conds
            if c["id"] not in locked_in_ids and c["id"] not in active_streak_ids
            and c.get("condition_type", "acceptance") in (
                "acceptance", "task_set_specific", "task_set_random",
            )]

    # lock_in 优先占坑
    if len(lock_in_conds) >= count:
        return lock_in_conds[:count]

    # active streak 接着占坑
    if len(lock_in_conds) + len(streak_conds) >= count:
        return (lock_in_conds + streak_conds)[:count]

    # 剩余坑位从 acceptance/task_set 中随机填充
    slots_left = count - len(lock_in_conds) - len(streak_conds)
    if len(pool) <= slots_left:
        return lock_in_conds + streak_conds + pool
    return lock_in_conds + streak_conds + random.sample(pool, slots_left)


def save_daily_conditions(cur, group_id: int, today: date, conditions: list[dict]) -> None:
    """持久化当天条件选择。"""
    for c in conditions:
        cur.execute(
            "INSERT INTO daily_condition_selections (group_id, condition_id, selection_date)"
            " VALUES (%s, %s, %s) ON CONFLICT (group_id, condition_id, selection_date) DO NOTHING",
            (group_id, c["id"], today),
        )


def ensure_daily_conditions(cur, group_id: int, today: date, count: int = 4) -> None:
    """懒加载：检查今天是否已分配条件，未分配则生成。幂等。

    使用 advisory lock 防止并发请求同时 SELECT COUNT(*) → 0 导致重复选取。
    """
    lock_id = (hash(f"daily_cond_{group_id}") & 0x7FFFFFFF)
    cur.execute("SELECT pg_advisory_xact_lock(%s)", (lock_id,))
    cur.execute(
        "SELECT COUNT(*) FROM daily_condition_selections WHERE group_id = %s AND selection_date = %s",
        (group_id, today),
    )
    if cur.fetchone()["count"] == 0:
        conds = select_daily_conditions(cur, group_id, count)
        if conds:
            save_daily_conditions(cur, group_id, today, conds)


def get_task_conditions(cur, task_id: int, group_id: int, today: date) -> list[dict]:
    """查询绑定到某任务的当天活跃且已被接受的条件。"""
    cur.execute(
        """SELECT c.id, c.name, c.reward_type, c.bonus_value, c.multiplier_value
           FROM conditions c
           JOIN condition_task_bindings ctb ON c.id = ctb.condition_id
           JOIN daily_condition_selections dcs ON c.id = dcs.condition_id
           JOIN child_condition_acceptances cca
             ON c.id = cca.condition_id AND cca.group_id = %s
             AND cca.acceptance_date = %s AND cca.accepted = true
           WHERE ctb.task_id = %s AND dcs.group_id = %s AND dcs.selection_date = %s""",
        (group_id, today, task_id, group_id, today),
    )
    return [dict(r) for r in cur.fetchall()]


def accept_condition(
    cur, child_id: int, group_id: int, condition_id: int,
    task_id: int, today: date, now,
) -> dict:
    """孩子接受某任务的条件挑战。"""
    cur.execute(
        """INSERT INTO child_condition_acceptances
           (child_id, group_id, condition_id, task_id, accepted, acceptance_date, created_at)
           VALUES (%s, %s, %s, %s, true, %s, %s)
           ON CONFLICT DO NOTHING""",
        (child_id, group_id, condition_id, task_id, today, now),
    )
    return {"success": True}


def _reward_description(cond: dict, passed: bool) -> str:
    """生成单条条件的文字描述（用于日志）。"""
    name = cond["name"]
    parts = []
    if cond["reward_type"] in ("bonus_points", "both") and cond.get("bonus_value"):
        val = cond["bonus_value"]
        parts.append(f"{name}{'+' if passed else '-'}{val}")
    if cond["reward_type"] in ("multiplier", "both") and cond.get("multiplier_value"):
        val = float(cond["multiplier_value"])
        if passed:
            parts.append(f"{name}×{val}")
        else:
            parts.append(f"{name}÷{val}")
    return "、" .join(parts)


def calculate_condition_result(
    base_points: int, star_rating: int,
    daily_multiplier: float | None,
    conditions: list[dict],
) -> tuple[int, str]:
    """
    综合计算最终积分（星级 + 翻倍 + 条件奖惩）。
    conditions: [{"reward_type": str, "bonus_value": int|None,
                   "multiplier_value": float|None, "passed": bool, "name": str}, ...]
    返回 (final_points, description_suffix).
    """
    star_mult = STAR_MULTIPLIERS[star_rating]
    daily_mult = daily_multiplier if daily_multiplier else 1.0

    cond_mult = 1.0  # 所有条件倍率乘算（通过 ×val，失败 ÷val）
    cond_bonus = 0
    desc_parts = []

    for c in conditions:
        passed = c.get("passed", False)
        rt = c["reward_type"]

        if rt in ("multiplier", "both") and c.get("multiplier_value"):
            val = float(c["multiplier_value"])
            if passed:
                cond_mult *= val
            else:
                cond_mult /= val

        if rt in ("bonus_points", "both") and c.get("bonus_value"):
            val = int(c["bonus_value"])
            cond_bonus += val if passed else -val

        desc = _reward_description(c, passed)
        if desc:
            desc_parts.append(desc)

    raw = base_points * star_mult * daily_mult * cond_mult
    final = max(1, round(raw) + cond_bonus)

    desc = " | ".join(desc_parts) if desc_parts else ""
    return final, desc


def create_condition(
    cur, group_id: int, name: str, reward_type: str,
    bonus_value: int | None, multiplier_value: float | None,
    task_ids: list[int], now,
) -> dict:
    """创建新条件并绑定到任务。"""
    cur.execute(
        "INSERT INTO conditions (group_id, name, reward_type, bonus_value, multiplier_value, created_at)"
        " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (group_id, name, reward_type, bonus_value, multiplier_value, now),
    )
    cond_id = cur.fetchone()["id"]
    for tid in task_ids:
        cur.execute(
            "INSERT INTO condition_task_bindings (condition_id, task_id) VALUES (%s, %s)"
            " ON CONFLICT DO NOTHING",
            (cond_id, tid),
        )
    return {"success": True, "condition_id": cond_id}


def delete_condition(cur, condition_id: int, group_id: int) -> dict:
    """删除条件（CASCADE 清除绑定）。"""
    cur.execute(
        "DELETE FROM conditions WHERE id = %s AND group_id = %s",
        (condition_id, group_id),
    )
    return {"success": True}


# ---- Streak（连续打卡）----

def check_streak_on_complete(cur, child_id: int, group_id: int, task_id: int, today: date, now) -> list[dict]:
    """任务完成时检查连续打卡条件。返回 [{type, condition_name, ...}]。"""
    from datetime import timedelta

    cur.execute(
        """SELECT c.id, c.name, c.bonus_value, c.streak_days
           FROM conditions c
           JOIN condition_task_bindings ctb ON c.id = ctb.condition_id
           JOIN daily_condition_selections dcs ON c.id = dcs.condition_id
           WHERE c.condition_type = 'streak'
             AND c.group_id = %s AND ctb.task_id = %s
             AND dcs.selection_date = %s AND dcs.group_id = %s""",
        (group_id, task_id, today, group_id),
    )
    streaks = [dict(r) for r in cur.fetchall()]
    if not streaks:
        return []

    results = []
    yesterday = today - timedelta(days=1)

    for s in streaks:
        # ensure progress row exists
        cur.execute(
            """INSERT INTO condition_streak_progress (child_id, group_id, condition_id)
               VALUES (%s, %s, %s) ON CONFLICT (child_id, condition_id) DO NOTHING""",
            (child_id, group_id, s["id"]),
        )
        cur.execute(
            "SELECT * FROM condition_streak_progress WHERE child_id = %s AND condition_id = %s",
            (child_id, s["id"]),
        )
        prog = cur.fetchone()

        if prog["status"] != "active":
            continue

        if prog["last_completed_date"] == today:
            continue  # 今天已经计过

        bonus_value = s["bonus_value"] or 10
        streak_days = s["streak_days"] or 3

        if prog["last_completed_date"] == yesterday:
            new_count = prog["streak_count"] + 1
        elif prog["streak_count"] == 0:
            new_count = 1  # 首次
        else:
            # 中断 → 扣分
            penalty = min(bonus_value, _get_child_points(cur, child_id))
            if penalty > 0:
                cur.execute(
                    "UPDATE children SET total_points = GREATEST(0, total_points - %s) WHERE id = %s",
                    (penalty, child_id),
                )
                _insert_point_log(cur, "punish", penalty,
                                  f"⚠️ 连续打卡中断「{s['name']}」→ -{penalty}分",
                                  group_id, child_id, now)
            cur.execute(
                "UPDATE condition_streak_progress SET status = 'failed', updated_at = %s WHERE id = %s",
                (now, prog["id"]),
            )
            results.append({"type": "streak_failed", "condition_name": s["name"], "penalty": penalty})
            continue

        if new_count >= streak_days:
            # 达成
            cur.execute("UPDATE children SET total_points = total_points + %s WHERE id = %s",
                        (bonus_value, child_id))
            _insert_point_log(cur, "earn", bonus_value,
                              f"🔥 连续打卡达成「{s['name']}」{streak_days}天 → +{bonus_value}分",
                              group_id, child_id, now)
            cur.execute(
                "UPDATE condition_streak_progress SET status = 'completed', streak_count = %s,"
                " last_completed_date = %s, updated_at = %s WHERE id = %s",
                (new_count, today, now, prog["id"]),
            )
            results.append({"type": "streak_completed", "condition_name": s["name"],
                            "bonus": bonus_value, "days": new_count})
        else:
            # 继续
            cur.execute(
                "UPDATE condition_streak_progress SET streak_count = %s,"
                " last_completed_date = %s, updated_at = %s WHERE id = %s",
                (new_count, today, now, prog["id"]),
            )
            results.append({"type": "streak_progress", "condition_name": s["name"],
                            "current": new_count, "target": streak_days})

    return results


# ---- Task Set（任务集合）----

def check_taskset_on_complete(cur, child_id: int, group_id: int, task_id: int, today: date, now) -> list[dict]:
    """任务完成时检查任务集合条件。返回 [{type, condition_name, ...}]。"""
    cur.execute(
        """SELECT c.id, c.name, c.bonus_value, c.condition_type, c.subset_size,
                  array_agg(ctb.task_id) AS all_task_ids
           FROM conditions c
           JOIN condition_task_bindings ctb ON c.id = ctb.condition_id
           JOIN daily_condition_selections dcs ON c.id = dcs.condition_id
           WHERE c.condition_type IN ('task_set_specific', 'task_set_random')
             AND c.group_id = %s AND dcs.selection_date = %s AND dcs.group_id = %s
           GROUP BY c.id""",
        (group_id, today, group_id),
    )
    all_sets = [dict(r) for r in cur.fetchall()]
    # filter to those that include this task
    matching = [ts for ts in all_sets if task_id in ts["all_task_ids"]]
    if not matching:
        return []

    import json as _json

    results = []
    for ts in matching:
        # ensure progress row
        cur.execute(
            """INSERT INTO condition_task_set_progress (child_id, group_id, condition_id, selection_date)
               VALUES (%s, %s, %s, %s) ON CONFLICT (child_id, condition_id, selection_date) DO NOTHING""",
            (child_id, group_id, ts["id"], today),
        )
        cur.execute(
            "SELECT * FROM condition_task_set_progress"
            " WHERE child_id = %s AND condition_id = %s AND selection_date = %s",
            (child_id, ts["id"], today),
        )
        prog = cur.fetchone()
        if prog["status"] != "active":
            continue

        # determine required tasks
        if ts["condition_type"] == "task_set_random":
            selected = _json.loads(prog["selected_tasks"]) if isinstance(prog["selected_tasks"], str) else (prog["selected_tasks"] or [])
            if not selected:
                # generate random subset for the day
                pool = ts["all_task_ids"]
                size = min(ts["subset_size"] or 3, len(pool))
                import random as _random
                selected = _random.sample(pool, size)
                cur.execute(
                    "UPDATE condition_task_set_progress SET selected_tasks = %s WHERE id = %s",
                    (_json.dumps(selected), prog["id"]),
                )
            required = set(selected)
        else:
            required = set(ts["all_task_ids"])

        # add current task
        completed = set(prog["completed_tasks"] if isinstance(prog["completed_tasks"], list) else [])
        completed.add(task_id)

        if required.issubset(completed):
            # 全部完成
            bonus_value = ts["bonus_value"] or 10
            cur.execute("UPDATE children SET total_points = total_points + %s WHERE id = %s",
                        (bonus_value, child_id))
            _insert_point_log(cur, "earn", bonus_value,
                              f"🎯 任务集合达成「{ts['name']}」→ +{bonus_value}分",
                              group_id, child_id, now)
            cur.execute(
                """UPDATE condition_task_set_progress SET completed_tasks = %s,
                   status = 'completed', completed_at = %s WHERE id = %s""",
                (_json.dumps(list(completed)), now, prog["id"]),
            )
            results.append({"type": "taskset_completed", "condition_name": ts["name"],
                            "bonus": bonus_value})
        else:
            cur.execute(
                "UPDATE condition_task_set_progress SET completed_tasks = %s WHERE id = %s",
                (_json.dumps(list(completed)), prog["id"]),
            )
            remaining = required - completed
            results.append({"type": "taskset_progress", "condition_name": ts["name"],
                            "done": len(completed & required), "total": len(required),
                            "remaining_task_ids": list(remaining)})

    return results


# ---- Helpers ----

def _get_child_points(cur, child_id: int) -> int:
    cur.execute("SELECT total_points FROM children WHERE id = %s", (child_id,))
    row = cur.fetchone()
    return row["total_points"] if row else 0


def _insert_point_log(cur, action: str, amount: int, description: str, group_id: int, child_id: int, now):
    cur.execute(
        "INSERT INTO point_logs (action, amount, description, created_at, group_id, child_id)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (action, amount, description, now, group_id, child_id),
    )


def ensure_taskset_progress(cur, child_id: int, group_id: int, condition_id: int,
                            today: date) -> dict | None:
    """确保 task_set 进度行存在。对 task_set_random 在首次访问时随机抽取子集。

    返回 progress 数据，含 selected_tasks 和 completed_tasks 列表。
    """
    cur.execute("SELECT * FROM conditions WHERE id = %s", (condition_id,))
    cond = cur.fetchone()
    if not cond:
        return None

    cur.execute(
        """INSERT INTO condition_task_set_progress (child_id, group_id, condition_id, selection_date)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (child_id, condition_id, selection_date) DO NOTHING""",
        (child_id, group_id, condition_id, today),
    )
    cur.execute(
        "SELECT * FROM condition_task_set_progress"
        " WHERE child_id = %s AND condition_id = %s AND selection_date = %s",
        (child_id, condition_id, today),
    )
    prog = cur.fetchone()

    if cond["condition_type"] == "task_set_random":
        selected = (prog["selected_tasks"] if isinstance(prog["selected_tasks"], list)
                    else [])
        if not selected:
            # 首次访问：随机抽取 subset_size 个任务
            cur.execute(
                "SELECT array_agg(ctb.task_id) AS all_task_ids"
                " FROM condition_task_bindings ctb WHERE ctb.condition_id = %s",
                (condition_id,),
            )
            row = cur.fetchone()
            pool = row["all_task_ids"] if row and row["all_task_ids"] else []
            size = min(cond["subset_size"] or 3, len(pool))
            import json as _json
            if size > 0 and len(pool) >= size:
                selected = random.sample(pool, size)
            else:
                selected = pool
            cur.execute(
                "UPDATE condition_task_set_progress SET selected_tasks = %s WHERE id = %s",
                (_json.dumps(selected), prog["id"]),
            )
            prog["selected_tasks"] = selected

    completed = (prog["completed_tasks"] if isinstance(prog["completed_tasks"], list)
                 else [])
    return {
        "completed_tasks": completed,
        "selected_tasks": (prog["selected_tasks"] if isinstance(prog["selected_tasks"], list)
                           else []),
        "status": prog["status"],
    }


# ---- 条件覆盖管理 ----

def get_condition_overrides(cur, group_id: int) -> list[dict]:
    """列出群组的所有条件覆盖设置。"""
    cur.execute(
        """SELECT co.*, c.name AS condition_name
           FROM condition_overrides co
           JOIN conditions c ON co.condition_id = c.id
           WHERE co.group_id = %s""",
        (group_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def set_condition_override(cur, group_id: int, condition_id: int,
                           override_type: str, now,
                           duration_days: int | None = None) -> dict:
    """设置或清除条件覆盖。override_type='none' 时清除。
    duration_days: 持续天数，None 表示永久。
    """
    if override_type == "none":
        cur.execute(
            "DELETE FROM condition_overrides WHERE group_id = %s AND condition_id = %s",
            (group_id, condition_id),
        )
    else:
        expires = None
        if duration_days is not None and duration_days > 0:
            expires = now.date() + timedelta(days=duration_days)
        cur.execute(
            """INSERT INTO condition_overrides (group_id, condition_id, override_type, expires_at)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (group_id, condition_id)
               DO UPDATE SET override_type = EXCLUDED.override_type,
                             expires_at = EXCLUDED.expires_at""",
            (group_id, condition_id, override_type, expires),
        )
    return {"success": True}


# ---- 每日刷新 ----

def refresh_daily_conditions(cur, group_id: int, child_id: int, today: date, now,
                              condition_id: int | None = None) -> dict:
    """刷新单条悬赏条件，每日 2 次免费，之后每次递增 5 积分。"""
    # 检查该条件是否已被接受，已接受不可刷新
    if condition_id is not None:
        cur.execute(
            "SELECT 1 FROM child_condition_acceptances"
            " WHERE group_id = %s AND condition_id = %s AND acceptance_date = %s AND accepted = true",
            (group_id, condition_id, today),
        )
        if cur.fetchone():
            raise ValueError("已接受的条件不能刷新")

        # 确认该条件确实在今日选单中
        cur.execute(
            "SELECT 1 FROM daily_condition_selections"
            " WHERE group_id = %s AND selection_date = %s AND condition_id = %s",
            (group_id, today, condition_id),
        )
        if not cur.fetchone():
            raise ValueError("条件不在今日选单中")

    cur.execute(
        "SELECT COUNT(*) as cnt FROM condition_refresh_log"
        " WHERE group_id = %s AND refresh_date = %s",
        (group_id, today),
    )
    refresh_count = cur.fetchone()["cnt"]

    if refresh_count < 2:
        point_cost = 0
    else:
        point_cost = 5 * (refresh_count - 1)

    if point_cost > 0:
        cur.execute("SELECT total_points FROM children WHERE id = %s", (child_id,))
        child = cur.fetchone()
        if not child or child["total_points"] < point_cost:
            raise ValueError("积分不足，无法刷新")

        cur.execute(
            "UPDATE children SET total_points = total_points - %s WHERE id = %s",
            (point_cost, child_id),
        )
        _insert_point_log(cur, "spend", point_cost,
                          f"🔄 刷新今日悬赏条件 (第{refresh_count + 1}次)",
                          group_id, child_id, now)

    cur.execute(
        "INSERT INTO condition_refresh_log (group_id, refresh_date, refresh_count, point_cost, created_at)"
        " VALUES (%s, %s, %s, %s, %s)",
        (group_id, today, refresh_count + 1, point_cost, now),
    )

    if condition_id is not None:
        # 只替换指定的一条未接受条件
        cur.execute(
            "DELETE FROM daily_condition_selections"
            " WHERE group_id = %s AND selection_date = %s AND condition_id = %s",
            (group_id, today, condition_id),
        )
        cur.execute(
            "DELETE FROM condition_task_set_progress"
            " WHERE group_id = %s AND selection_date = %s AND condition_id = %s",
            (group_id, today, condition_id),
        )
        # 获取当前选单中已有的条件 ID，避免重复
        cur.execute(
            "SELECT condition_id FROM daily_condition_selections"
            " WHERE group_id = %s AND selection_date = %s",
            (group_id, today),
        )
        existing_ids = {r["condition_id"] for r in cur.fetchall()}
        # 从候选池中选一条不在已有列表中的
        candidates = select_daily_conditions(cur, group_id, 20)
        new_cond = None
        for c in candidates:
            if c["id"] not in existing_ids:
                new_cond = c
                break
        if new_cond:
            save_daily_conditions(cur, group_id, today, [new_cond])
    else:
        # 兜底：全量刷新（不应再走到这里，但保留兼容）
        cur.execute(
            """DELETE FROM daily_condition_selections
               WHERE group_id = %s AND selection_date = %s
                 AND condition_id NOT IN (
                   SELECT DISTINCT condition_id FROM child_condition_acceptances
                   WHERE group_id = %s AND acceptance_date = %s AND accepted = true
                 )""",
            (group_id, today, group_id, today),
        )
        conds = select_daily_conditions(cur, group_id, 4)
        if conds:
            cur.execute(
                "DELETE FROM daily_condition_selections WHERE group_id = %s AND selection_date = %s",
                (group_id, today),
            )
            save_daily_conditions(cur, group_id, today, conds)

    new_count = refresh_count + 1
    free_left = max(0, 2 - new_count)
    next_cost = 0 if new_count < 2 else 5 * (new_count - 1)

    return {
        "success": True,
        "point_cost": point_cost,
        "free_refreshes_left": free_left,
        "next_refresh_cost": next_cost,
    }
