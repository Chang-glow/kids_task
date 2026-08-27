"""
数据库连接管理与表初始化（PostgreSQL）。
通过 config.DATABASE_URL 获取连接，方便切换云数据库。
"""

import time
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool
from api.config import DATABASE_URL


def _build_dsn():
    dsn = DATABASE_URL
    if 'pgbouncer=true' in dsn:
        dsn = dsn.replace('?pgbouncer=true', '?').replace('&pgbouncer=true', '')
        dsn = dsn.rstrip('?&')
    if 'localhost' not in dsn and '127.0.0.1' not in dsn and 'sslmode' not in dsn:
        sep = '?' if '?' not in dsn else '&'
        dsn = f'{dsn}{sep}sslmode=require'
    return dsn


_pool: ThreadedConnectionPool | None = None
_pool_dsn: str = ""


def _init_pool():
    global _pool, _pool_dsn
    dsn = _build_dsn()
    kwargs = {'cursor_factory': RealDictCursor, 'connect_timeout': 10}
    if ':6543' in dsn:
        kwargs['options'] = '-c plan_cache_mode=force_custom_plan'
    _pool = ThreadedConnectionPool(1, 3, dsn, **kwargs)
    _pool_dsn = dsn


class _PooledConnection:
    """包装 psycopg2 连接，让 close() 归还连接池而非真正关闭。"""

    def __init__(self, conn, pool):
        self._conn = conn
        self._pool = pool

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):
        try:
            self._pool.putconn(self._conn)
        except Exception:
            try:
                self._conn.close()
            except Exception:
                pass

    def really_close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _is_conn_alive(conn) -> bool:
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        return True
    except Exception:
        return False


def get_db():
    """获取 PostgreSQL 数据库连接（字典游标），走连接池。

    每个 Vercel 实例最多持有 3 个连接，避免耗尽 Supabase session pooler
    的 15 连接上限。调用方正常 conn.close() 即可——实际归还连接池而非关闭。
    遇到连接超限错误时自动重试最多 3 次。
    """
    global _pool, _pool_dsn
    dsn = _build_dsn()
    if _pool is None or dsn != _pool_dsn:
        if _pool is not None:
            try:
                _pool.closeall()
            except Exception:
                pass
        _init_pool()

    last_error = None
    for attempt in range(3):
        try:
            conn = _pool.getconn()
            if _is_conn_alive(conn):
                return _PooledConnection(conn, _pool)
            # 连接已失效，关闭并从池中移除，重试
            try:
                conn.close()
            except Exception:
                pass
            continue
        except psycopg2.OperationalError as e:
            last_error = e
            msg = str(e)
            if 'too many clients' in msg or 'max clients reached' in msg.lower() or 'EMAXCONNSESSION' in msg:
                time.sleep(0.3 * (attempt + 1))
                continue
            raise
        except Exception:
            last_error = None
            break
    if last_error:
        raise last_error
    conn = _pool.getconn()
    return _PooledConnection(conn, _pool)


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
    cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS description TEXT DEFAULT ''")

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
    cur.execute("ALTER TABLE daily_boost_overrides ADD COLUMN IF NOT EXISTS expires_at DATE")

    # ---- 每日奖励时段定价（替代旧 daily_reward_surges）----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_reward_pricing (
            id SERIAL PRIMARY KEY,
            reward_id INTEGER REFERENCES rewards(id) ON DELETE CASCADE,
            group_id INTEGER REFERENCES family_groups(id),
            pricing_date DATE NOT NULL,
            surge_peak_rate NUMERIC(4,2) NOT NULL DEFAULT 0.25,
            sale_trough_rate NUMERIC(4,2) NOT NULL DEFAULT 0.15,
            plateau_minutes INTEGER NOT NULL DEFAULT 0,
            partial_peak_factor NUMERIC(3,2) NOT NULL DEFAULT 1.0,
            is_flat BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(reward_id, pricing_date)
        )
    """)

    # ---- 定价覆盖设置（替代旧 daily_reward_surge_overrides）----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_pricing_overrides (
            reward_id INTEGER PRIMARY KEY REFERENCES rewards(id) ON DELETE CASCADE,
            group_id INTEGER REFERENCES family_groups(id),
            override_type TEXT NOT NULL,
            manual_surge_peak NUMERIC(4,2),
            manual_sale_trough NUMERIC(4,2),
            manual_plateau INTEGER,
            manual_partial_factor NUMERIC(3,2),
            expires_at DATE,
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

    # ---- 条件覆盖设置（admin lock_in / lock_out）----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS condition_overrides (
            id SERIAL PRIMARY KEY,
            group_id INTEGER REFERENCES family_groups(id),
            condition_id INTEGER REFERENCES conditions(id) ON DELETE CASCADE,
            override_type TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(group_id, condition_id)
        )
    """)
    cur.execute("ALTER TABLE condition_overrides ADD COLUMN IF NOT EXISTS expires_at DATE")

    # ---- 条件刷新记录（每日限额）----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS condition_refresh_log (
            id SERIAL PRIMARY KEY,
            group_id INTEGER REFERENCES family_groups(id),
            refresh_date DATE NOT NULL,
            refresh_count INTEGER NOT NULL DEFAULT 1,
            point_cost INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)

    # ---- 每日奖章 ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_medals (
            id SERIAL PRIMARY KEY,
            child_id INTEGER REFERENCES children(id) ON DELETE CASCADE,
            group_id INTEGER REFERENCES family_groups(id),
            medal_date DATE NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            UNIQUE(child_id, medal_date)
        )
    """)

    # ---- 优惠券 ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS coupons (
            id SERIAL PRIMARY KEY,
            child_id INTEGER REFERENCES children(id) ON DELETE CASCADE,
            group_id INTEGER REFERENCES family_groups(id),
            medal_count INTEGER NOT NULL DEFAULT 5,
            used BOOLEAN NOT NULL DEFAULT false,
            reward_id INTEGER REFERENCES rewards(id),
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            used_at TIMESTAMP
        )
    """)
    # 迁移：旧字段清理（coupon_type, discount_pct 已废弃，统一为 medal_count）
    cur.execute("ALTER TABLE coupons DROP COLUMN IF EXISTS coupon_type")
    cur.execute("ALTER TABLE coupons DROP COLUMN IF EXISTS discount_pct")
    cur.execute("ALTER TABLE coupons ADD COLUMN IF NOT EXISTS medal_count INTEGER NOT NULL DEFAULT 5")

    # ---- 投资章（每日清零，每种任务每天只给 1 枚）----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS investment_medals (
            id SERIAL PRIMARY KEY,
            child_id INTEGER REFERENCES children(id) ON DELETE CASCADE,
            group_id INTEGER REFERENCES family_groups(id),
            task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
            medal_date DATE NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(child_id, task_id, medal_date)
        )
    """)

    # ---- 投资券 ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS investment_coupons (
            id SERIAL PRIMARY KEY,
            child_id INTEGER REFERENCES children(id) ON DELETE CASCADE,
            group_id INTEGER REFERENCES family_groups(id),
            medal_count INTEGER NOT NULL DEFAULT 5,
            used BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            used_at TIMESTAMP
        )
    """)

    # ---- 活跃投资（每日收益）----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS investments (
            id SERIAL PRIMARY KEY,
            child_id INTEGER REFERENCES children(id) ON DELETE CASCADE,
            group_id INTEGER REFERENCES family_groups(id),
            coupon_id INTEGER REFERENCES investment_coupons(id),
            principal INTEGER NOT NULL DEFAULT 10,
            daily_income NUMERIC(5,1) NOT NULL DEFAULT 0.5,
            days_remaining INTEGER NOT NULL DEFAULT 50,
            total_earned NUMERIC(10,1) NOT NULL DEFAULT 0,
            last_payout_date DATE,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            status TEXT NOT NULL DEFAULT 'active'
        )
    """)

    # ---- 奖励锁（admin 绑定任务作钥匙，全部完成前不可兑换）----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS reward_locks (
            id SERIAL PRIMARY KEY,
            reward_id INTEGER REFERENCES rewards(id) ON DELETE CASCADE,
            task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
            group_id INTEGER REFERENCES family_groups(id),
            lock_group INTEGER,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(reward_id, task_id)
        )
    """)
    # 迁移：钥匙分组 —— 同组内任选其一完成
    cur.execute("ALTER TABLE reward_locks ADD COLUMN IF NOT EXISTS lock_group INTEGER")
    # 迁移：旧版 reward_locks 的 UNIQUE(reward_id) 改为 UNIQUE(reward_id, task_id)
    cur.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'reward_locks_reward_id_key'
            ) THEN
                ALTER TABLE reward_locks DROP CONSTRAINT reward_locks_reward_id_key;
                ALTER TABLE reward_locks ADD CONSTRAINT reward_locks_reward_id_task_id_key UNIQUE (reward_id, task_id);
            END IF;
        END $$;
    """)

    # ---- JWT token 撤销表（退出登录时写入，验证时检查）----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS revoked_tokens (
            jti TEXT PRIMARY KEY,
            expires_at TIMESTAMP NOT NULL
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
