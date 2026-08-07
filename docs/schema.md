# 数据库表结构

核心关系：`family_groups` ← `children` / `tasks` / `rewards` / `point_logs`（全部通过 `group_id` 外键隔离）。

| 表名 | 说明 |
|------|------|
| `family_groups` | 家庭群组（邀请码、名称） |
| `children` | 孩子档案（归属群组、积分） |
| `tasks` | 任务列表（可重复/非重复、归属群组/孩子） |
| `rewards` | 奖励商城（归属群组） |
| `point_logs` | 积分流水（earn/spend/punish） |
| `undo_operations` | 操作历史（JSONB 存储撤回上下文） |
| `loans` | 贷款记录（本金、剩余本金、日利率、累计利息、状态） |
| `daily_task_boosts` | 每日翻倍记录（任务 × 日期 × 倍率） |
| `daily_boost_overrides` | 翻倍覆盖（lock_in / lock_out / manual） |
| `daily_reward_pricing` | 时段定价记录（奖励 × 日期，SHA256 确定性随机） |
| `daily_pricing_overrides` | 定价覆盖（lock_in / lock_out / manual_params） |
| `daily_medals` | 每日奖章记录（孩子 × 日期 × 数量） |
| `coupons` | 优惠券（归属孩子，medal_count 章数，used 状态） |
| `conditions` | 悬赏条件定义（acceptance / streak / task_set_specific / task_set_random） |
| `condition_task_bindings` | 条件 ↔ 任务多对多绑定 |
| `daily_condition_selections` | 每日条件选取（群组 × 日期，advisory lock 防竞态） |
| `child_condition_acceptances` | 孩子接受条件记录（acceptance 类型） |
| `condition_streak_progress` | 连续打卡进度追踪（child × condition，跨天） |
| `condition_task_set_progress` | 任务集合每日进度（child × condition × date） |
| `admin_settings` | Admin 密码哈希、系统配置 |
| `users` | 兼容旧版的单用户表（只读，不再写入） |
