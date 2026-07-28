"""时段定价服务：连续时间定价曲线，替代旧的每日涨降价系统。

定价曲线在 8:00/18:30 过零点，11:00 涨幅峰值，1:00 降幅谷值。
支持平台（消峰去谷）和半山腰系数。
"""

import hashlib
import random
from datetime import date, timedelta


# ---- 纯函数：定价曲线计算 ----

def compute_rate_at_time(params: dict, hour: int, minute: int) -> float:
    """纯函数 O(1)：给定参数和时间，返回当前定价倍率（正=涨价，负=降价，0=原价）。

    params: {surge_peak_rate, sale_trough_rate, plateau_minutes, partial_peak_factor, is_flat}
    """
    if params.get("is_flat", False):
        return 0.0

    surge_peak = float(params["surge_peak_rate"])
    sale_trough = float(params["sale_trough_rate"])
    plateau = int(params.get("plateau_minutes", 0))
    factor = float(params.get("partial_peak_factor", 1.0))

    effective_peak = surge_peak * factor
    effective_trough = sale_trough * factor

    half_plateau = plateau / 2.0

    # 关键时间点（分钟）
    ZERO_AM = 8 * 60          # 480
    PEAK = 11 * 60            # 660
    ZERO_PM = 18 * 60 + 30    # 1110
    TROUGH = 25 * 60          # 1500 (次日 1:00)
    ZERO_NEXT_AM = 32 * 60    # 1920 (次日 8:00)

    m = hour * 60 + minute
    # 归一化：如果 m < ZERO_AM，视为次日凌晨 → 加上 1440 分钟
    if m < ZERO_AM:
        m += 24 * 60

    # 六段定位与线性插值
    if m <= PEAK - half_plateau:
        # 段1: 早上涨价 [ZERO_AM, PEAK - plateau/2]  0 → effective_peak
        return _lerp(m, ZERO_AM, PEAK - half_plateau, 0.0, effective_peak)

    elif m <= PEAK + half_plateau:
        # 段2: 峰值平台 [PEAK - plateau/2, PEAK + plateau/2]  保持 effective_peak
        return effective_peak

    elif m <= ZERO_PM:
        # 段3: 下午回落 [PEAK + plateau/2, ZERO_PM]  effective_peak → 0
        return _lerp(m, PEAK + half_plateau, ZERO_PM, effective_peak, 0.0)

    elif m <= TROUGH - half_plateau:
        # 段4: 傍晚降价 [ZERO_PM, TROUGH - plateau/2]  0 → -effective_trough
        return _lerp(m, ZERO_PM, TROUGH - half_plateau, 0.0, -effective_trough)

    elif m <= TROUGH + half_plateau:
        # 段5: 谷值平台 [TROUGH - plateau/2, TROUGH + plateau/2]  保持 -effective_trough
        return -effective_trough

    else:
        # 段6: 前夜尾段 [TROUGH + plateau/2, ZERO_NEXT_AM]  -effective_trough → 0
        return _lerp(m, TROUGH + half_plateau, ZERO_NEXT_AM, -effective_trough, 0.0)


def _lerp(t, t_start, t_end, v_start, v_end):
    """线性插值，避免除零。"""
    if t_end == t_start:
        return v_start
    return v_start + (v_end - v_start) * (t - t_start) / (t_end - t_start)


# ---- 确定性随机生成 ----

def _deterministic_seed(reward_id: int, date_str: str, group_id: int) -> int:
    """用 hash 生成确定性种子，确保 Vercel serverless 各实例一致。"""
    key = f"{reward_id}:{date_str}:{group_id}"
    h = hashlib.sha256(key.encode()).hexdigest()
    return int(h[:16], 16)


def _random_params(rng: random.Random) -> dict:
    """从随机种子生成一天一个奖励的定价参数。"""
    # 60% 概率参与波动
    if rng.random() > 0.6:
        return {"is_flat": True}

    surge_peak = rng.choice([0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50])
    sale_trough = rng.choice([0.10, 0.15, 0.20, 0.25])
    plateau = rng.choice([0, 0, 0, 30, 60, 90, 120, 180])  # 偏重无平台
    factor = rng.choice([0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])

    return {
        "surge_peak_rate": surge_peak,
        "sale_trough_rate": sale_trough,
        "plateau_minutes": plateau,
        "partial_peak_factor": factor,
        "is_flat": False,
    }


# ---- 每日定价选择与持久化 ----

def select_daily_pricing(cur, group_id: int, today: date) -> list[dict]:
    """为群组选出当天每个奖励的定价参数。

    优先级: lock_out → is_flat; manual_params → 使用手动参数;
    lock_in → 强制参与; 默认 → 40%概率不参与 + 随机参数。
    """
    cur.execute("SELECT id, cost_points FROM rewards WHERE group_id = %s", (group_id,))
    rewards = {r["id"]: r["cost_points"] for r in cur.fetchall()}
    if not rewards:
        return []

    cur.execute(
        """SELECT reward_id, override_type, manual_surge_peak, manual_sale_trough,
                  manual_plateau, manual_partial_factor
           FROM daily_pricing_overrides WHERE group_id = %s
           AND (expires_at IS NULL OR expires_at >= %s)""",
        (group_id, today),
    )
    overrides = {}
    for r in cur.fetchall():
        overrides[r["reward_id"]] = r

    result = []
    date_str = today.isoformat()

    for rid in rewards:
        ov = overrides.get(rid)

        if ov and ov["override_type"] == "lock_out":
            result.append({"reward_id": rid, "is_flat": True})
            continue

        if ov and ov["override_type"] == "manual_params":
            result.append({
                "reward_id": rid,
                "surge_peak_rate": float(ov["manual_surge_peak"] or 0.25),
                "sale_trough_rate": float(ov["manual_sale_trough"] or 0.15),
                "plateau_minutes": int(ov["manual_plateau"] or 0),
                "partial_peak_factor": float(ov["manual_partial_factor"] or 1.0),
                "is_flat": False,
            })
            continue

        if ov and ov["override_type"] == "lock_in":
            # 强制参与，但参数随机
            rng = random.Random(_deterministic_seed(rid, date_str, group_id))
            params = _random_params(rng)
            params["is_flat"] = False  # lock_in 强制不为 flat
            params["reward_id"] = rid
            result.append(params)
            continue

        # 默认：随机决定
        rng = random.Random(_deterministic_seed(rid, date_str, group_id))
        params = _random_params(rng)
        params["reward_id"] = rid
        result.append(params)

    return result


def save_daily_pricing(cur, group_id: int, today: date, pricing_list: list[dict]) -> None:
    """持久化每日定价参数。"""
    for p in pricing_list:
        cur.execute(
            """INSERT INTO daily_reward_pricing
               (reward_id, group_id, pricing_date, surge_peak_rate, sale_trough_rate,
                plateau_minutes, partial_peak_factor, is_flat)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (reward_id, pricing_date) DO UPDATE
               SET surge_peak_rate = EXCLUDED.surge_peak_rate,
                   sale_trough_rate = EXCLUDED.sale_trough_rate,
                   plateau_minutes = EXCLUDED.plateau_minutes,
                   partial_peak_factor = EXCLUDED.partial_peak_factor,
                   is_flat = EXCLUDED.is_flat""",
            (p["reward_id"], group_id, today,
             p.get("surge_peak_rate", 0.25),
             p.get("sale_trough_rate", 0.15),
             p.get("plateau_minutes", 0),
             p.get("partial_peak_factor", 1.0),
             p.get("is_flat", False)),
        )


def ensure_daily_pricing(cur, group_id: int, today: date) -> None:
    """懒加载：检查今天是否已分配，未分配则生成。幂等。"""
    cur.execute(
        "SELECT COUNT(*) FROM daily_reward_pricing WHERE group_id = %s AND pricing_date = %s",
        (group_id, today),
    )
    if cur.fetchone()["count"] == 0:
        pricing_list = select_daily_pricing(cur, group_id, today)
        if pricing_list:
            save_daily_pricing(cur, group_id, today, pricing_list)


def get_todays_pricing(cur, group_id: int, today: date, hour: int, minute: int) -> dict[int, dict]:
    """返回今天定价映射 {reward_id: {rate, params, is_flat}}，rate 根据当前时间计算。"""
    cur.execute(
        """SELECT reward_id, surge_peak_rate, sale_trough_rate, plateau_minutes,
                  partial_peak_factor, is_flat
           FROM daily_reward_pricing WHERE group_id = %s AND pricing_date = %s""",
        (group_id, today),
    )
    result = {}
    for r in cur.fetchall():
        params = {
            "surge_peak_rate": float(r["surge_peak_rate"]),
            "sale_trough_rate": float(r["sale_trough_rate"]),
            "plateau_minutes": int(r["plateau_minutes"]),
            "partial_peak_factor": float(r["partial_peak_factor"]),
            "is_flat": bool(r["is_flat"]),
        }
        rate = compute_rate_at_time(params, hour, minute)
        result[r["reward_id"]] = {
            "rate": round(rate, 4),
            "params": params,
            "is_flat": bool(r["is_flat"]),
        }
    return result


# ---- 覆盖管理 ----

def get_pricing_overrides(cur, group_id: int) -> list[dict]:
    """获取群组的所有定价覆盖设置。"""
    cur.execute(
        "SELECT dpo.*, r.name AS reward_name FROM daily_pricing_overrides dpo"
        " JOIN rewards r ON dpo.reward_id = r.id"
        " WHERE dpo.group_id = %s",
        (group_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def set_pricing_override(
    cur, group_id: int, reward_id: int, override_type: str,
    manual_params: dict | None = None, now=None,
    duration_days: int | None = None,
) -> dict:
    """设置或清除定价覆盖。

    override_type: lock_in | lock_out | manual_params | none
    manual_params: {surge_peak_rate, sale_trough_rate, plateau_minutes, partial_peak_factor}
    """
    if override_type == "none":
        cur.execute("DELETE FROM daily_pricing_overrides WHERE reward_id = %s", (reward_id,))
    else:
        expires = None
        if duration_days is not None and duration_days > 0:
            expires = now.date() + timedelta(days=duration_days)

        sp = None
        st = None
        pl = None
        pf = None
        if override_type == "manual_params" and manual_params:
            sp = manual_params.get("surge_peak_rate")
            st = manual_params.get("sale_trough_rate")
            pl = manual_params.get("plateau_minutes")
            pf = manual_params.get("partial_peak_factor")

        cur.execute(
            """INSERT INTO daily_pricing_overrides
               (reward_id, group_id, override_type, manual_surge_peak, manual_sale_trough,
                manual_plateau, manual_partial_factor, expires_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (reward_id) DO UPDATE
               SET override_type = EXCLUDED.override_type,
                   manual_surge_peak = EXCLUDED.manual_surge_peak,
                   manual_sale_trough = EXCLUDED.manual_sale_trough,
                   manual_plateau = EXCLUDED.manual_plateau,
                   manual_partial_factor = EXCLUDED.manual_partial_factor,
                   expires_at = EXCLUDED.expires_at,
                   updated_at = EXCLUDED.updated_at""",
            (reward_id, group_id, override_type, sp, st, pl, pf, expires, now),
        )
    return {"success": True}
