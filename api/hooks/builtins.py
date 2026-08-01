"""内置 hook handler：奖章发放、连击检测、任务集检测。

这些 handler 由 app startup 时注册到 after_task_complete_write 事件。
移除对应的注册调用即可停用某个功能模块，无需修改 tasks.py。
"""

from api.hooks import register
from api.services.medal_service import award_medal
from api.services.condition_service import check_streak_on_complete, check_taskset_on_complete


def _handle_medal_award(**kwargs):
    """任务完成 → 发放 1 枚奖章。"""
    cur = kwargs["cur"]
    effective_child = kwargs["effective_child"]
    group_id = kwargs["group_id"]
    today = kwargs["today"]

    if effective_child:
        award_medal(cur, effective_child, group_id, today)


def _handle_streak_check(**kwargs):
    """任务完成 → 检查连续打卡条件。"""
    cur = kwargs["cur"]
    effective_child = kwargs["effective_child"]
    group_id = kwargs["group_id"]
    task_id = kwargs["task"]["id"]
    today = kwargs["today"]
    now = kwargs["now"]

    if effective_child:
        check_streak_on_complete(cur, effective_child, group_id, task_id, today, now)


def _handle_taskset_check(**kwargs):
    """任务完成 → 检查任务集条件。"""
    cur = kwargs["cur"]
    effective_child = kwargs["effective_child"]
    group_id = kwargs["group_id"]
    task_id = kwargs["task"]["id"]
    today = kwargs["today"]
    now = kwargs["now"]

    if effective_child:
        check_taskset_on_complete(cur, effective_child, group_id, task_id, today, now)


# ---- 注册入口 ----

def register_all():
    """注册所有内置 hook handler。在 app startup 时调用一次。"""
    register("after_task_complete_write", _handle_medal_award)
    register("after_task_complete_write", _handle_streak_check)
    register("after_task_complete_write", _handle_taskset_check)
