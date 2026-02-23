# 🌟 儿童作业任务与积分兑换商城系统

## 快速启动

### 1. 安装依赖
```bash
pip install fastapi uvicorn
```

### 2. 启动后端
```bash
python app.py
```
服务启动后访问：http://localhost:8000

---

## 文件结构
```
.
├── app.py          # Python FastAPI 后端
├── index.html      # 前端页面（放同目录即可访问）
└── kids_rewards.db # SQLite 数据库（自动创建）
```

## 数据库表结构

| 表名 | 说明 |
|------|------|
| `user` | 用户信息与总积分 |
| `tasks` | 任务列表（pending/done） |
| `rewards` | 奖励商城列表 |
| `point_logs` | 积分流水记录 |

## API 路由

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | /api/user | 获取用户积分 |
| GET | /api/tasks | 获取任务列表 |
| POST | /api/tasks | 添加任务 |
| POST | /api/tasks/complete | 完成任务+评级 |
| DELETE | /api/tasks/{id} | 删除任务 |
| GET | /api/rewards | 获取奖励列表 |
| POST | /api/rewards | 添加奖励 |
| POST | /api/rewards/redeem | 兑换奖励 |
| DELETE | /api/rewards/{id} | 删除奖励 |
| GET | /api/logs | 获取流水记录 |

## 积分折算规则
- ⭐ 1星 = 50%
- ⭐⭐ 2星 = 60%
- ⭐⭐⭐ 3星 = 80%
- ⭐⭐⭐⭐ 4星 = 100%
- ⭐⭐⭐⭐⭐ 5星 = 120%（超级加倍！）
