"""
数据库连接管理与表初始化（PostgreSQL）。
通过 config.DATABASE_URL 获取连接，方便切换云数据库。
"""

import psycopg2
from psycopg2.extras import RealDictCursor
from api.config import DATABASE_URL


def get_db():
    """获取 PostgreSQL 数据库连接（字典游标）。
    自动为远程库开启 SSL，剥离 Supabase pooler 特有参数。
    Supabase 直连是 IPv6-only，Vercel 不支持 IPv6 → 请用 Transaction pooler (port 6543)。
    """
    dsn = DATABASE_URL
    # psycopg2 不认识 Supabase pooler URL 的 ?pgbouncer=true，剥离
    if 'pgbouncer=true' in dsn:
        dsn = dsn.replace('?pgbouncer=true', '?').replace('&pgbouncer=true', '')
        dsn = dsn.rstrip('?&')
    if 'localhost' not in dsn and '127.0.0.1' not in dsn and 'sslmode' not in dsn:
        sep = '?' if '?' not in dsn else '&'
        dsn = f'{dsn}{sep}sslmode=require'
    # Transaction pooler (port 6543) 不支持 prepared statements
    kwargs = {}
    if ':6543' in dsn:
        kwargs['options'] = '-c plan_cache_mode=force_custom_plan'
    return psycopg2.connect(
        dsn, cursor_factory=RealDictCursor, connect_timeout=10, **kwargs,
    )


def init_db():
    """初始化表结构。幂等——所有 CREATE 使用 IF NOT EXISTS。"""
    conn = get_db()
    cur = conn.cursor()

    # ---- 家庭群组表 ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS family_groups (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '我们的家',
            invite_code TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)

    # ---- 孩子档案表 ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS children (
            id SERIAL PRIMARY KEY,
            group_id INTEGER REFERENCES family_groups(id),
            name TEXT NOT NULL,
            emoji TEXT DEFAULT '👶',
            total_points INTEGER DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)

    # ---- 孩子信用分（迁移）----
    cur.execute("ALTER TABLE children ADD COLUMN IF NOT EXISTS credit_score INTEGER DEFAULT 100")
    cur.execute("UPDATE children SET credit_score = 100 WHERE credit_score IS NULL")

    # ---- 贷款表 ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS loans (
            id SERIAL PRIMARY KEY,
            group_id INTEGER REFERENCES family_groups(id),
            child_id INTEGER REFERENCES children(id),
            amount INTEGER NOT NULL,
            remaining_principal INTEGER NOT NULL,
            daily_rate NUMERIC(5,2) NOT NULL DEFAULT 5.0,
            accrued_interest INTEGER NOT NULL DEFAULT 0,
            last_interest_at TIMESTAMP NOT NULL,
            borrowed_at TIMESTAMP NOT NULL,
            repaid_at TIMESTAMP,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    cur.execute("ALTER TABLE loans ADD COLUMN IF NOT EXISTS accrued_interest INTEGER NOT NULL DEFAULT 0")
    cur.execute("ALTER TABLE loans ADD COLUMN IF NOT EXISTS last_interest_at TIMESTAMP")
    cur.execute("ALTER TABLE loans ADD COLUMN IF NOT EXISTS last_credit_decay_at TIMESTAMP")

    # ---- 任务表 ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            emoji TEXT NOT NULL,
            base_points INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            is_repeatable BOOLEAN NOT NULL DEFAULT false,
            completed_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL,
            group_id INTEGER REFERENCES family_groups(id),
            child_id INTEGER REFERENCES children(id)
        )
    """)
    cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS is_repeatable BOOLEAN NOT NULL DEFAULT false")
    cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP")
    cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS group_id INTEGER REFERENCES family_groups(id)")
    cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS child_id INTEGER REFERENCES children(id)")

    # ---- 奖励商城表 ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rewards (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            emoji TEXT NOT NULL,
            cost_points INTEGER NOT NULL,
            created_at TIMESTAMP NOT NULL,
            group_id INTEGER REFERENCES family_groups(id)
        )
    """)
    cur.execute("ALTER TABLE rewards ADD COLUMN IF NOT EXISTS group_id INTEGER REFERENCES family_groups(id)")

    # ---- 积分流水表 ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS point_logs (
            id SERIAL PRIMARY KEY,
            action TEXT NOT NULL,
            amount INTEGER NOT NULL,
            description TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL,
            group_id INTEGER REFERENCES family_groups(id),
            child_id INTEGER REFERENCES children(id)
        )
    """)
    cur.execute("ALTER TABLE point_logs ADD COLUMN IF NOT EXISTS group_id INTEGER REFERENCES family_groups(id)")
    cur.execute("ALTER TABLE point_logs ADD COLUMN IF NOT EXISTS child_id INTEGER REFERENCES children(id)")
    cur.execute("ALTER TABLE point_logs ADD COLUMN IF NOT EXISTS undone BOOLEAN DEFAULT false")

    # ---- Admin 设置表 ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    # ---- 操作历史表（撤回支持）----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS undo_operations (
            id SERIAL PRIMARY KEY,
            group_id INTEGER REFERENCES family_groups(id),
            child_id INTEGER REFERENCES children(id),
            operation_type TEXT NOT NULL,
            description TEXT NOT NULL,
            undo_data JSONB NOT NULL,
            created_at TIMESTAMP NOT NULL,
            undone_at TIMESTAMP
        )
    """)

    # ---- 每日任务翻倍 ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_task_boosts (
            id SERIAL PRIMARY KEY,
            task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
            group_id INTEGER REFERENCES family_groups(id),
            boost_date DATE NOT NULL,
            multiplier NUMERIC(3,2) NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(task_id, boost_date)
        )
    """)

    # ---- 翻倍覆盖设置（admin）----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_boost_overrides (
            task_id INTEGER PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
            group_id INTEGER REFERENCES family_groups(id),
            override_type TEXT NOT NULL,
            manual_multiplier NUMERIC(3,2),
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)

    # ---- 悬赏附加条件 ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS conditions (
            id SERIAL PRIMARY KEY,
            group_id INTEGER REFERENCES family_groups(id),
            name TEXT NOT NULL,
            reward_type TEXT NOT NULL,
            bonus_value INTEGER,
            multiplier_value NUMERIC(3,2),
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    cur.execute("ALTER TABLE conditions ADD COLUMN IF NOT EXISTS condition_type TEXT DEFAULT 'acceptance'")
    cur.execute("ALTER TABLE conditions ADD COLUMN IF NOT EXISTS streak_days INTEGER")
    cur.execute("ALTER TABLE conditions ADD COLUMN IF NOT EXISTS subset_size INTEGER")

    # ---- 条件-任务绑定（多对多）----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS condition_task_bindings (
            id SERIAL PRIMARY KEY,
            condition_id INTEGER REFERENCES conditions(id) ON DELETE CASCADE,
            task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
            UNIQUE(condition_id, task_id)
        )
    """)

    # ---- 每日条件选择 ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_condition_selections (
            id SERIAL PRIMARY KEY,
            group_id INTEGER REFERENCES family_groups(id),
            condition_id INTEGER REFERENCES conditions(id) ON DELETE CASCADE,
            selection_date DATE NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(group_id, condition_id, selection_date)
        )
    """)

    # ---- 孩子条件接受记录 ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS child_condition_acceptances (
            id SERIAL PRIMARY KEY,
            child_id INTEGER REFERENCES children(id) ON DELETE CASCADE,
            group_id INTEGER REFERENCES family_groups(id),
            condition_id INTEGER REFERENCES conditions(id),
            task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
            accepted BOOLEAN NOT NULL DEFAULT false,
            passed BOOLEAN,
            acceptance_date DATE NOT NULL,
            completed_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    cur.execute("ALTER TABLE child_condition_acceptances ADD COLUMN IF NOT EXISTS penalty_applied BOOLEAN DEFAULT false")

    # ---- 连续打卡进度 ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS condition_streak_progress (
            id SERIAL PRIMARY KEY,
            child_id INTEGER REFERENCES children(id) ON DELETE CASCADE,
            group_id INTEGER REFERENCES family_groups(id),
            condition_id INTEGER REFERENCES conditions(id) ON DELETE CASCADE,
            streak_count INTEGER NOT NULL DEFAULT 0,
            last_completed_date DATE,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(child_id, condition_id)
        )
    """)

    # ---- 任务集合每日进度 ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS condition_task_set_progress (
            id SERIAL PRIMARY KEY,
            child_id INTEGER REFERENCES children(id) ON DELETE CASCADE,
            group_id INTEGER REFERENCES family_groups(id),
            condition_id INTEGER REFERENCES conditions(id) ON DELETE CASCADE,
            selection_date DATE NOT NULL,
            selected_tasks JSONB DEFAULT '[]',
            completed_tasks JSONB DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'active',
            completed_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(child_id, condition_id, selection_date)
        )
    """)

    # ---- 兼容旧 users 表（只读，不再写入）----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '小主人',
            total_points INTEGER NOT NULL DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()


def resolve_group_id(invite_code: str) -> int:
    """Look up group_id from invite_code. Returns the id or None."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM family_groups WHERE invite_code = %s", (invite_code,))
    group = cur.fetchone()
    conn.close()
    return group["id"] if group else None


def load_simulated_time() -> None:
    """启动时从 DB 恢复模拟时间设置。避免循环导入，放在 database 层。"""
    try:
        import api.config as config
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT value FROM admin_settings WHERE key = 'simulated_time'")
        row = cur.fetchone()
        conn.close()
        if row and row["value"]:
            from datetime import datetime
            t = datetime.fromisoformat(row["value"])
            config.set_simulated_time(t)
    except Exception:
        pass  # DB 未就绪时静默跳过
