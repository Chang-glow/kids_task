"""每日任务翻倍服务：选择、权重衰减、倍率分配。"""

import random
from datetime import date, timedelta


BOOST_TIERS = [
    (1.50, 0.75),
    (1.75, 0.15),
    (2.00, 0.10),
]


def _random_multiplier() -> float:
    """按概率分布随机选取倍率：1.5x (75%), 1.75x (15%), 2.0x (10%)."""
    r = random.random()
    cumulative = 0.0
    for mult, prob in BOOST_TIERS:
        cumulative += prob
        if r < cumulative:
            return mult
    return 1.50


def compute_boost_weight(cur, task_id: int, today: date) -> float:
    """
    计算任务今天的抽选权重。
    base = task.base_points，每连续中奖一天权重折半，未中奖则重置。
    """
    cur.execute("SELECT base_points FROM tasks WHERE id = %s", (task_id,))
    row = cur.fetchone()
    if not row:
        return 0.0
    base = float(row["base_points"])
    consecutive = 0
    check_date = today - timedelta(days=1)
    while True:
        cur.execute(
            "SELECT 1 FROM daily_task_boosts WHERE task_id = %s AND boost_date = %s",
            (task_id, check_date),
        )
        if cur.fetchone():
            consecutive += 1
            check_date = check_date - timedelta(days=1)
        else:
            break
    return base * (0.5 ** consecutive)


def _weighted_sample(pool: list[tuple[int, float]], k: int) -> list[int]:
    """不放回加权随机抽样。pool: [(task_id, weight), ...]，返回 task_id 列表。"""
    if k <= 0 or not pool:
        return []
    selected = []
    remaining = [(tid, w) for tid, w in pool if w > 0]
    for _ in range(min(k, len(remaining))):
        total = sum(w for _, w in remaining)
        if total <= 0:
            break
        r = random.uniform(0, total)
        cumulative = 0.0
        for i, (tid, w) in enumerate(remaining):
            cumulative += w
            if r <= cumulative:
                selected.append(tid)
                remaining.pop(i)
                break
    return selected


def select_daily_boosts(cur, group_id: int, today: date, count: int = 3) -> list[dict]:
    """
    为群组选出当天翻倍任务。
    优先 lock_in，排除 lock_out，其余按衰减权重随机抽选。
    """
    cur.execute(
        "SELECT id, base_points FROM tasks WHERE group_id = %s AND status = 'pending'",
        (group_id,),
    )
    tasks = {t["id"]: t["base_points"] for t in cur.fetchall()}
    if not tasks:
        return []

    cur.execute(
        "SELECT task_id, override_type, manual_multiplier FROM daily_boost_overrides WHERE group_id = %s",
        (group_id,),
    )
    overrides = {}
    for r in cur.fetchall():
        overrides[r["task_id"]] = r

    locked_in = []
    locked_out = set()
    manual_mult = {}
    for tid, ov in overrides.items():
        if ov["override_type"] == "lock_in":
            locked_in.append(tid)
        elif ov["override_type"] == "lock_out":
            locked_out.add(tid)
        elif ov["override_type"] == "manual_multiplier" and ov["manual_multiplier"] is not None:
            manual_mult[tid] = float(ov["manual_multiplier"])

    result = []
    used = set()

    # 先处理 lock_in（不占随机名额但不超过 count）
    for tid in locked_in:
        if tid in tasks and tid not in locked_out and len(result) < count:
            mult = manual_mult.get(tid, _random_multiplier())
            result.append({"task_id": tid, "multiplier": mult})
            used.add(tid)

    # 剩余名额用加权随机
    remaining_slots = count - len(result)
    if remaining_slots > 0:
        pool = []
        for tid in tasks:
            if tid in used or tid in locked_out:
                continue
            weight = compute_boost_weight(cur, tid, today)
            if weight > 0:
                pool.append((tid, weight))
        selected = _weighted_sample(pool, remaining_slots)
        for tid in selected:
            mult = manual_mult.get(tid, _random_multiplier())
            result.append({"task_id": tid, "multiplier": mult})

    return result


def save_daily_boosts(cur, group_id: int, today: date, boosts: list[dict]) -> None:
    """持久化翻倍选择。"""
    for b in boosts:
        cur.execute(
            "INSERT INTO daily_task_boosts (task_id, group_id, boost_date, multiplier)"
            " VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (task_id, boost_date) DO UPDATE SET multiplier = EXCLUDED.multiplier",
            (b["task_id"], group_id, today, b["multiplier"]),
        )


def ensure_daily_boosts(cur, group_id: int, today: date, count: int = 3) -> None:
    """懒加载：检查今天是否已分配翻倍，未分配则生成。幂等。"""
    cur.execute(
        "SELECT COUNT(*) FROM daily_task_boosts WHERE group_id = %s AND boost_date = %s",
        (group_id, today),
    )
    if cur.fetchone()["count"] == 0:
        boosts = select_daily_boosts(cur, group_id, today, count)
        if boosts:
            save_daily_boosts(cur, group_id, today, boosts)


def get_task_boost(cur, task_id: int, today: date) -> float | None:
    """查询某个任务今天的翻倍倍率，未翻倍则返回 None。"""
    cur.execute(
        "SELECT multiplier FROM daily_task_boosts WHERE task_id = %s AND boost_date = %s",
        (task_id, today),
    )
    row = cur.fetchone()
    return float(row["multiplier"]) if row else None


def get_boost_overrides(cur, group_id: int) -> list[dict]:
    """获取群组的所有翻倍覆盖设置。"""
    cur.execute(
        "SELECT dbo.*, t.name AS task_name FROM daily_boost_overrides dbo"
        " JOIN tasks t ON dbo.task_id = t.id"
        " WHERE dbo.group_id = %s",
        (group_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def set_boost_override(
    cur, group_id: int, task_id: int, override_type: str,
    manual_multiplier: float | None, now,
) -> dict:
    """设置或清除翻倍覆盖。override_type = 'none' 时清除。"""
    if override_type == "none":
        cur.execute("DELETE FROM daily_boost_overrides WHERE task_id = %s", (task_id,))
    else:
        mult = manual_multiplier if override_type == "manual_multiplier" else None
        cur.execute(
            "INSERT INTO daily_boost_overrides (task_id, group_id, override_type, manual_multiplier, updated_at)"
            " VALUES (%s, %s, %s, %s, %s)"
            " ON CONFLICT (task_id) DO UPDATE SET override_type = EXCLUDED.override_type,"
            " manual_multiplier = EXCLUDED.manual_multiplier, updated_at = EXCLUDED.updated_at",
            (task_id, group_id, override_type, mult, now),
        )
    return {"success": True}


def get_todays_boosts(cur, group_id: int, today: date) -> dict[int, float]:
    """返回今天翻倍任务映射 {task_id: multiplier}。"""
    cur.execute(
        "SELECT task_id, multiplier FROM daily_task_boosts WHERE group_id = %s AND boost_date = %s",
        (group_id, today),
    )
    return {r["task_id"]: float(r["multiplier"]) for r in cur.fetchall()}
