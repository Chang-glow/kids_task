"""Admin 后台测试：认证 + 积分修改 + 日志管理 + 撤回操作。"""

import pytest


class TestAdminAuth:
    """密码设置与登录认证测试"""

    def test_status_returns_configured(self, client):
        res = client.get("/api/admin/status")
        assert res.status_code == 200
        assert "configured" in res.json()

    def test_setup_requires_min_password(self, client):
        res = client.post("/api/admin/setup", json={"password": "ab"})
        assert res.status_code == 400

    def test_setup_creates_password(self, client, admin_token):
        # admin_token fixture already verifies setup + login work
        assert "Authorization" in admin_token

    def test_login_works(self, client, admin_token):
        res = client.post("/api/admin/login", json={"password": "admintest"})
        assert res.status_code == 200
        assert "token" in res.json()

    def test_login_wrong_password(self, client):
        res = client.post("/api/admin/login", json={"password": "wrongpasswordZZZZ"})
        assert res.status_code in (400, 401)


class TestAdminGroups:
    """群组管理测试"""

    def test_groups_unauthorized(self, client):
        res = client.get("/api/admin/groups")
        assert res.status_code == 401

    def test_groups_with_token(self, client, admin_token):
        res = client.get("/api/admin/groups", headers=admin_token)
        assert res.status_code == 200
        assert isinstance(res.json(), list)


class TestAdminPoints:
    """积分修改测试"""

    def test_modify_points_set(self, client, admin_token, group_ctx):
        child_id = group_ctx["children"][0]["id"]
        group_id = group_ctx["id"]
        res = client.post(
            "/api/admin/points",
            json={"child_id": child_id, "group_id": group_id, "mode": "set", "value": 200},
            headers=admin_token,
        )
        assert res.status_code == 200
        assert res.json()["new_points"] == 200

    def test_modify_points_delta(self, client, admin_token, group_ctx):
        child_id = group_ctx["children"][0]["id"]
        group_id = group_ctx["id"]
        # Set to 100 first
        client.post(
            "/api/admin/points",
            json={"child_id": child_id, "group_id": group_id, "mode": "set", "value": 100},
            headers=admin_token,
        )
        # Delta +50
        res = client.post(
            "/api/admin/points",
            json={"child_id": child_id, "group_id": group_id, "mode": "delta", "value": 50},
            headers=admin_token,
        )
        assert res.status_code == 200
        assert res.json()["new_points"] == 150

    def test_modify_points_missing_child(self, client, admin_token, group_ctx):
        res = client.post(
            "/api/admin/points",
            json={"child_id": 99999, "group_id": group_ctx["id"], "mode": "set", "value": 100},
            headers=admin_token,
        )
        assert res.status_code == 404


class TestAdminLogs:
    """日志管理测试"""

    def test_add_log(self, client, admin_token, group_ctx):
        child_id = group_ctx["children"][0]["id"]
        res = client.post(
            "/api/admin/logs",
            json={"child_id": child_id, "group_id": group_ctx["id"], "action": "earn", "amount": 20, "description": "测试添加日志"},
            headers=admin_token,
        )
        assert res.status_code == 200
        assert "log_id" in res.json()

    def test_add_log_validation(self, client, admin_token, group_ctx):
        res = client.post(
            "/api/admin/logs",
            json={"child_id": group_ctx["children"][0]["id"], "group_id": group_ctx["id"], "action": "invalid", "amount": 10, "description": "test"},
            headers=admin_token,
        )
        assert res.status_code == 400

    def test_delete_log(self, client, admin_token, group_ctx):
        child_id = group_ctx["children"][0]["id"]
        group_id = group_ctx["id"]
        # Add then delete
        add = client.post(
            "/api/admin/logs",
            json={"child_id": child_id, "group_id": group_id, "action": "earn", "amount": 10, "description": "to delete"},
            headers=admin_token,
        )
        log_id = add.json()["log_id"]
        res = client.delete(f"/api/admin/logs/{log_id}", headers=admin_token)
        assert res.status_code == 200

    def test_delete_nonexistent_log(self, client, admin_token):
        res = client.delete("/api/admin/logs/99999", headers=admin_token)
        assert res.status_code == 404


class TestAdminOperations:
    """操作历史 & 撤回测试"""

    def test_list_operations(self, client, admin_token, group_ctx):
        res = client.get(f"/api/admin/operations?group_id={group_ctx['id']}", headers=admin_token)
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_undo_manual_edit(self, client, admin_token, group_ctx):
        child_id = group_ctx["children"][0]["id"]
        group_id = group_ctx["id"]
        # Modify points
        client.post(
            "/api/admin/points",
            json={"child_id": child_id, "group_id": group_id, "mode": "set", "value": 500},
            headers=admin_token,
        )
        # Find the operation
        ops = client.get(f"/api/admin/operations?group_id={group_id}", headers=admin_token).json()
        edit_op = next((o for o in ops if o["operation_type"] == "manual_edit"), None)
        assert edit_op is not None

        # Undo it
        res = client.post(f"/api/admin/undo/{edit_op['id']}", headers=admin_token)
        assert res.status_code == 200

    def test_undo_nonexistent(self, client, admin_token):
        res = client.post("/api/admin/undo/99999", headers=admin_token)
        assert res.status_code == 404

    def test_double_undo_rejected(self, client, admin_token, group_ctx):
        child_id = group_ctx["children"][0]["id"]
        group_id = group_ctx["id"]
        client.post(
            "/api/admin/points",
            json={"child_id": child_id, "group_id": group_id, "mode": "set", "value": 300},
            headers=admin_token,
        )
        ops = client.get(f"/api/admin/operations?group_id={group_id}", headers=admin_token).json()
        edit_op = next((o for o in ops if o["operation_type"] == "manual_edit"), None)
        # First undo
        client.post(f"/api/admin/undo/{edit_op['id']}", headers=admin_token)
        # Second undo should fail
        res = client.post(f"/api/admin/undo/{edit_op['id']}", headers=admin_token)
        assert res.status_code == 400


class TestAdminTasksRewards:
    """任务 & 奖励查询测试"""

    def test_admin_tasks(self, client, admin_token, group_ctx):
        res = client.get(f"/api/admin/tasks?group_id={group_ctx['id']}", headers=admin_token)
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_admin_rewards(self, client, admin_token, group_ctx):
        res = client.get(f"/api/admin/rewards?group_id={group_ctx['id']}", headers=admin_token)
        assert res.status_code == 200
        assert isinstance(res.json(), list)


class TestAdminDelete:
    """删除群组 & 孩子测试"""

    def test_delete_child(self, client, admin_token, group_ctx):
        # Add a second child to delete
        res = client.post(
            "/api/children",
            json={"name": "待删除", "emoji": "🗑️"},
            headers=group_ctx["headers"],
        )
        child_id = res.json()["id"]

        res = client.delete(f"/api/admin/children/{child_id}", headers=admin_token)
        assert res.status_code == 200

        # Verify gone
        info = client.get(f"/api/groups/{group_ctx['invite_code']}").json()
        child_ids = [c["id"] for c in info.get("children", [])]
        assert child_id not in child_ids

    def test_delete_child_nonexistent(self, client, admin_token):
        res = client.delete("/api/admin/children/99999", headers=admin_token)
        assert res.status_code == 404

    def test_delete_group(self, client, admin_token, group_ctx):
        res = client.delete(f"/api/admin/groups/{group_ctx['id']}", headers=admin_token)
        assert res.status_code == 200

        # Verify gone
        res = client.get(f"/api/groups/{group_ctx['invite_code']}")
        assert res.status_code == 404

    def test_delete_group_nonexistent(self, client, admin_token):
        res = client.delete("/api/admin/groups/99999", headers=admin_token)
        assert res.status_code == 404
