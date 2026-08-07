# API 路由

所有路由（除 admin 外）通过 `X-Group-Code` 请求头做群组隔离。

## 群组

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/api/groups` | 创建群组 + 默认孩子 → 返回 invite_code |
| GET | `/api/groups/{invite_code}` | 查询群组信息 + 孩子列表 |

## 任务

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/api/tasks` | 获取任务列表（惰性清理过期非重复任务） |
| POST | `/api/tasks` | 添加任务 |
| POST | `/api/tasks/complete` | 完成任务 + 星级评级 |
| DELETE | `/api/tasks/{id}` | 删除任务 |
| GET | `/api/tasks/boosts/today` | 获取今日翻倍任务映射 |
| GET | `/api/tasks/conditions/today` | 获取今日悬赏条件列表 |
| POST | `/api/tasks/conditions/accept` | 接受某任务的条件挑战 |
| GET | `/api/tasks/{id}/conditions` | 获取任务绑定的今日条件 |

## 奖励商城

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/api/rewards` | 获取奖励列表（按积分升序，注入涨降价信息） |
| POST | `/api/rewards` | 添加奖励 |
| POST | `/api/rewards/redeem` | 兑换奖励（事务保护，不扣成负数，支持优惠券降价） |
| DELETE | `/api/rewards/{id}` | 删除奖励 |
| GET | `/api/rewards/pricing/today` | 获取今日时段定价映射 |

## 奖章 & 优惠券

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/api/medals/today` | 获取今日奖章数 |
| POST | `/api/medals/exchange` | 奖章兑换优惠券（5 章起兑） |
| GET | `/api/coupons` | 获取可用优惠券列表 |
| DELETE | `/api/coupons/{id}` | 删除优惠券 |

## 孩子 & 积分

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/api/children` | 添加孩子 |
| GET | `/api/logs` | 积分流水（分页） |
| POST | `/api/punish` | 惩罚扣分（冷静期限制） |

## 贷款

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/api/loans` | 获取贷款列表（含当前应还总额） |
| GET | `/api/loans/status` | 获取贷款资格（信用分、限额、冷却期） |
| POST | `/api/loans` | 借款 |
| POST | `/api/loans/{id}/repay` | 还款（支持部分还款） |

## Admin

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/api/admin/status` | 检查是否已设置密码 |
| POST | `/api/admin/setup` | 首次设置密码 |
| POST | `/api/admin/login` | 登录获取 token |
| POST | `/api/admin/change-password` | 修改密码 |
| POST | `/api/admin/undo` | 撤回上一步操作 |
| GET | `/api/admin/groups` | 列出所有群组 |
| GET | `/api/admin/boost-overrides` | 读取翻倍覆盖设置 |
| POST | `/api/admin/boost-overrides` | 设置翻倍覆盖 |
| GET | `/api/admin/surge-overrides` | 读取涨降价覆盖设置 |
| POST | `/api/admin/surge-overrides` | 设置涨降价覆盖 |
| PUT | `/api/admin/tasks/{id}` | 编辑任务（名称、积分、描述等） |
| GET | `/api/admin/conditions` | 列出悬赏条件 |
| POST | `/api/admin/conditions` | 创建悬赏条件 |
| DELETE | `/api/admin/conditions/{id}` | 删除悬赏条件 |
| POST | `/api/admin/groups/{id}/tasks` | 跨群组添加任务 |
| DELETE | `/api/admin/groups/{id}/tasks/{tid}` | 跨群组删除任务 |
| POST | `/api/admin/groups/{id}/rewards` | 跨群组添加奖励 |
| DELETE | `/api/admin/groups/{id}/rewards/{rid}` | 跨群组删除奖励 |
| GET | `/api/admin/loan-settings` | 读取贷款设置（利率、最高额度） |
| POST | `/api/admin/loan-settings` | 保存贷款设置 |
| GET | `/api/admin/simulated-time` | 读取模拟时间 |
| POST | `/api/admin/simulated-time` | 设置模拟时间 |
