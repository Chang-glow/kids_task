"""积分计算业务逻辑。"""

import math
from api.config import STAR_MULTIPLIERS


def calculate_final_points(base_points: int, star_rating: int) -> int:
    """根据星级评分计算最终积分 = base_points * 星级系数，向下取整"""
    if star_rating not in STAR_MULTIPLIERS:
        raise ValueError(f"星级必须在1-5之间，收到：{star_rating}")
    return math.floor(base_points * STAR_MULTIPLIERS[star_rating])
