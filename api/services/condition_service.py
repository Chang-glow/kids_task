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
    """懒加载：检查今天是否已分配条件，未分配则生成。幂等。"""
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
