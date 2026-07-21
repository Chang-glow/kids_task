"""
应用配置常量。所有可配置项通过环境变量注入，方便 Vercel / Supabase 部署。
"""

import os
from datetime import datetime, timezone, timedelta

# 数据库（环境变量优先，Vercel dashboard 或 .env 中设置）
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql:///kids_rewards",
)

# 积分星级折算比例
STAR_MULTIPLIERS = {
    1: 0.5,
    2: 0.6,
    3: 0.8,
    4: 1.0,
    5: 1.2,
}

# 北京时间 UTC+8
CST = timezone(timedelta(hours=8))

# 模拟时间（admin 可设置，方便测试利息/信用分随时间变化）
_simulated_time: datetime | None = None


def now_cst() -> datetime:
    """返回当前北京时间（或模拟时间），不带时区信息（存入数据库）"""
    if _simulated_time is not None:
        return _simulated_time
    return datetime.now(CST).replace(tzinfo=None)


def set_simulated_time(t: datetime | None) -> None:
    """Admin 设置模拟时间。传 None 清除模拟，恢复真实时间。"""
    global _simulated_time
    _simulated_time = t


def get_simulated_time() -> datetime | None:
    """返回当前模拟时间，None 表示未设置。"""
    return _simulated_time
