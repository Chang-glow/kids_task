# API Hooks 完整介入点分析

> 只记录，不修改。本文档是 hooks 框架的完整设计参考。
> 生成日期: 2026-07-28

---

## 零、Hook 注册器架构（参考）

```python
# api/hooks.py
_registry: dict[str, list[Callable]] = {}

def register(event: str, handler: Callable) -> None:
    """注册 hook。handler 接收 **kwargs，异常不传播。"""
    _registry.setdefault(event, []).append(handler)

def emit(event: str, **kwargs) -> list[Any]:
    """触发 hook。返回所有 handler 结果，单个异常不中断其他 handler。"""
    results = []
    for h in _registry.get(event, []):
        try:
            results.append(h(**kwargs))
        except Exception:
            pass  # hook 异常不传播
    return results

def unregister(event: str, handler: Callable) -> None:
    """取消注册。"""
    try:
        _registry[event].remove(handler)
    except (KeyError, ValueError):
        pass
```

### 设计原则

1. **同步执行**：hook handler 在事务内同步执行，可访问 `cur`
2. **异常隔离**：单个 hook 异常不中断其他 hook，也不中断主流程
3. **无优先级**：handler 按注册顺序执行，不保证顺序
4. **返回值可选**：handler 返回值被收集但不强制使用（cur 参数可直接写 DB）

---

## 一、任务生命周期 (Task Lifecycle)

### 1.1 task.create — 创建任务

| 项目 | 内容 |
|------|------|
| **位置** | `api/routes/tasks.py:51-71` |
| **当前侧效应** | 无 |
| **undo 记录** | 无 |

| Hook 事件 | 时机 | 可用 kwargs |
|-----------|------|------------|
| `before_task_create` | 校验后，INSERT 前 (:61) | `req` (AddTaskRequest), `group_id`, `cur` |
| `after_task_create` | commit 后，响应前 (:71) | `task` (dict), `group_id`, `task_id` |

**缺口**：无 undo 记录，无 try/except，无 cache 失效。

### 1.2 task.complete — 完成任务（最复杂）

| 项目 | 内容 |
|------|------|
| **位置** | `api/routes/tasks.py:74-211` |
| **当前侧效应** | `ensure_daily_boosts`(:92，boost_service)，`ensure_daily_conditions`(:101，condition_service)，`check_streak_on_complete`(:180)，`check_taskset_on_complete`(:181)，`award_medal`(:186)，`UPDATE users`(:188) |
| **undo 记录** | 有 (task_complete) |

| Hook 事件 | 时机 | 可用 kwargs |
|-----------|------|------------|
| `before_task_complete_validate` | 取任务前 (:82) | `req`, `group_id`, `cur` |
| `after_task_fetched` | SELECT 后，状态校验后 (:87) | `task` (dict), `req`, `group_id`, `cur` |
| `after_daily_boost_resolved` | boost lookup 后 (:98) | `task`, `req`, `group_id`, `daily_multiplier`, `cur` |
| `after_conditions_resolved` | conditions 计算后 (:114) | `task`, `req`, `group_id`, `conditions` (list), `condition_data`, `daily_multiplier`, `cur` |
| **`after_points_calculated`** ★ | final_points 确定后，DB 写前 (:121) | `task`, `req`, `group_id`, `today`, `now`, `final_points`, `daily_multiplier`, `conditions`, `condition_data`, `condition_extra`, `multiplier_pct`, `cur` |
| `after_task_status_updated` | UPDATE tasks 后 (:128) | `task`, `req`, `group_id`, `now`, `final_points`, `result_message`, `cur` |
| `after_points_distributed` | UPDATE children 后 (:136) | `task`, `req`, `group_id`, `now`, `final_points`, `child_id` (resolved), `cur` |
| `after_point_log_created` | INSERT point_logs 后 (:151) | `task`, `req`, `group_id`, `now`, `final_points`, `log_id`, `description`, `cur` |
| `after_undo_recorded` | INSERT undo_operations 后 (:171) | `task`, `req`, `group_id`, `undo_data`, `log_id`, `cur` |
| `after_streak_checked` | 连击条件检查后 (:180) | `task`, `req`, `group_id`, `effective_child`, `streak_results` (list, **被丢弃**), `cur` |
| `after_taskset_checked` | 任务集条件检查后 (:181) | `task`, `req`, `group_id`, `effective_child`, `taskset_results` (list, **被丢弃**), `cur` |
| `after_medal_awarded` | 奖章发放后 (:186) | `task`, `req`, `group_id`, `effective_child`, `medal_count` (int, **被丢弃**), `cur` |
| **`after_task_complete_commit`** ★ | commit 后 (:190) | `task`, `req`, `group_id`, `final_points`, `log_id`, `streak_results`, `taskset_results`, `child` (新余额), `result_message` |
| `on_task_complete_error` | except 块 (:204-209) | `task`, `req`, `group_id`, `exception` |

**关键缺口**：
- `streak_results`, `taskset_results`, `medal_count` 三个返回值**直接丢弃**，hook 是唯一消费渠道
- `check_streak_on_complete` 和 `check_taskset_on_complete` 各自直接写 `children.total_points` 和 `point_logs`，但**不创建 undo 记录**
- `after_task_complete_commit` 是唯一安全做外部通知的点（事务已提交）

### 1.3 task.delete — 删除任务

| 项目 | 内容 |
|------|------|
| **位置** | `api/routes/tasks.py:214-227` |
| **当前侧效应** | DELETE `child_condition_acceptances` |
| **undo 记录** | 无 |

| Hook 事件 | 时机 | 可用 kwargs |
|-----------|------|------------|
| `before_task_delete` | SELECT 后，DELETE 前 (:222) | `task_id`, `group_id`, `task` (dict, 所有列), `cur` |
| `after_task_delete` | commit 后 (:227) | `task_id`, `group_id` |

**缺口**：无 undo 记录，无法恢复。删除任务不影响已发放积分/奖章/连击进度。

### 1.4 task.edit — 编辑任务（管理端）

| 项目 | 内容 |
|------|------|
| **位置** | `api/routes/admin.py:1240-1290` |
| **当前侧效应** | 无（不改 `daily_task_boosts` cache） |
| **undo 记录** | 有 (task_edit) |

| Hook 事件 | 时机 | 可用 kwargs |
|-----------|------|------------|
| `before_task_edit` | SELECT 后，UPDATE 前 (:1273) | `task` (旧值), `name`, `emoji`, `base_points`, `description`, `is_repeatable`, `group_id`, `cur` |
| `after_task_edit` | commit 后 (:1289) | `task_id`, `updated_task`, `group_id`, `undo_data` |

**缺口**：改 `base_points` 后不清除当日 boost cache，已生成的 boost multiplier 仍是旧的。

---

## 二、奖励生命周期 (Reward Lifecycle)

### 2.1 reward.create — 创建奖励

| 项目 | 内容 |
|------|------|
| **位置** | `api/routes/rewards.py:48-65` |
| **当前侧效应** | 无 |
| **undo 记录** | 无 |

| Hook 事件 | 时机 | 可用 kwargs |
|-----------|------|------------|
| `before_reward_create` | 校验后，INSERT 前 (:57) | `req`, `group_id`, `cur` |
| `after_reward_create` | commit 后 (:65) | `reward` (dict), `group_id` |

### 2.2 reward.delete — 删除奖励

| 项目 | 内容 |
|------|------|
| **位置** | `api/routes/rewards.py:166-177` |
| **当前侧效应** | CASCADE: `daily_reward_pricing`, `daily_pricing_overrides` |
| **undo 记录** | 无 |

| Hook 事件 | 时机 | 可用 kwargs |
|-----------|------|------------|
| `before_reward_delete` | SELECT 后，DELETE 前 (:174) | `reward` (dict), `group_id`, `cur` |
| `after_reward_delete` | commit 后 (:177) | `reward_id`, `group_id` |

**缺口**：奖励曾被兑换过（`point_logs` 中引用了 `reward_id`），删除后变成孤儿引用。每日定价缓存不失效。

### 2.3 reward.redeem — 兑换奖励（高复杂度）

| 项目 | 内容 |
|------|------|
| **位置** | `api/routes/rewards.py:68-163` |
| **当前侧效应** | `ensure_daily_pricing`(:102)，`compute_effective_price`(:117，纯函数)，`apply_coupon`(:160，UPDATE coupons)，`UPDATE users`(:168，legacy) |
| **undo 记录** | 有 (redeem_reward) |

| Hook 事件 | 时机 | 可用 kwargs |
|-----------|------|------------|
| `before_redeem_validate` | DB 连接后 (:73) | `req`, `group_id`, `cur` |
| `after_reward_fetched` | SELECT reward 后 (:80) | `reward` (dict), `req`, `group_id`, `cur` |
| `after_child_fetched` | SELECT child 后 (:86) | `reward`, `child` (dict), `req`, `group_id`, `cur` |
| `after_coupon_validated` | coupon 校验后 (:97) | `reward`, `child`, `coupon` (dict or None), `req`, `group_id`, `cur` |
| `after_pricing_resolved` | pricing lookup 后 (:103) | `reward`, `child`, `coupon`, `pricing`, `info`, `group_id`, `cur` |
| **`after_cost_calculated`** ★ | 价格确定后，余额检查前 (:123) | `reward`, `child`, `cost`, `rate`, `coupon`, `coupon_desc`, `group_id`, `req`, `cur` |
| **`before_redeem_write`** ★ | 余额校验通过，DB 写前 (:134) | `reward`, `child`, `cost`, `rate`, `coupon`, `group_id`, `req`, `cur` |
| `after_points_deducted` | UPDATE children 后 (:138) | `reward`, `child`, `cost`, `rate`, `coupon`, `group_id`, `cur` |
| `after_redeem_log_created` | INSERT point_logs 后 (:153) | `reward`, `child`, `cost`, `log_id`, `coupon`, `group_id`, `cur` |
| `after_coupon_applied` | apply_coupon 后 (:161) | `reward`, `child`, `cost`, `coupon`, `group_id`, `cur` |
| `after_redeem_undo_recorded` | INSERT undo_operations 后 (:166) | `reward`, `child`, `cost`, `undo_data`, `coupon`, `group_id`, `cur` |
| **`after_redeem_commit`** ★ | commit 后 (:170) | `reward`, `child` (新余额), `cost`, `log_id`, `coupon`, `group_id` |
| `on_redeem_error` | except 块 (:178-183) | `reward`, `req`, `group_id`, `exception` |

**关键缺口**：
- **并发风险**：`UPDATE children SET total_points = total_points - cost` 无 `SELECT FOR UPDATE`，两个并发兑换可能都通过余额检查
- `now_cst()` 调了两次（:100 用于定价，:137 用于日志），时间不一致
- `undo_data` 缺少 `reward_id`
- Legacy `users` 表更新 (:168) 不被 undo 覆盖
- `apply_coupon` 返回值 `{"success": True}` **被丢弃**，静默失败

### 2.4 reward.list — 列举奖励（含定价注入）

| 项目 | 内容 |
|------|------|
| **位置** | `api/routes/rewards.py:15-45` |
| **当前侧效应** | `ensure_daily_pricing`(:22，可能 UPSERT) |
| **undo 记录** | 无 |

| Hook 事件 | 时机 | 可用 kwargs |
|-----------|------|------------|
| `after_pricing_generated` | ensure_daily_pricing 后 (:22) | `group_id`, `today`, `cur` |
| `after_rewards_listed` | 响应前 (:45) | `result` (enriched list), `pricing` (dict), `group_id` |

---

## 三、管理端 (Admin Operations)

### 3.1 密码相关

| 端点 | 位置 | Hook 事件 | 可用 kwargs |
|------|------|-----------|------------|
| POST /setup | `admin.py:37` | `admin.password_setup` | `password`, `success`, `token`, `cur` |
| POST /reset | `admin.py:59` | `admin.password_reset` | `old_hash`, `cur` |
| POST /change-password | `admin.py:83` | `admin.password_change` | `old_hash`, `new_hash`, `cur` |
| POST /login | `admin.py:102` | `admin.login` | `password`, `matched_key`, `success`, `token`, `upgraded_hash` (bool) |

### 3.2 积分操作

| 操作 | 位置 | undo 类型 | Hook 事件 | 可用 kwargs |
|------|------|-----------|-----------|------------|
| POST /points | `admin.py:159` | `manual_edit` | `admin.points_manual_change` | `child_id`, `group_id`, `mode`, `value`, `description`, `previous_points`, `new_points`, `log_id`, `cur` |
| POST /logs | `admin.py:236` | `manual_log_add` | `admin.log_manual_add` | `child_id`, `group_id`, `action`, `amount`, `description`, `actual_deducted`, `log_id`, `cur` |
| DELETE /logs/{id} | `admin.py:296` | `manual_log_delete` | `admin.log_soft_delete` | `log_id`, `log` (dict), `group_id`, `child_id`, `cur` |

### 3.3 回滚操作

| 操作 | 位置 | Hook 事件 | 可用 kwargs |
|------|------|-----------|------------|
| POST /undo/{id} | `admin.py:368` | **`admin.undo_executed`** ★ | `operation_id`, `op` (dict), `op_type`, `undo_data`, `cur` |

**`admin.undo_executed` 是最高价值的管理端 hook**，因为每次 undo 都需要被审计。

**undo handler 覆盖的 13 种操作类型**：
`manual_edit`, `manual_log_add`, `manual_log_delete`, `task_complete`, `redeem_reward`, `punish`, `borrow_loan`, `repay_loan`, `boost_override_change`, `condition_override_change`, `surge_override_change` (legacy), `pricing_override_change`, `task_edit`

**undo handler 未覆盖的操作**（这些操作从未创建 undo 记录）：条件 CRUD、任务/奖励 CRUD、loan_settings 变更、simulated_time 变更

### 3.4 配置变更

| 端点 | 位置 | undo 记录 | Hook 事件 | 可用 kwargs |
|------|------|-----------|-----------|------------|
| POST /loan-settings | `admin.py:605` | 无 | `admin.loan_settings_change` | `interest_rate`, `max_amount`, `old_interest_rate`, `old_max_amount`, `cur` |
| POST /simulated-time | `admin.py:726` | 无 | `admin.simulated_time_change` | `time_str`, `previous_time`, `simulated` (bool), `message`, `cur` |
| DELETE /groups/{id} | `admin.py:656` | 无（且不可 undo） | **`admin.before_group_delete`** ★ | `group_id`, `cur` |
| DELETE /children/{id} | `admin.py:686` | 无 | **`admin.before_child_delete`** ★ | `child_id`, `group_id`, `child_name`, `cur` |

**缺口**：
- **Group delete 是最危险的操作**，无 undo，先删 `undo_operations` 再删所有数据。且缺失 cascade 的表（`coupons`, `daily_condition_selections` 等）会成为孤儿。
- `simulated_time` 调用 `set_simulated_time()` 修改**进程内存中的全局状态**（:740-745），在 DB commit 之前（:752），rollback 会导致不一致。

### 3.5 覆盖类操作（Override CRUD）

这三个覆盖端点模式一致，都有 undo 记录，都清除了当天缓存：

| 端点 | 位置 | undo 类型 | Hook 事件 | 清理的表 |
|------|------|-----------|-----------|---------|
| POST /boost-overrides | `admin.py:782` | `boost_override_change` | `admin.boost_override_change` | `daily_task_boosts` |
| POST /pricing-overrides | `admin.py:853` | `pricing_override_change` | `admin.pricing_override_change` | `daily_reward_pricing` |
| POST /condition-overrides | `admin.py:1137` | `condition_override_change` | `admin.condition_override_change` | `daily_condition_selections` |

**可用 kwargs**：`group_id`, `resource_id` (task_id/reward_id/condition_id), `override_type`, `*_params`, `duration_days`, `old_override` (dict), `result`, `cur`

### 3.6 条件管理（缺 undo）

| 端点 | 位置 | undo 记录 | Hook 事件 | 可用 kwargs |
|------|------|-----------|-----------|------------|
| POST /conditions | `admin.py:944` | 无 | `admin.condition_create` | `group_id`, `name`, `reward_type`, `condition_type`, `bonus_value`, `multiplier_value`, `streak_days`, `subset_size`, `task_ids`, `condition_id`, `cur` |
| PUT /conditions/{id} | `admin.py:1020` | 无 | `admin.condition_update` | `condition_id`, `group_id`, 所有更新字段, `old_condition` (dict), `cur` |
| DELETE /conditions/{id} | `admin.py:1095` | 无 | `admin.condition_delete` | `condition_id`, `group_id`, `name`, `cur` |

**缺口**：条件创建/更新/删除都没有 undo 记录。条件更新使用 DELETE+INSERT 模式更新 bindings（非原子操作）。

### 3.7 任务/奖励管理（缺 undo）

| 端点 | 位置 | undo 记录 | Hook 事件 | 可用 kwargs |
|------|------|-----------|-----------|------------|
| POST /groups/{id}/tasks | `admin.py:1197` | 无 | `admin.task_add` | `group_id`, `name`, `emoji`, `base_points`, `is_repeatable`, `description`, `child_id`, `task_id`, `cur` |
| DELETE /groups/{id}/tasks/{id} | `admin.py:1225` | 无 | `admin.task_delete` | `group_id`, `task_id`, `task` (dict), `cur` |
| POST /groups/{id}/rewards | `admin.py:1305` | 无 | `admin.reward_add` | `group_id`, `name`, `emoji`, `cost_points`, `reward_id`, `cur` |
| DELETE /groups/{id}/rewards/{id} | `admin.py:1330` | 无 | `admin.reward_delete` | `group_id`, `reward_id`, `cur` |

---

## 四、贷款生命周期 (Loan Lifecycle)

### 4.1 loan.borrow — 借款

| 项目 | 内容 |
|------|------|
| **位置** | `api/routes/loans.py:93-175` |
| **当前侧效应** | `check_loan_eligibility` (loan_service，读 children/loans)，`UPDATE children.total_points`，`INSERT point_logs`，`UPDATE users` (legacy) |
| **undo 记录** | 有 (borrow_loan) |

| Hook 事件 | 时机 | 可用 kwargs |
|-----------|------|------------|
| `before_loan_borrow` | 校验前 (:96) | `req`, `group_id`, `cur` |
| `after_loan_eligibility` | 资格检查后 (:118) | `req`, `group_id`, `child_id`, `eligibility` (dict), `max_amount`, `interest_rate`, `cur` |
| `after_loan_created` | INSERT loans 后 (:133) | `req`, `group_id`, `child_id`, `loan_id`, `amount`, `interest_rate`, `cur` |
| `after_loan_points_added` | UPDATE children 后 (:138) | `req`, `group_id`, `child_id`, `loan_id`, `amount`, `cur` |
| `after_loan_log_created` | INSERT point_logs 后 (:146) | `req`, `group_id`, `child_id`, `loan_id`, `amount`, `log_id`, `cur` |
| `after_loan_undo_recorded` | INSERT undo_operations 后 (:154) | `req`, `group_id`, `child_id`, `loan_id`, `amount`, `undo_data`, `cur` |
| **`after_loan_borrow_commit`** ★ | commit 后 (:156) | `req`, `group_id`, `child_id`, `loan_id`, `amount`, `new_balance` |

### 4.2 loan.repay — 还款

| 项目 | 内容 |
|------|------|
| **位置** | `api/routes/loans.py:178-300` |
| **当前侧效应** | `apply_repayment` (纯计算)，`calculate_credit_change` (纯计算)，`UPDATE children.total_points`，`UPDATE loans`，`UPDATE children.credit_score`，`INSERT point_logs`，`UPDATE users` |
| **undo 记录** | 有 (repay_loan) |

| Hook 事件 | 时机 | 可用 kwargs |
|-----------|------|------------|
| `before_loan_repay` | 校验前 (:181) | `req`, `group_id`, `cur` |
| `after_loan_fetched` | SELECT loan 后 (:195) | `loan` (dict), `req`, `group_id`, `cur` |
| `after_child_fetched_repay` | SELECT child 后 (:202) | `loan`, `child` (dict), `req`, `group_id`, `cur` |
| `after_repayment_calculated` | `apply_repayment` 后 (:205) | `loan`, `child`, `req`, `result` (dict: principal, interest, remaining), `cur` |
| **`after_loan_points_deducted`** ★ | UPDATE children 后 (:217) | `loan`, `child`, `req`, `result`, `cur` |
| `after_loan_updated` | UPDATE loans 后 (:227) | `loan`, `child`, `req`, `result`, `cur` |
| `after_credit_score_updated` | UPDATE credit_score 后 (:238) | `loan`, `child`, `req`, `credit_change` (int or None), `cur` |
| `after_repay_log_created` | INSERT point_logs 后 (:259) | `loan`, `child`, `req`, `result`, `log_id`, `cur` |
| `after_repay_undo_recorded` | INSERT undo_operations 后 (:277) | `loan`, `child`, `req`, `result`, `undo_data`, `cur` |
| **`after_loan_repay_commit`** ★ | commit 后 (:279) | `loan`, `child` (新余额/新 credit), `req`, `result` |

### 4.3 cron.refresh_loans — 定时利息/信用衰减

| 项目 | 内容 |
|------|------|
| **位置** | `api/routes/logs.py:21-42` → `loan_service.refresh_loans` |
| **当前侧效应** | UPDATE `loans.accrued_interest` + `last_interest_at`，UPDATE `children.credit_score` -= decay，UPDATE `loans.last_credit_decay_at` |
| **undo 记录** | 无 |

| Hook 事件 | 时机 | 可用 kwargs |
|-----------|------|------------|
| `before_cron_loan_refresh` | 加载活跃贷款后 (:35) | `loans` (list of dicts), `now`, `cur` |
| `per_loan_interest_accrued` | 每条贷款利息计息后 (`loan_service.py:198`) | `loan` (dict), `interest_added` (bool), `cur` |
| `per_loan_credit_decayed` | 每条贷款信用衰减后 (`loan_service.py:229`) | `loan` (dict), `decay_days` (int), `cur` |
| `after_cron_loan_refresh_commit` | commit 后 (:36) | `loans`, `now` |

**缺口**：利息增加和信用衰减不创建 point_log，不创建 undo 记录，不可见。

---

## 五、奖章/优惠券生命周期 (Medal/Coupon Lifecycle)

### 5.1 medal.award — 奖章发放（任务完成触发）

| 项目 | 内容 |
|------|------|
| **位置** | `api/routes/tasks.py:186` → `medal_service.award_medal` (:25) |
| **当前侧效应** | UPSERT `daily_medals` |
| **undo 记录** | 无 |

| Hook 事件 | 时机 | 可用 kwargs |
|-----------|------|------------|
| `on_medal_awarded` | UPSERT 后 | `cur`, `child_id`, `group_id`, `medal_date`, `new_count` (int) |

**缺口**：`award_medal` 返回值 `count` 在 tasks.py:186 **被丢弃**。undo 不回退奖章。

### 5.2 medal.exchange — 奖章兑换优惠券

| 项目 | 内容 |
|------|------|
| **位置** | `api/routes/medals.py:37-61` → `medal_service.exchange_coupon` (:50) |
| **当前侧效应** | UPDATE `daily_medals.count -= cost`，INSERT `coupons` |
| **undo 记录** | 无 |

| Hook 事件 | 时机 | 可用 kwargs |
|-----------|------|------------|
| `before_medal_exchange` | 校验后 (:45) | `req`, `child_id`, `group_id`, `cur` |
| `after_medal_exchange` | exchange_coupon 后 (:48) | `req`, `child_id`, `group_id`, `coupon_id`, `medals_remaining`, `coupon_type`, `discount_pct`, `cur` |
| **`after_medal_exchange_commit`** ★ | commit 后 (:49) | `req`, `child_id`, `group_id`, `coupon_id`, `medals_remaining` |

**缺口**：无 undo 记录。奖章扣除 + 优惠券创建不可逆。

### 5.3 coupon.discard — 丢弃优惠券

| 项目 | 内容 |
|------|------|
| **位置** | `api/routes/medals.py:75-89` |
| **当前侧效应** | DELETE coupons (硬删除) |
| **undo 记录** | 无 |

| Hook 事件 | 时机 | 可用 kwargs |
|-----------|------|------------|
| `before_coupon_discard` | DELETE 前 (:82) | `coupon_id`, `child_id`, `group_id`, `coupon` (dict), `cur` |
| `after_coupon_discard` | commit 后 (:89) | `coupon_id`, `child_id`, `group_id` |

### 5.4 coupon.apply — 优惠券使用（兑换奖励触发）

| 项目 | 内容 |
|------|------|
| **位置** | `api/routes/rewards.py:160` → `medal_service.apply_coupon` (:97) |
| **当前侧效应** | UPDATE `coupons SET used = true` |
| **undo 记录** | 有（作为 redeem_reward undo 的一部分，admin.py:441-451） |

| Hook 事件 | 时机 | 可用 kwargs |
|-----------|------|------------|
| `on_coupon_used` | UPDATE coupons 后 | `cur`, `coupon_id`, `reward_id`, `child_id`, `group_id`, `now` |

**缺口**：`apply_coupon` 返回值 `{"success": True}` **被丢弃**。

---

## 六、群组/孩子生命周期 (Group/Child Lifecycle)

### 6.1 group.create — 创建群组

| 项目 | 内容 |
|------|------|
| **位置** | `api/routes/group.py:18-47` |
| **当前侧效应** | 插入 `family_groups` + `children` |
| **undo 记录** | 无 |
| **错误处理** | 无 try/except |

| Hook 事件 | 时机 | 可用 kwargs |
|-----------|------|------------|
| `before_group_create` | INSERT 前 (:26) | `name`, `child_name`, `invite_code`, `now`, `cur` |
| `after_group_create` | commit 前 (:38) | `group_id`, `child_id`, `invite_code`, `now` |

**缺口**：两个 INSERT 之间如果第二句失败，第一句没有 rollback（隐式事务）。

### 6.2 child.add — 添加孩子

| 项目 | 内容 |
|------|------|
| **位置** | `api/routes/children.py:12-29` |
| **当前侧效应** | INSERT children |
| **undo 记录** | 无 |
| **错误处理** | 无 try/except |

| Hook 事件 | 时机 | 可用 kwargs |
|-----------|------|------------|
| `before_child_add` | 校验后 (:17) | `name`, `emoji`, `group_id`, `cur` |
| `after_child_add` | commit 后 (:29) | `child_id`, `name`, `emoji`, `group_id` |

---

## 七、定时/系统事件 (Cron/System Events)

### 7.1 每日翻倍刷新 (Daily Boost Generation)

| 项目 | 内容 |
|------|------|
| **触发点** | `boost_service.py:145-154` → 任意任务列举 (/tasks, /tasks/loop) + /admin/logs cron |
| **侧效应** | UPSERT `daily_task_boosts` |
| **undo 记录** | 无 |

| Hook 事件 | 时机 | 可用 kwargs |
|-----------|------|------------|
| `on_daily_boost_generate` | save_daily_boosts 后 | `cur`, `group_id`, `date`, `boosts` (list of dicts) |

### 7.2 每日悬赏刷新 (Daily Condition Generation)

| 项目 | 内容 |
|------|------|
| **触发点** | `condition_service.py:85-99` → 任务列举/完成 + /admin/logs cron |
| **侧效应** | INSERT `daily_condition_selections` |
| **undo 记录** | 无 |

| Hook 事件 | 时机 | 可用 kwargs |
|-----------|------|------------|
| `on_daily_condition_generate` | save_daily_conditions 后 | `cur`, `group_id`, `date`, `conditions` (list of dicts) |

### 7.3 每日定价刷新 (Daily Pricing Generation)

| 项目 | 内容 |
|------|------|
| **触发点** | `pricing_service.py:ensure_daily_pricing` → 奖励列举/兑换 |
| **侧效应** | UPSERT `daily_reward_pricing` |
| **undo 记录** | 无 |

| Hook 事件 | 时机 | 可用 kwargs |
|-----------|------|------------|
| `on_daily_pricing_generate` | save_daily_pricing 后 | `cur`, `group_id`, `date`, `pricing_map` (dict[reward_id, params]) |

### 7.4 条件刷新 (手动刷新悬赏)

| 项目 | 内容 |
|------|------|
| **触发点** | `condition_service.py:521-629` → POST /tasks/refresh-conditions |
| **侧效应** | UPDATE children (扣分), INSERT point_logs, INSERT condition_refresh_log, DELETE/INSERT daily_condition_selections, DELETE condition_task_set_progress |
| **undo 记录** | 无 |

| Hook 事件 | 时机 | 可用 kwargs |
|-----------|------|------------|
| `on_condition_refresh` | refresh_daily_conditions 后 | `cur`, `group_id`, `child_id`, `today`, `condition_id`, `point_cost`, `new_conditions` (list) |

**缺口**：付费刷新扣分不创建 undo 记录。

### 7.5 连击/任务集处罚与奖励

| 项目 | 内容 |
|------|------|
| **触发点** | `condition_service.py:224-402` → 任务完成时 |
| **侧效应** | UPDATE children (加分/扣分), INSERT point_logs, UPDATE progress |
| **undo 记录** | 无 |

| Hook 事件 | 时机 | 可用 kwargs |
|-----------|------|------------|
| `on_streak_penalty` | 连击断签扣分后 | `cur`, `child_id`, `group_id`, `condition_id`, `penalty_points`, `today` |
| `on_streak_completed` | 连击条件满足奖励后 | `cur`, `child_id`, `group_id`, `condition_id`, `bonus_points`, `today` |
| `on_taskset_completed` | 任务集条件满足奖励后 | `cur`, `child_id`, `group_id`, `condition_id`, `bonus_points`, `today` |

**缺口**：这三种积分变更没有 undo 记录，undo task_complete 时不恢复。

---

## 八、跨模块调用图

```
tasks.complete
├── boost_service.ensure_daily_boosts         → daily_task_boosts
├── condition_service.ensure_daily_conditions  → daily_condition_selections
├── condition_service.get_task_conditions      → (reads only)
├── condition_service.calculate_condition_result → (pure)
├── point_service.calculate_final_points       → (pure)
├── condition_service.check_streak_on_complete → children, point_logs, condition_streak_progress
├── condition_service.check_taskset_on_complete → children, point_logs, condition_task_set_progress
└── medal_service.award_medal                  → daily_medals

rewards.redeem
├── pricing_service.ensure_daily_pricing       → daily_reward_pricing
├── pricing_service.get_todays_pricing         → (reads only)
├── medal_service.compute_effective_price       → (pure)
└── medal_service.apply_coupon                 → coupons

loans.borrow
├── loan_service.get_max_amount                → (reads admin_settings)
├── loan_service.get_interest_rate             → (reads admin_settings)
└── loan_service.check_loan_eligibility        → (reads children, loans)

loans.repay
├── loan_service.apply_repayment               → (pure)
└── loan_service.calculate_credit_change       → (pure)

cron.refresh_loans
└── loan_service.refresh_loans                 → loans, children.credit_score

cron.refresh_all
├── boost_service.ensure_daily_boosts
├── condition_service.ensure_daily_conditions
├── pricing_service.ensure_daily_pricing       (NOT currently called, but should be)
└── loan_service.refresh_loans
```

---

## 九、优先级汇总

### Tier 1 — 高价值（影响多个模块 / 现有返回值被丢弃 / 安全关键）

| # | 事件 | 理由 |
|---|------|------|
| 1 | `after_task_complete_commit` | 唯一安全通知点，streak/taskset/medal 结果在此消费 |
| 2 | `after_redeem_commit` | 奖励兑换成功通知 |
| 3 | `before_redeem_write` | 可阻止兑换，可覆盖价格 |
| 4 | `admin.undo_executed` | 每次回滚需审计 |
| 5 | `admin.before_group_delete` | 群组删除前最后拦截点 |
| 6 | `on_medal_awarded` | 返回值在 tasks.py 被丢弃 |
| 7 | `on_streak_penalty` / `on_streak_completed` | 积分变动但无 undo，需外部追踪 |
| 8 | `on_taskset_completed` | 同上 |

### Tier 2 — 重要（覆盖现有缺乏 undo 的操作）

| # | 事件 | 理由 |
|---|------|------|
| 9 | `after_medal_exchange_commit` | 无 undo，需外部记录 |
| 10 | `before_coupon_discard` | 硬删除，不可逆 |
| 11 | `admin.condition_create/update/delete` | 条件 CRUD 均无 undo |
| 12 | `admin.task_add/delete` | 任务 CRUD 均无 undo |
| 13 | `admin.reward_add/delete` | 奖励 CRUD 均无 undo |
| 14 | `admin.simulated_time_change` | 全局状态 + DB 写入时序不一致 |
| 15 | `on_condition_refresh` | 付费刷新扣分无 undo |

### Tier 3 — 日常使用（CRUD 审计 / 错误追踪）

| # | 事件 | 理由 |
|---|------|------|
| 16 | `on_task_complete_error` | 当前无错误记录 |
| 17 | `on_redeem_error` | 当前无错误记录 |
| 18 | `after_points_calculated` | 可修改积分（如节假日加成） |
| 19 | `after_cost_calculated` | 可修改兑换价格 |
| 20 | `after_rewards_listed` | 可注入额外奖励信息 |
| 21 | `on_daily_boost_generate` | 翻倍生成审计 |
| 22 | `on_daily_condition_generate` | 悬赏生成审计 |
| 23 | `on_daily_pricing_generate` | 定价生成审计 |
| 24 | `before_group_create` | 群组创建拦截 |
| 25 | `before_child_add` | 孩子添加拦截 |
| 26 | `per_loan_interest_accrued` | 利息计息审计 |
| 27 | `per_loan_credit_decayed` | 信用衰减审计 |
| 28 | `admin.loan_settings_change` | 贷款配置变更审计 |

### Tier 4 — 低优先（基础设施 / 可选）

| # | 事件 | 理由 |
|---|------|------|
| 29 | `before_task_create` | 简单操作 |
| 30 | `before_task_delete` | 简单操作 |
| 31 | `after_task_edit` | 已有 undo |
| 32 | `before_reward_create` | 简单操作 |
| 33 | `before_reward_delete` | 简单操作 |
| 34 | `admin.boost_override_change` | 已有 undo + cache 失效 |
| 35 | `admin.pricing_override_change` | 已有 undo + cache 失效 |
| 36 | `admin.condition_override_change` | 已有 undo + cache 失效 |
| 37 | `admin.password_*` | 低频操作 |
| 38 | `admin.login` | 已有 JWT |
| 39 | `admin.points_manual_change` | 已有 undo |
| 40 | `admin.log_manual_add` | 已有 undo |
| 41 | `cron.refresh_loans` | 低频定时 |
| 42 | `task.accept_condition` | 简单 INSERT |

---

## 十、实施建议

### 分阶段实施

**Phase 3a (核心)**：实现 `api/hooks.py` + 在 2 个最重要的地方插入 `emit`：
- `after_task_complete_commit` (tasks.py:190 后)
- `after_redeem_commit` (rewards.py:170 后)

**Phase 3b (扩展)**：在所有 Tier 1 + Tier 2 hook 点插入 `emit`。

**Phase 3c (完善)**：覆盖 Tier 3 + Tier 4。

### 插入模板

```python
# 事务内 hook（可访问 cur）
from api.hooks import emit
emit("after_points_calculated",
     task=task, req=req, group_id=group_id,
     final_points=final_points, daily_multiplier=daily_multiplier,
     conditions=conditions, cur=cur)

# 事务外 hook（commit 后）
emit("after_task_complete_commit",
     task=task, group_id=group_id,
     final_points=final_points, log_id=log_id,
     streak_results=streak_results, taskset_results=taskset_results,
     child=child)
```

### 跨模块循环引用处理

`emit` 不应在模块加载时触发任何 import。hook handler 注册使用 lazy import 或在 `api/hooks.py` 层面统一管理：

```python
# 在 api/hooks.py 末尾
def _auto_register_builtin_hooks():
    """延迟导入内置 hook，避免循环引用。"""
    from api.hooks.medal_hooks import register as _medal
    from api.hooks.audit_hooks import register as _audit
    _medal()
    _audit()

# 在 app startup 时调用
```

### 文件规划

```
api/
├── hooks.py              # 核心注册器 + emit
├── hooks/
│   ├── __init__.py
│   ├── medal_hooks.py    # 奖章相关 hook（替代 inline import）
│   ├── audit_hooks.py    # 审计日志 hook
│   └── notification_hooks.py  # 通知 hook（未来）
└── routes/
    ├── tasks.py          # 插入 emit 调用
    ├── rewards.py
    ├── loans.py
    ├── medals.py
    └── admin.py
```
