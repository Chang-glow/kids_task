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
from datetime import datetime, timezone, timedelta

# 北京时间 UTC+8
CST = timezone(timedelta(hours=8))

def now_cst():
    """返回当前北京时间（UTC+8），替代所有 datetime.now()"""
    return datetime.now(CST).replace(tzinfo=None)  # 存入数据库不带时区信息，保持一致

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

    # 用户/积分表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '小主人',
            total_points INTEGER NOT NULL DEFAULT 0
        )
    """)

    # 任务表（含可重复标志和完成时间）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            emoji TEXT NOT NULL,
            base_points INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            is_repeatable BOOLEAN NOT NULL DEFAULT false,
            completed_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL
        )
    """)
    # 兼容已存在的旧表：尝试新增列，若已存在则忽略
    cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS is_repeatable BOOLEAN NOT NULL DEFAULT false")
    cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP")

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

    # 初始化默认用户
    cur.execute("""
        INSERT INTO users (id, name, total_points)
        VALUES (1, '小主人', 0)
        ON CONFLICT (id) DO NOTHING
    """)

    # 插入默认示例任务（若任务表为空）
    cur.execute("SELECT COUNT(*) FROM tasks")
    if cur.fetchone()['count'] == 0:
        now = now_cst()
        tasks_data = [
            ("今日阅读30分钟", "📖", 20, "pending", False, now),
            ("整理房间", "🧹", 15, "pending", True, now),
            ("完成数学作业", "✏️", 25, "pending", False, now),
        ]
        cur.executemany(
            "INSERT INTO tasks (name, emoji, base_points, status, is_repeatable, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
            tasks_data
        )

    # 插入默认示例奖励（若奖励表为空）
    cur.execute("SELECT COUNT(*) FROM rewards")
    if cur.fetchone()['count'] == 0:
        now = now_cst()
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
    is_repeatable: bool = False  # 是否可重复完成，默认否

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

# ==================== 积分计算工具函数 ====================

STAR_MULTIPLIERS = {
    1: 0.5,
    2: 0.6,
    3: 0.8,
    4: 1.0,
    5: 1.2,
}

def calculate_final_points(base_points: int, star_rating: int) -> int:
    if star_rating not in STAR_MULTIPLIERS:
        raise ValueError(f"星级必须在1-5之间，收到：{star_rating}")
    return math.floor(base_points * STAR_MULTIPLIERS[star_rating])

# ==================== API 路由 ====================

@app.get("/api/user")
def get_user():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = 1")
    user = cur.fetchone()
    conn.close()
    return dict(user) if user else {}


@app.get("/api/tasks")
def get_tasks():
    """获取所有任务，查询前自动懒清理过期的非重复已完成任务"""
    conn = get_db()
    cur = conn.cursor()
    try:
        # 懒清理：删除「非重复 + 已完成 + 完成日期不是今天」的任务
        cur.execute("""
            DELETE FROM tasks
            WHERE status = 'done'
              AND is_repeatable = false
              AND completed_at IS NOT NULL
              AND DATE(completed_at) < CURRENT_DATE
        """)
        cur.execute("SELECT * FROM tasks ORDER BY created_at DESC")
        tasks = cur.fetchall()
        conn.commit()
        return [dict(t) for t in tasks]
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.post("/api/tasks")
def add_task(req: AddTaskRequest):
    if req.base_points <= 0:
        raise HTTPException(status_code=400, detail="基础积分必须大于0")
    if len(req.name.strip()) == 0:
        raise HTTPException(status_code=400, detail="任务名称不能为空")
    conn = get_db()
    cur = conn.cursor()
    now = now_cst()
    cur.execute(
        "INSERT INTO tasks (name, emoji, base_points, status, is_repeatable, created_at) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (req.name.strip(), req.emoji, req.base_points, "pending", req.is_repeatable, now)
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
    - 可重复任务：保持 pending，只增加积分并记录流水
    - 非重复任务：标为 done，增加积分并记录流水
    """
    if req.star_rating not in range(1, 6):
        raise HTTPException(status_code=400, detail="星级评分必须在1到5之间")
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM tasks WHERE id = %s", (req.task_id,))
        task = cur.fetchone()
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        if task["status"] != "pending":
            raise HTTPException(status_code=400, detail="该任务已经完成，不能重复提交")

        final_points = calculate_final_points(task["base_points"], req.star_rating)
        multiplier_pct = int(STAR_MULTIPLIERS[req.star_rating] * 100)
        now = now_cst()

        # 可重复任务保持 pending，非重复任务标为 done
        if task["is_repeatable"]:
            cur.execute("UPDATE tasks SET completed_at = %s WHERE id = %s", (now, req.task_id))
            result_message = f"任务完成！获得 {final_points} 积分，明天还能继续 🔄"
        else:
            cur.execute("UPDATE tasks SET status = 'done', completed_at = %s WHERE id = %s", (now, req.task_id))
            result_message = f"太棒了！获得 {final_points} 积分 🎉"

        cur.execute("UPDATE users SET total_points = total_points + %s WHERE id = 1", (final_points,))

        description = f"完成任务「{task['name']}」{req.star_rating}⭐（{multiplier_pct}%）→ +{final_points}分"
        cur.execute(
            "INSERT INTO point_logs (action, amount, description, created_at) VALUES (%s, %s, %s, %s)",
            ("earn", final_points, description, now)
        )
        conn.commit()

        cur.execute("SELECT * FROM users WHERE id = 1")
        user = cur.fetchone()
        return {
            "success": True,
            "earned_points": final_points,
            "total_points": user["total_points"],
            "message": result_message
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
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM tasks WHERE id = %s", (task_id,))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="任务不存在")
    cur.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
    conn.commit()
    conn.close()
    return {"success": True}


@app.get("/api/rewards")
def get_rewards():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM rewards ORDER BY cost_points ASC")
    rewards = cur.fetchall()
    conn.close()
    return [dict(r) for r in rewards]


@app.post("/api/rewards")
def add_reward(req: AddRewardRequest):
    if req.cost_points <= 0:
        raise HTTPException(status_code=400, detail="所需积分必须大于0")
    if len(req.name.strip()) == 0:
        raise HTTPException(status_code=400, detail="奖励名称不能为空")
    conn = get_db()
    cur = conn.cursor()
    now = now_cst()
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
    """兑换奖励，事务保护不扣成负数"""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM rewards WHERE id = %s", (req.reward_id,))
        reward = cur.fetchone()
        if not reward:
            raise HTTPException(status_code=404, detail="奖励不存在")

        cur.execute("SELECT * FROM users WHERE id = 1")
        user = cur.fetchone()
        current_points = user["total_points"]
        cost = reward["cost_points"]

        if current_points < cost:
            raise HTTPException(
                status_code=400,
                detail=f"积分不够啦，继续加油！💪 当前积分：{current_points}，需要：{cost}，还差：{cost - current_points}"
            )

        now = now_cst()
        cur.execute("UPDATE users SET total_points = total_points - %s WHERE id = 1", (cost,))

        cur.execute("SELECT total_points FROM users WHERE id = 1")
        user_after = cur.fetchone()
        if user_after["total_points"] < 0:
            conn.rollback()
            raise HTTPException(status_code=400, detail="积分异常，兑换失败")

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
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM rewards WHERE id = %s", (reward_id,))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="奖励不存在")
    cur.execute("DELETE FROM rewards WHERE id = %s", (reward_id,))
    conn.commit()
    conn.close()
    return {"success": True}


@app.post("/api/punish")
def punish_user(req: PunishRequest):
    """惩罚扣分，使用 max(0, current - penalty) 确保不扣成负数"""
    if req.penalty_points <= 0:
        raise HTTPException(status_code=400, detail="扣分值必须大于0")
    if len(req.name.strip()) == 0:
        raise HTTPException(status_code=400, detail="惩罚原因不能为空")
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT total_points FROM users WHERE id = 1")
        user = cur.fetchone()
        current_points = user["total_points"]
        new_points = max(0, current_points - req.penalty_points)
        actual_deducted = current_points - new_points

        now = now_cst()
        cur.execute("UPDATE users SET total_points = %s WHERE id = 1", (new_points,))

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
def get_logs(offset: int = 0, limit: int = 10):
    """分页获取流水记录，offset=跳过条数，limit=获取条数"""
    conn = get_db()
    cur = conn.cursor()
    # 获取总条数，用于前端判断是否还有更多
    cur.execute("SELECT COUNT(*) FROM point_logs")
    total = cur.fetchone()['count']
    cur.execute(
        "SELECT * FROM point_logs ORDER BY created_at DESC LIMIT %s OFFSET %s",
        (limit, offset)
    )
    logs = cur.fetchall()
    conn.close()
    return {"total": total, "logs": [dict(l) for l in logs]}


@app.post("/api/admin/fix-time")
def fix_time():
    """
    时区修正工具：将数据库中已有的时间记录统一 +8 小时
    仅需执行一次，修正历史数据的 UTC 偏差
    """
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE point_logs SET created_at = created_at + INTERVAL '8 hours'")
        logs_updated = cur.rowcount
        cur.execute("UPDATE tasks SET created_at = created_at + INTERVAL '8 hours'")
        tasks_updated = cur.rowcount
        cur.execute("""
            UPDATE tasks SET completed_at = completed_at + INTERVAL '8 hours'
            WHERE completed_at IS NOT NULL
        """)
        conn.commit()
        return {
            "success": True,
            "message": f"时区修正完成！流水记录更新 {logs_updated} 条，任务更新 {tasks_updated} 条"
        }
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


# 获取周期状态
@app.get("/api/stats")
def get_stats():
    """
    积分统计报告
    按日/周/月三个维度聚合 point_logs，返回盈、亏、净值
    """
    conn = get_db()
    cur = conn.cursor()
    try:
        result = {}
        for period in ['day', 'week', 'month']:
            cur.execute("""
                SELECT
                    date_trunc(%s, created_at)          AS period_start,
                    SUM(CASE WHEN action = 'earn'
                             THEN amount ELSE 0 END)    AS earned,
                    SUM(CASE WHEN action IN ('spend', 'punish')
                             THEN amount ELSE 0 END)    AS spent,
                    SUM(CASE WHEN action = 'earn'
                             THEN amount ELSE -amount END) AS net
                FROM point_logs
                GROUP BY date_trunc(%s, created_at)
                ORDER BY period_start DESC
                LIMIT 30
            """, (period, period))
            rows = cur.fetchall()
            # 将 datetime 转为字符串，方便 JSON 序列化
            result[period] = [
                {
                    "period_start": row["period_start"].strftime(
                        "%Y-%m-%d" if period == "day" else
                        "%Y 第%W周" if period == "week" else
                        "%Y-%m"
                    ),
                    "earned": int(row["earned"] or 0),
                    "spent":  int(row["spent"]  or 0),
                    "net":    int(row["net"]    or 0),
                }
                for row in rows
            ]
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


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
