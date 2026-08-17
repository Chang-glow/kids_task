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
    coupon_id: int | None = None
    investment_coupon_id: int | None = None
    child_id: int | None = None


class ExchangeCouponRequest(BaseModel):
    medal_count: int


class AdminIssueMedalRequest(BaseModel):
    child_id: int
    count: int  # >= 1


class AdminIssueCouponRequest(BaseModel):
    child_id: int
    medal_count: int  # >= 5


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


class EditTaskRequest(BaseModel):
    name: str
    emoji: str
    base_points: int
    description: str = ""
    is_repeatable: bool = False
    group_id: int


class BorrowRequest(BaseModel):
    amount: int
    child_id: int | None = None


class RepayRequest(BaseModel):
    amount: int  # 还款金额，支持部分还款
