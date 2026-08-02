"""投资系统 hook handler：任务完成时自动发放投资章。

独立模块，移除 register_all() 调用即可停用整个投资系统，
无需修改 tasks.py。
"""

from api.services.investment_service import award_investment_medal


def _handle_investment_medal_award(**kwargs):
    """任务完成 → 发放 1 枚投资章（同一任务当天只给 1 枚）。"""
    cur = kwargs["cur"]
    effective_child = kwargs["effective_child"]
    group_id = kwargs["group_id"]
    task_id = kwargs["task"]["id"]
    today = kwargs["today"]

    if effective_child:
        award_investment_medal(cur, effective_child, group_id, task_id, today)


def register_all():
    """注册投资系统 hook handler。在 app startup 时调用。"""
    from api.hooks import register

    register("after_task_complete_write", _handle_investment_medal_award)
