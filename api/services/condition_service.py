"""悬赏附加条件服务：选择、奖惩计算、CRUD。"""

import random
from datetime import date
from api.config import STAR_MULTIPLIERS


def select_daily_conditions(cur, group_id: int, count: int = 4) -> list[dict]:
    """随机选取当天激活的条件。排除已无待完成任务绑定的"死"条件。不足 count 则全选。"""
    cur.execute(
        """SELECT c.id, c.name, c.reward_type, c.bonus_value, c.multiplier_value
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
    if len(all_conds) <= count:
        return all_conds
    return random.sample(all_conds, count)


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
    # 自动延续活跃的 streak 条件
    carry_over_active_streaks(cur, group_id, today)


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

    cond_mult_sum = 0.0  # 所有条件倍率加算（delta 累加）
    cond_bonus = 0
    desc_parts = []

    for c in conditions:
        passed = c.get("passed", False)
        rt = c["reward_type"]

        if rt in ("multiplier", "both") and c.get("multiplier_value"):
            val = float(c["multiplier_value"])
            delta = val - 1.0
            cond_mult_sum += delta if passed else -delta

        if rt in ("bonus_points", "both") and c.get("bonus_value"):
            val = int(c["bonus_value"])
            cond_bonus += val if passed else -val

        desc = _reward_description(c, passed)
        if desc:
            desc_parts.append(desc)

    cond_mult = 1.0 + cond_mult_sum
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


# ---- Streak 自动延续 ----

def carry_over_active_streaks(cur, group_id: int, today: date) -> None:
    """把还在 active 状态的 streak 条件自动加入今日 daily_condition_selections。"""
    cur.execute(
        """INSERT INTO daily_condition_selections (group_id, condition_id, selection_date)
           SELECT DISTINCT sp.group_id, sp.condition_id, %s
           FROM condition_streak_progress sp
           WHERE sp.group_id = %s AND sp.status = 'active'
           ON CONFLICT (group_id, condition_id, selection_date) DO NOTHING""",
        (today, group_id),
    )
