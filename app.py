"""
儿童作业任务与积分兑换商城系统 - 后端 API
技术栈：FastAPI + PostgreSQL (Render 持久化)
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
import math
import os
from datetime import datetime

app = FastAPI(title="儿童积分系统")

# 允许跨域（开发阶段）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 从环境变量获取 PostgreSQL 连接字符串
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/kids_rewards")

# ==================== 数据库初始化 ====================


def get_db():
    """获取 PostgreSQL 数据库连接，返回字典类型游标"""
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn


def init_db():
    """初始化数据库表结构（PostgreSQL 版本）"""
    conn = get_db()
    cur = conn.cursor()

    # 用户/积分表（系统只有一个孩子，用 id=1 的记录作为主体）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '小主人',
            total_points INTEGER NOT NULL DEFAULT 0
        )
    """)

    # 任务表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            emoji TEXT NOT NULL,
            base_points INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP NOT NULL
        )
    """)

    # 奖励商城表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rewards (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            emoji TEXT NOT NULL,
            cost_points INTEGER NOT NULL,
            created_at TIMESTAMP NOT NULL
        )
    """)

    # 积分流水表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS point_logs (
            id SERIAL PRIMARY KEY,
            action TEXT NOT NULL,
            amount INTEGER NOT NULL,
            description TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL
        )
    """)

    # 初始化默认用户（若不存在）
    cur.execute("""
        INSERT INTO users (id, name, total_points)
        VALUES (1, '小主人', 0)
        ON CONFLICT (id) DO NOTHING
    """)

    # 插入默认示例任务（若任务表为空）
    cur.execute("SELECT COUNT(*) FROM tasks")
    if cur.fetchone()['count'] == 0:
        now = datetime.now()
        tasks_data = [
            ("今日阅读30分钟", "📖", 20, "pending", now),
            ("整理房间", "🧹", 15, "pending", now),
            ("完成数学作业", "✏️", 25, "pending", now),
        ]
        cur.executemany(
            "INSERT INTO tasks (name, emoji, base_points, status, created_at) VALUES (%s, %s, %s, %s, %s)",
            tasks_data
        )

    # 插入默认示例奖励（若奖励表为空）
    cur.execute("SELECT COUNT(*) FROM rewards")
    if cur.fetchone()['count'] == 0:
        now = datetime.now()
        rewards_data = [
            ("看30分钟电视", "📺", 30, now),
            ("玩游戏1小时", "🎮", 50, now),
            ("买一包零食", "🍬", 40, now),
            ("周末出去玩", "🎡", 100, now),
        ]
        cur.executemany(
            "INSERT INTO rewards (name, emoji, cost_points, created_at) VALUES (%s, %s, %s, %s)",
            rewards_data
        )

    conn.commit()
    conn.close()


# 启动时初始化表结构
init_db()

# ==================== 数据模型（Pydantic） ====================


class CompleteTaskRequest(BaseModel):
    task_id: int
    star_rating: int  # 1-5星


class AddTaskRequest(BaseModel):
    name: str
    emoji: str
    base_points: int


class AddRewardRequest(BaseModel):
    name: str
    emoji: str
    cost_points: int


class RedeemRewardRequest(BaseModel):
    reward_id: int


class PunishRequest(BaseModel):
    name: str        # 惩罚原因名称
    emoji: str       # 代表 Emoji
    penalty_points: int  # 扣除积分数

# ==================== 积分计算工具函数 ====================


STAR_MULTIPLIERS = {
    1: 0.5,   # 1星 50%
    2: 0.6,   # 2星 60%
    3: 0.8,   # 3星 80%
    4: 1.0,   # 4星 100%
    5: 1.2,   # 5星 120%
}

def calculate_final_points(base_points: int, star_rating: int) -> int:
    """
    根据基础积分和星级，计算最终得分
    结果向下取整，防止出现小数积分
    """
    if star_rating not in STAR_MULTIPLIERS:
        raise ValueError(f"星级必须在1-5之间，收到：{star_rating}")
    multiplier = STAR_MULTIPLIERS[star_rating]
    return math.floor(base_points * multiplier)

# ==================== API 路由 ====================


@app.get("/api/user")
def get_user():
    """获取用户信息和总积分"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = 1")
    user = cur.fetchone()
    conn.close()
    return dict(user) if user else {}


@app.get("/api/tasks")
def get_tasks():
    """获取所有任务（包括已完成）"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM tasks ORDER BY created_at DESC")
    tasks = cur.fetchall()
    conn.close()
    return [dict(t) for t in tasks]


@app.post("/api/tasks")
def add_task(req: AddTaskRequest):
    """添加新任务"""
    if req.base_points <= 0:
        raise HTTPException(status_code=400, detail="基础积分必须大于0")
    if len(req.name.strip()) == 0:
        raise HTTPException(status_code=400, detail="任务名称不能为空")

    conn = get_db()
    cur = conn.cursor()
    now = datetime.now()
    cur.execute(
        "INSERT INTO tasks (name, emoji, base_points, status, created_at) VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (req.name.strip(), req.emoji, req.base_points, "pending", now)
    )
    task_id = cur.fetchone()['id']
    conn.commit()
    cur.execute("SELECT * FROM tasks WHERE id = %s", (task_id,))
    task = cur.fetchone()
    conn.close()
    return dict(task)


@app.post("/api/tasks/complete")
def complete_task(req: CompleteTaskRequest):
    """
    完成任务并评级
    - 校验任务存在且为pending状态
    - 计算最终积分（含星级折算）
    - 更新任务状态、增加总积分、记录流水
    """
    if req.star_rating not in range(1, 6):
        raise HTTPException(status_code=400, detail="星级评分必须在1到5之间")

    conn = get_db()
    cur = conn.cursor()
    try:
        # 查询任务
        cur.execute("SELECT * FROM tasks WHERE id = %s", (req.task_id,))
        task = cur.fetchone()
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        if task["status"] != "pending":
            raise HTTPException(status_code=400, detail="该任务已经完成，不能重复提交")

        # 计算积分
        final_points = calculate_final_points(task["base_points"], req.star_rating)
        multiplier_pct = int(STAR_MULTIPLIERS[req.star_rating] * 100)
        now = datetime.now()

        # 更新任务状态
        cur.execute("UPDATE tasks SET status = 'done' WHERE id = %s", (req.task_id,))

        # 增加总积分
        cur.execute("UPDATE users SET total_points = total_points + %s WHERE id = 1", (final_points,))

        # 记录流水账
        description = f"完成任务「{task['name']}」{req.star_rating}⭐（{multiplier_pct}%）→ +{final_points}分"
        cur.execute(
            "INSERT INTO point_logs (action, amount, description, created_at) VALUES (%s, %s, %s, %s)",
            ("earn", final_points, description, now)
        )

        conn.commit()

        # 返回最新用户信息
        cur.execute("SELECT * FROM users WHERE id = 1")
        user = cur.fetchone()
        return {
            "success": True,
            "earned_points": final_points,
            "total_points": user["total_points"],
            "message": f"太棒了！获得 {final_points} 积分 🎉"
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int):
    """删除任务"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM tasks WHERE id = %s", (task_id,))
    task = cur.fetchone()
    if not task:
        conn.close()
        raise HTTPException(status_code=404, detail="任务不存在")
    cur.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
    conn.commit()
    conn.close()
    return {"success": True}


@app.get("/api/rewards")
def get_rewards():
    """获取所有可兑换奖励"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM rewards ORDER BY cost_points ASC")
    rewards = cur.fetchall()
    conn.close()
    return [dict(r) for r in rewards]


@app.post("/api/rewards")
def add_reward(req: AddRewardRequest):
    """添加新奖励"""
    if req.cost_points <= 0:
        raise HTTPException(status_code=400, detail="所需积分必须大于0")
    if len(req.name.strip()) == 0:
        raise HTTPException(status_code=400, detail="奖励名称不能为空")

    conn = get_db()
    cur = conn.cursor()
    now = datetime.now()
    cur.execute(
        "INSERT INTO rewards (name, emoji, cost_points, created_at) VALUES (%s, %s, %s, %s) RETURNING id",
        (req.name.strip(), req.emoji, req.cost_points, now)
    )
    reward_id = cur.fetchone()['id']
    conn.commit()
    cur.execute("SELECT * FROM rewards WHERE id = %s", (reward_id,))
    reward = cur.fetchone()
    conn.close()
    return dict(reward)


@app.post("/api/rewards/redeem")
def redeem_reward(req: RedeemRewardRequest):
    """
    兑换奖励
    关键防护：使用数据库事务确保积分不会被扣成负数
    """
    conn = get_db()
    cur = conn.cursor()
    try:
        # 查询奖励
        cur.execute("SELECT * FROM rewards WHERE id = %s", (req.reward_id,))
        reward = cur.fetchone()
        if not reward:
            raise HTTPException(status_code=404, detail="奖励不存在")

        # 查询当前积分
        cur.execute("SELECT * FROM users WHERE id = 1")
        user = cur.fetchone()
        current_points = user["total_points"]
        cost = reward["cost_points"]

        if current_points < cost:
            raise HTTPException(
                status_code=400,
                detail=f"积分不够啦，继续加油！💪 当前积分：{current_points}，需要：{cost}，还差：{cost - current_points}"
            )

        now = datetime.now()

        # 扣除积分
        cur.execute("UPDATE users SET total_points = total_points - %s WHERE id = 1", (cost,))

        # 二次校验（理论上不应发生）
        cur.execute("SELECT total_points FROM users WHERE id = 1")
        user_after = cur.fetchone()
        if user_after["total_points"] < 0:
            conn.rollback()
            raise HTTPException(status_code=400, detail="积分异常，兑换失败")

        # 记录消耗流水
        description = f"兑换奖励「{reward['name']}」{reward['emoji']} → -{cost}分"
        cur.execute(
            "INSERT INTO point_logs (action, amount, description, created_at) VALUES (%s, %s, %s, %s)",
            ("spend", cost, description, now)
        )

        conn.commit()

        return {
            "success": True,
            "spent_points": cost,
            "total_points": user_after["total_points"],
            "message": f"兑换成功！{reward['emoji']} 享受你的「{reward['name']}」吧！"
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.delete("/api/rewards/{reward_id}")
def delete_reward(reward_id: int):
    """删除奖励"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM rewards WHERE id = %s", (reward_id,))
    reward = cur.fetchone()
    if not reward:
        conn.close()
        raise HTTPException(status_code=404, detail="奖励不存在")
    cur.execute("DELETE FROM rewards WHERE id = %s", (reward_id,))
    conn.commit()
    conn.close()
    return {"success": True}


@app.post("/api/punish")
def punish_user(req: PunishRequest):
    """
    惩罚扣分
    使用 max(0, current - penalty) 确保积分不会扣成负数
    """
    if req.penalty_points <= 0:
        raise HTTPException(status_code=400, detail="扣分值必须大于0")
    if len(req.name.strip()) == 0:
        raise HTTPException(status_code=400, detail="惩罚原因不能为空")

    conn = get_db()
    cur = conn.cursor()
    try:
        # 查询当前积分
        cur.execute("SELECT total_points FROM users WHERE id = 1")
        user = cur.fetchone()
        current_points = user["total_points"]

        # 计算扣后积分，最低为 0，绝不扣成负数
        new_points = max(0, current_points - req.penalty_points)
        actual_deducted = current_points - new_points  # 实际扣除量（可能小于请求值）

        now = datetime.now()

        # 更新积分
        cur.execute("UPDATE users SET total_points = %s WHERE id = 1", (new_points,))

        # 记录惩罚流水
        description = f"{req.emoji} 惩罚「{req.name.strip()}」→ -{actual_deducted}分"
        cur.execute(
            "INSERT INTO point_logs (action, amount, description, created_at) VALUES (%s, %s, %s, %s)",
            ("punish", actual_deducted, description, now)
        )

        conn.commit()

        return {
            "success": True,
            "deducted_points": actual_deducted,
            "total_points": new_points,
            "message": f"已扣除 {actual_deducted} 积分，请下次注意！{req.emoji}"
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.get("/api/logs")
def get_logs():
    """获取积分流水记录（最近100条）"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM point_logs ORDER BY created_at DESC LIMIT 100")
    logs = cur.fetchall()
    conn.close()
    return [dict(l) for l in logs]


# 提供前端静态文件
if os.path.exists("index.html"):
    @app.get("/")
    def serve_frontend():
        return FileResponse("index.html")


if __name__ == "__main__":
    import uvicorn
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run(app, host="0.0.0.0", port=port)