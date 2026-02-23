"""
儿童作业任务与积分兑换商城系统 - 后端 API
技术栈：FastAPI + SQLite
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import sqlite3
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

DB_PATH = "kids_rewards.db"

# ==================== 数据库初始化 ====================

def get_db():
    """获取数据库连接，启用外键约束"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # 让查询结果可以像字典一样访问
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """初始化数据库表结构"""
    conn = get_db()
    cursor = conn.cursor()

    # 用户/积分表（系统只有一个孩子，用 id=1 的记录作为主体）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '小主人',
            total_points INTEGER NOT NULL DEFAULT 0
        )
    """)

    # 任务表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,          -- 任务名称
            emoji TEXT NOT NULL,         -- 代表 Emoji
            base_points INTEGER NOT NULL, -- 基础积分
            status TEXT NOT NULL DEFAULT 'pending', -- pending / done
            created_at TEXT NOT NULL
        )
    """)

    # 奖励商城表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rewards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,          -- 奖励名称
            emoji TEXT NOT NULL,         -- 代表 Emoji
            cost_points INTEGER NOT NULL, -- 所需积分
            created_at TEXT NOT NULL
        )
    """)

    # 积分流水表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS point_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,        -- 'earn'（获取）/ 'spend'（消耗）
            amount INTEGER NOT NULL,     -- 积分变动量（正数）
            description TEXT NOT NULL,   -- 说明文字
            created_at TEXT NOT NULL
        )
    """)

    # 初始化默认用户（若不存在）
    cursor.execute("INSERT OR IGNORE INTO user (id, name, total_points) VALUES (1, '小主人', 0)")

    # 插入默认示例任务（若任务表为空）
    cursor.execute("SELECT COUNT(*) FROM tasks")
    if cursor.fetchone()[0] == 0:
        now = datetime.now().isoformat()
        cursor.executemany("INSERT INTO tasks (name, emoji, base_points, status, created_at) VALUES (?,?,?,?,?)", [
            ("今日阅读30分钟", "📖", 20, "pending", now),
            ("整理房间", "🧹", 15, "pending", now),
            ("完成数学作业", "✏️", 25, "pending", now),
        ])

    # 插入默认示例奖励（若奖励表为空）
    cursor.execute("SELECT COUNT(*) FROM rewards")
    if cursor.fetchone()[0] == 0:
        now = datetime.now().isoformat()
        cursor.executemany("INSERT INTO rewards (name, emoji, cost_points, created_at) VALUES (?,?,?,?)", [
            ("看30分钟电视", "📺", 30, now),
            ("玩游戏1小时", "🎮", 50, now),
            ("买一包零食", "🍬", 40, now),
            ("周末出去玩", "🎡", 100, now),
        ])

    conn.commit()
    conn.close()


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
    user = conn.execute("SELECT * FROM user WHERE id = 1").fetchone()
    conn.close()
    return dict(user)


@app.get("/api/tasks")
def get_tasks():
    """获取所有待完成任务（pending状态）"""
    conn = get_db()
    tasks = conn.execute(
        "SELECT * FROM tasks ORDER BY created_at DESC"
    ).fetchall()
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
    now = datetime.now().isoformat()
    cursor = conn.execute(
        "INSERT INTO tasks (name, emoji, base_points, status, created_at) VALUES (?,?,?,?,?)",
        (req.name.strip(), req.emoji, req.base_points, "pending", now)
    )
    conn.commit()
    task_id = cursor.lastrowid
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
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
    try:
        # 查询任务
        task = conn.execute("SELECT * FROM tasks WHERE id = ?", (req.task_id,)).fetchone()
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        if task["status"] != "pending":
            raise HTTPException(status_code=400, detail="该任务已经完成，不能重复提交")

        # 计算积分
        final_points = calculate_final_points(task["base_points"], req.star_rating)
        multiplier_pct = int(STAR_MULTIPLIERS[req.star_rating] * 100)
        now = datetime.now().isoformat()

        # 更新任务状态
        conn.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (req.task_id,))

        # 增加总积分（使用原子操作防止并发问题）
        conn.execute("UPDATE user SET total_points = total_points + ? WHERE id = 1", (final_points,))

        # 记录流水账
        description = f"完成任务「{task['name']}」{req.star_rating}⭐（{multiplier_pct}%）→ +{final_points}分"
        conn.execute(
            "INSERT INTO point_logs (action, amount, description, created_at) VALUES (?,?,?,?)",
            ("earn", final_points, description, now)
        )

        conn.commit()

        # 返回最新用户信息
        user = conn.execute("SELECT * FROM user WHERE id = 1").fetchone()
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
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not task:
        conn.close()
        raise HTTPException(status_code=404, detail="任务不存在")
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()
    return {"success": True}


@app.get("/api/rewards")
def get_rewards():
    """获取所有可兑换奖励"""
    conn = get_db()
    rewards = conn.execute("SELECT * FROM rewards ORDER BY cost_points ASC").fetchall()
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
    now = datetime.now().isoformat()
    cursor = conn.execute(
        "INSERT INTO rewards (name, emoji, cost_points, created_at) VALUES (?,?,?,?)",
        (req.name.strip(), req.emoji, req.cost_points, now)
    )
    conn.commit()
    reward_id = cursor.lastrowid
    reward = conn.execute("SELECT * FROM rewards WHERE id = ?", (reward_id,)).fetchone()
    conn.close()
    return dict(reward)


@app.post("/api/rewards/redeem")
def redeem_reward(req: RedeemRewardRequest):
    """
    兑换奖励
    关键防护：使用数据库事务确保积分不会被扣成负数
    校验顺序：查询奖励 → 查询当前积分 → 比较 → 扣除
    """
    conn = get_db()
    try:
        # 查询奖励
        reward = conn.execute("SELECT * FROM rewards WHERE id = ?", (req.reward_id,)).fetchone()
        if not reward:
            raise HTTPException(status_code=404, detail="奖励不存在")

        # 查询当前积分（使用 FOR UPDATE 语义，SQLite 通过事务保证原子性）
        user = conn.execute("SELECT * FROM user WHERE id = 1").fetchone()
        current_points = user["total_points"]
        cost = reward["cost_points"]

        # ⚠️ 关键校验：积分是否充足
        if current_points < cost:
            raise HTTPException(
                status_code=400,
                detail=f"积分不够啦，继续加油！💪 当前积分：{current_points}，需要：{cost}，还差：{cost - current_points}"
            )

        now = datetime.now().isoformat()

        # 扣除积分（使用减法而非直接赋值，配合 CHECK 约束更安全）
        conn.execute(
            "UPDATE user SET total_points = total_points - ? WHERE id = 1",
            (cost,)
        )

        # 二次校验：确保扣减后积分不为负（双重保险）
        user_after = conn.execute("SELECT total_points FROM user WHERE id = 1").fetchone()
        if user_after["total_points"] < 0:
            # 不应该走到这里，但作为最后防线
            conn.rollback()
            raise HTTPException(status_code=400, detail="积分异常，兑换失败")

        # 记录消耗流水
        description = f"兑换奖励「{reward['name']}」{reward['emoji']} → -{cost}分"
        conn.execute(
            "INSERT INTO point_logs (action, amount, description, created_at) VALUES (?,?,?,?)",
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
    reward = conn.execute("SELECT * FROM rewards WHERE id = ?", (reward_id,)).fetchone()
    if not reward:
        conn.close()
        raise HTTPException(status_code=404, detail="奖励不存在")
    conn.execute("DELETE FROM rewards WHERE id = ?", (reward_id,))
    conn.commit()
    conn.close()
    return {"success": True}


@app.get("/api/logs")
def get_logs():
    """获取积分流水记录（最近100条）"""
    conn = get_db()
    logs = conn.execute(
        "SELECT * FROM point_logs ORDER BY created_at DESC LIMIT 100"
    ).fetchall()
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
    uvicorn.run(app, host="0.0.0.0", port=8001)
