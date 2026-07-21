"""
Test fixtures: FastAPI TestClient + test database.
Uses a separate test database (kids_rewards_test) to avoid touching production data.

Usage:
    DATABASE_URL 默认指向 kids_rewards_test。
    运行前确保该数据库存在：createdb kids_rewards_test
"""

import os
import atexit
import pytest

# Override before any imports — app.py reads this at module load time
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql:///kids_rewards_test",
)

# 记录测试中创建的群组 ID，进程退出时统一清理
_test_group_ids: set[int] = set()


def _cleanup_test_data():
    """进程退出时清除测试产生的群组数据和带 TEST: 标记的密码哈希。"""
    import psycopg2
    from psycopg2.extras import RealDictCursor
    try:
        conn = psycopg2.connect("postgresql:///kids_rewards_test", cursor_factory=RealDictCursor)
        cur = conn.cursor()
        cur.execute("DELETE FROM admin_settings WHERE key = 'password_hash_test'")
        cur.execute("DELETE FROM admin_settings WHERE key IN ('loan_interest_rate', 'loan_max_amount')")
        if _test_group_ids:
            ids = tuple(_test_group_ids)
            cur.execute("DELETE FROM child_condition_acceptances WHERE group_id IN %s", (ids,))
            cur.execute("DELETE FROM daily_condition_selections WHERE group_id IN %s", (ids,))
            cur.execute("DELETE FROM condition_task_bindings WHERE condition_id IN (SELECT id FROM conditions WHERE group_id IN %s)", (ids,))
            cur.execute("DELETE FROM conditions WHERE group_id IN %s", (ids,))
            cur.execute("DELETE FROM daily_task_boosts WHERE group_id IN %s", (ids,))
            cur.execute("DELETE FROM daily_boost_overrides WHERE group_id IN %s", (ids,))
            cur.execute("DELETE FROM undo_operations WHERE group_id IN %s", (ids,))
            cur.execute("DELETE FROM point_logs WHERE group_id IN %s", (ids,))
            cur.execute("DELETE FROM loans WHERE group_id IN %s", (ids,))
            cur.execute("DELETE FROM tasks WHERE group_id IN %s", (ids,))
            cur.execute("DELETE FROM rewards WHERE group_id IN %s", (ids,))
            cur.execute("DELETE FROM children WHERE group_id IN %s", (ids,))
            cur.execute("DELETE FROM family_groups WHERE id IN %s", (ids,))
        conn.commit()
        conn.close()
    except Exception:
        pass


atexit.register(_cleanup_test_data)


@pytest.fixture
def app():
    """Import and return the FastAPI app. Module-level init_db() runs on import."""
    from api.main import app as _app

    return _app


@pytest.fixture
def client(app):
    """TestClient wrapping the FastAPI app."""
    from fastapi.testclient import TestClient

    return TestClient(app)


@pytest.fixture
def group_ctx(client):
    """Create a test family group and return its id + invite_code + headers + children."""
    res = client.post("/api/groups", json={"name": "测试群组", "child_name": "测试孩子"})
    data = res.json()
    code = data["invite_code"]
    # Fetch full group info to get the id and children
    info = client.get(f"/api/groups/{code}").json()
    _test_group_ids.add(info["id"])
    return {
        "id": info["id"],
        "invite_code": code,
        "headers": {"X-Group-Code": code},
        "children": info.get("children", []),
    }


@pytest.fixture
def admin_token(client):
    """用独立 key password_hash_test 写测试密码，不碰生产密码。"""
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from api.admin_auth import hash_password

    conn = psycopg2.connect("postgresql:///kids_rewards_test", cursor_factory=RealDictCursor)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO admin_settings (key, value) VALUES ('password_hash_test', %s)"
        " ON CONFLICT (key) DO UPDATE SET value = %s",
        (hash_password("admintest"), hash_password("admintest")),
    )
    conn.commit()
    conn.close()

    res = client.post("/api/admin/login", json={"password": "admintest"})
    token = res.json()["token"]
    return {"Authorization": f"Bearer {token}"}
