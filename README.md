# 儿童作业任务与积分兑换商城系统

家庭任务管理与积分奖励系统，让孩子通过完成任务赚积分、兑换奖励，培养责任感和成就感。

**技术栈：** FastAPI + PostgreSQL (Supabase) + Alpine.js / 部署：Vercel

## 设计理念

- **零登录** — 群组 URL 即访问凭据，`/g/{invite_code}` 收藏即用、分享即协作
- **多孩子支持** — 一个家庭群组可添加多个孩子，各自独立积分
- **星级评级** — 完成任务后 1-5 星评价，积分按比例折算（50%-120%）
- **惩罚冷静期** — 扣分有 10 分钟 / 1 小时 / 24 小时三档上限，避免情绪化操作
- **限时翻倍** — 每日随机3个任务获得 1.5x-2.0x 倍率，连续中奖权重衰减，admin 可覆盖
- **悬赏条件** — 三种类型：接受挑战（弹窗自评 pass/fail）、连续打卡（连续 N 天完成奖励/中断扣分）、任务集合（同一天完成指定/随机任务集奖励），每日随机 4 选
- **撤回支持** — 所有操作可撤销，`undo_operations` 表记录完整上下文
- **贷款系统** — 可借积分应急，日利率单利计息，按时还款积累信用分提升贷款额度

## 快速启动

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入 Supabase session pooler 连接字符串
# DATABASE_URL=postgresql://postgres.xxx:password@aws-0-xxx.pooler.supabase.com:5432/postgres
```

本机开发也可用本地 PostgreSQL，不设 `DATABASE_URL` 时默认连接 `postgresql://localhost:5432/kids_rewards`。

### 3. 启动后端

```bash
python api/main.py
```

服务启动后访问：`http://localhost:8000`

首次访问会自动初始化表结构并插入示例任务和奖励。

### 4. 运行测试

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

## 文件结构

```
.
├── api/
│   ├── main.py             # FastAPI 入口（注册路由、启动初始化）
│   ├── config.py           # 环境变量、时区、积分折算常量
│   ├── dependencies.py     # FastAPI 依赖注入（X-Group-Code → group_id）
│   ├── admin_auth.py       # Admin 密码哈希 + JWT token 签发/验证
│   ├── models/
│   │   ├── database.py     # 数据库连接管理 + init_db() 幂等建表
│   │   └── schemas.py      # Pydantic 请求/响应模型
│   ├── routes/
│   │   ├── group.py        # 群组创建 / 查询（POST/GET /api/groups）
│   │   ├── tasks.py        # 任务 CRUD + 完成评级 + 条件检测
│   │   ├── rewards.py      # 奖励商城 CRUD + 兑换
│   │   ├── children.py     # 孩子档案管理
│   │   ├── logs.py         # 积分流水、惩罚扣分、统计
│   │   └── admin.py        # Admin 面板（密码设置/登录/群组管理/撤回）
│   └── services/
│       ├── point_service.py     # 积分计算（星级 × 基础分 → 最终分）
│       ├── boost_service.py     # 每日翻倍（权重抽样、衰减、覆盖）
│       ├── condition_service.py # 悬赏条件（选取、奖惩、streak/task_set 检测）
│       └── loan_service.py      # 贷款服务（利息、信用分）
├── index.html              # 主前端 SPA（Alpine.js）
├── admin.html              # Admin 管理后台
├── tests/                  # pytest 测试（96 个测试）
│   ├── conftest.py
│   ├── test_smoke.py
│   ├── test_group.py
│   ├── test_tasks.py
│   ├── test_rewards.py
│   ├── test_children.py
│   ├── test_logs.py
│   ├── test_admin.py
│   ├── test_loans.py
│   ├── test_boosts.py        # 限时翻倍测试
│   └── test_conditions.py    # 悬赏条件测试
├── old/                    # 旧版单文件 app.py + index.html（保留对照）
├── requirements.txt
├── requirements-dev.txt
├── vercel.json             # Vercel Serverless 部署配置
└── .env.example
```

## 数据库表结构

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
| `conditions` | 悬赏条件定义（acceptance / streak / task_set_specific / task_set_random） |
| `condition_task_bindings` | 条件 ↔ 任务多对多绑定 |
| `daily_condition_selections` | 每日条件选取（群组 × 日期，advisory lock 防竞态） |
| `child_condition_acceptances` | 孩子接受条件记录（acceptance 类型） |
| `condition_streak_progress` | 连续打卡进度追踪（child × condition，跨天） |
| `condition_task_set_progress` | 任务集合每日进度（child × condition × date） |
| `admin_settings` | Admin 密码哈希、系统配置 |
| `users` | 兼容旧版的单用户表（只读，不再写入） |

核心关系：`family_groups` ← `children` / `tasks` / `rewards` / `point_logs`（全部通过 `group_id` 外键隔离）。

## API 路由

所有路由（除 admin 外）通过 `X-Group-Code` 请求头做群组隔离。

### 群组

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/api/groups` | 创建群组 + 默认孩子 → 返回 invite_code |
| GET | `/api/groups/{invite_code}` | 查询群组信息 + 孩子列表 |

### 任务

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

### 奖励商城

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/api/rewards` | 获取奖励列表（按积分升序） |
| POST | `/api/rewards` | 添加奖励 |
| POST | `/api/rewards/redeem` | 兑换奖励（事务保护，不扣成负数） |
| DELETE | `/api/rewards/{id}` | 删除奖励 |

### 孩子 & 积分

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/api/children` | 添加孩子 |
| GET | `/api/logs` | 积分流水（分页） |
| POST | `/api/punish` | 惩罚扣分（冷静期限制） |

### Admin

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

### 贷款

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/api/loans` | 获取贷款列表（含当前应还总额） |
| GET | `/api/loans/status` | 获取贷款资格（信用分、限额、冷却期） |
| POST | `/api/loans` | 借款 |
| POST | `/api/loans/{id}/repay` | 还款（支持部分还款） |

## 积分折算规则

| 星级 | 折算比例 | 含义 |
|------|----------|------|
| 1 星 | 50% | 敷衍了事 |
| 2 星 | 60% | 还需努力 |
| 3 星 | 80% | 基本完成 |
| 4 星 | 100% | 完成得很好 |
| 5 星 | 120% | 超额完成，超级加倍 |

## 惩罚冷静期

| 时间窗口 | 累计扣分上限 |
|----------|-------------|
| 10 分钟 | 10 分 |
| 1 小时 | 25 分 |
| 24 小时 | 100 分 |

## 贷款规则

### 计息方式

- **日利率** — 默认 5%/天，admin 可在后台调整（0-100%）
- **单利不滚利** — 利息 = 剩余本金 × 日利率 × 天数，不计复利
- **还款优先抵本金** — 支付金额先扣本金，本金清零后再抵利息
- **支持部分还款** — 还任意金额，剩余本金减少后后续利息按新本金计算

### 信用分

每个孩子初始信用分 **100**，全额还清时更新：

| 还款时间 | 信用分变化 |
|----------|-----------|
| 1 天内 | +5 |
| 2 天 | +4 |
| 3 天 | +3 |
| ... | ... |
| 6 天及以上 | 递减至负数 |

### 信用分权益（基准 100）

| 信用分 | 冷却期 | 每周次数 | 最高额度 |
|--------|--------|----------|----------|
| 0-49 | 28 天 | 1 次 | 50 |
| 50-99 | 14 天 | 1 次 | 100 |
| 100-149 | 7 天 | 1 次 | 200 |
| 150-199 | 7 天 | 2 次 | 400 |
| 200+ | 7 天 | 4 次 | 800 |

每 +50 信用：额度翻倍、每周次数翻倍；每 -50 信用：额度减半、冷却期翻倍。

## 部署

项目通过 Vercel Serverless 部署，`vercel.json` 配置了 ASGI 入口。

数据库使用 Supabase PostgreSQL session pooler（`aws-*.pooler.supabase.com:5432`），这是唯一支持免费 IPv4 的 Supabase pooler 模式，兼容 Vercel 的 IPv4-only 免费层。

### 环境变量

| 变量 | 说明 |
|------|------|
| `DATABASE_URL` | PostgreSQL 连接字符串（Supabase session pooler） |
| `ADMIN_JWT_SECRET` | Admin JWT 签名密钥（不设则自动生成随机密钥） |

## 开发约定

- **TDD 驱动** — 每个路由模块对应一个 `tests/test_*.py`
- **旧文件保留** — 重构前的代码移入 `old/`，不删除
- **北京时间** — 所有时间戳使用 UTC+8，`now_cst()` 统一获取
- **幂等建表** — `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ADD COLUMN IF NOT EXISTS`
