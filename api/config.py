"""
应用配置常量。所有可配置项通过环境变量注入，方便 Vercel / Supabase 部署。
"""

import os
from datetime import datetime, timezone, timedelta

# 数据库（环境变量优先，Vercel dashboard 或 .env 中设置）
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://localhost:5432/kids_rewards",
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


def now_cst() -> datetime:
    """返回当前北京时间，不带时区信息（存入数据库）"""
    return datetime.now(CST).replace(tzinfo=None)
