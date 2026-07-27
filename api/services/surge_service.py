"""每日奖励涨降价服务：选择、涨降幅分配、覆盖管理。"""

import random
from datetime import date


SURGE_TIERS = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]
SALE_TIERS = [0.10, 0.15, 0.20, 0.25]


def _random_surge_rate() -> float:
    """均匀随机选取涨幅（10%-50%，5% 一档）。"""
    return random.choice(SURGE_TIERS)


def _random_sale_rate() -> float:
    """均匀随机选取降幅（10%-25%，5% 一档）。"""
    return random.choice(SALE_TIERS)


def _random_count() -> int:
    """随机选取今日涨降价奖励总数：2-5 个。"""
    return random.randint(2, 5)


def select_daily_surges(cur, group_id: int, today: date) -> list[dict]:
    """
    为群组选出当天涨降价奖励。
    优先 lock_in，排除 lock_out，其余均匀随机抽选。
    随机分配 surge/sale 类型。
    """
    cur.execute(
        "SELECT id, cost_points FROM rewards WHERE group_id = %s",
        (group_id,),
    )
    rewards = {r["id"]: r["cost_points"] for r in cur.fetchall()}
    if not rewards:
        return []

    cur.execute(
        "SELECT reward_id, override_type, manual_rate FROM daily_reward_surge_overrides WHERE group_id = %s",
        (group_id,),
    )
    overrides = {}
    for r in cur.fetchall():
        overrides[r["reward_id"]] = r

    locked_in = []
    locked_out = set()
    manual_rate_map = {}
    for rid, ov in overrides.items():
        if ov["override_type"] == "lock_in":
            locked_in.append(rid)
        elif ov["override_type"] == "lock_out":
            locked_out.add(rid)
        if ov["override_type"] == "manual_rate" and ov["manual_rate"] is not None:
            manual_rate_map[rid] = float(ov["manual_rate"])
            if rid not in locked_in:
                locked_in.append(rid)

    count = _random_count()
    result = []
    used = set()

    # 先处理 lock_in
    for rid in locked_in:
        if rid in rewards and rid not in locked_out and len(result) < count:
            rate = manual_rate_map.get(rid)
            typ = "surge"  # default
            if rate is not None:
                typ = "surge" if rate > 0 else "sale"
            else:
                typ = random.choice(["surge", "sale"])
                rate = _random_surge_rate() if typ == "surge" else _random_sale_rate()
            result.append({"reward_id": rid, "rate": abs(rate), "type": typ})
            used.add(rid)

    # 剩余名额均匀随机选择
    remaining_slots = count - len(result)
    if remaining_slots > 0:
        candidates = [rid for rid in rewards if rid not in used and rid not in locked_out]
        selected = random.sample(candidates, min(remaining_slots, len(candidates)))
        for rid in selected:
            rate = manual_rate_map.get(rid)
            if rate is not None:
                typ = "surge" if rate > 0 else "sale"
                rate = abs(rate)
            else:
                typ = random.choice(["surge", "sale"])
                rate = _random_surge_rate() if typ == "surge" else _random_sale_rate()
            result.append({"reward_id": rid, "rate": rate, "type": typ})

    return result


def save_daily_surges(cur, group_id: int, today: date, surges: list[dict]) -> None:
    """持久化涨降价选择。"""
    for s in surges:
        cur.execute(
            "INSERT INTO daily_reward_surges (reward_id, group_id, surge_date, rate, type)"
            " VALUES (%s, %s, %s, %s, %s)"
            " ON CONFLICT (reward_id, surge_date) DO UPDATE SET rate = EXCLUDED.rate, type = EXCLUDED.type",
            (s["reward_id"], group_id, today, s["rate"], s["type"]),
        )


def ensure_daily_surges(cur, group_id: int, today: date) -> None:
    """懒加载：检查今天是否已分配，未分配则生成。幂等。"""
    cur.execute(
        "SELECT COUNT(*) FROM daily_reward_surges WHERE group_id = %s AND surge_date = %s",
        (group_id, today),
    )
    if cur.fetchone()["count"] == 0:
        surges = select_daily_surges(cur, group_id, today)
        if surges:
            save_daily_surges(cur, group_id, today, surges)


def get_todays_surges(cur, group_id: int, today: date) -> dict[int, dict]:
    """返回今天涨降价映射 {reward_id: {rate, type}}。"""
    cur.execute(
        "SELECT reward_id, rate, type FROM daily_reward_surges WHERE group_id = %s AND surge_date = %s",
        (group_id, today),
    )
    return {r["reward_id"]: {"rate": float(r["rate"]), "type": r["type"]} for r in cur.fetchall()}


def get_surge_overrides(cur, group_id: int) -> list[dict]:
    """获取群组的所有涨降价覆盖设置。"""
    cur.execute(
        "SELECT dso.*, r.name AS reward_name FROM daily_reward_surge_overrides dso"
        " JOIN rewards r ON dso.reward_id = r.id"
        " WHERE dso.group_id = %s",
        (group_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def set_surge_override(
    cur, group_id: int, reward_id: int, override_type: str,
    manual_rate: float | None, now,
) -> dict:
    """设置或清除涨降价覆盖。override_type = 'none' 时清除。"""
    if override_type == "none":
        cur.execute("DELETE FROM daily_reward_surge_overrides WHERE reward_id = %s", (reward_id,))
    else:
        rate = manual_rate if override_type == "manual_rate" else None
        cur.execute(
            "INSERT INTO daily_reward_surge_overrides (reward_id, group_id, override_type, manual_rate, updated_at)"
            " VALUES (%s, %s, %s, %s, %s)"
            " ON CONFLICT (reward_id) DO UPDATE SET override_type = EXCLUDED.override_type,"
            " manual_rate = EXCLUDED.manual_rate, updated_at = EXCLUDED.updated_at",
            (reward_id, group_id, override_type, rate, now),
        )
    return {"success": True}
