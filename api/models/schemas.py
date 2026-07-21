"""
Pydantic 请求/响应模型。
"""

from pydantic import BaseModel


class ConditionResultItem(BaseModel):
    condition_id: int
    passed: bool


class CompleteTaskRequest(BaseModel):
    task_id: int
    star_rating: int  # 1-5 星
    condition_results: list[ConditionResultItem] = []


class AcceptConditionRequest(BaseModel):
    condition_id: int
    task_id: int
    child_id: int | None = None


class AddTaskRequest(BaseModel):
    name: str
    emoji: str
    base_points: int
    is_repeatable: bool = False
    child_id: int | None = None


class AddRewardRequest(BaseModel):
    name: str
    emoji: str
    cost_points: int


class RedeemRewardRequest(BaseModel):
    reward_id: int


class PunishRequest(BaseModel):
    name: str
    emoji: str
    penalty_points: int


class CreateGroupRequest(BaseModel):
    name: str = "我们的家"
    child_name: str = "小主人"


class AddChildRequest(BaseModel):
    name: str
    emoji: str = "👶"


class BorrowRequest(BaseModel):
    amount: int
    child_id: int | None = None


class RepayRequest(BaseModel):
    amount: int  # 还款金额，支持部分还款
