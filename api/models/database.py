"""
数据库连接管理与表初始化（PostgreSQL）。
通过 config.DATABASE_URL 获取连接，方便切换云数据库。
"""

import psycopg2
from psycopg2.extras import RealDictCursor
from api.config import DATABASE_URL


def get_db():
    """获取 PostgreSQL 数据库连接（字典游标）。
    自动为远程库开启 SSL，为 PgBouncer 模式禁用 prepared statements。
    """
    dsn = DATABASE_URL
    if 'localhost' not in dsn and '127.0.0.1' not in dsn and 'sslmode' not in dsn:
        sep = '?' if '?' not in dsn else '&'
        dsn = f'{dsn}{sep}sslmode=require'
    # PgBouncer transaction 模式不支持 prepared statements
    return psycopg2.connect(
        dsn, cursor_factory=RealDictCursor, connect_timeout=10,
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
