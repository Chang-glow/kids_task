"""Tests for daily conditions, condition CRUD, acceptance, and completion with results."""


class TestDailyConditions:
    """GET /api/tasks/conditions/today returns today's conditions."""

    def test_conditions_today_empty(self, client, group_ctx):
        """Conditions/today returns empty list when no conditions exist."""
        res = client.get("/api/tasks/conditions/today", headers=group_ctx["headers"])
        assert res.status_code == 200
        assert res.json() == []

    def test_conditions_today_idempotent(self, client, group_ctx):
        """Calling conditions/today twice returns same results."""
        res1 = client.get("/api/tasks/conditions/today", headers=group_ctx["headers"])
        res2 = client.get("/api/tasks/conditions/today", headers=group_ctx["headers"])
        assert res1.json() == res2.json()


class TestConditionCRUD:
    """Admin condition create/list/delete."""

    def test_create_condition_bonus(self, client, group_ctx, admin_token):
        """Create a bonus_points condition and list it."""
        h = group_ctx["headers"]
        res = client.post("/api/tasks", json={"name": "阅读", "emoji": "📖", "base_points": 20}, headers=h)
        task_id = res.json()["id"]

        resp = client.post(
            "/api/admin/conditions",
            json={
                "group_id": group_ctx["id"],
                "name": "早起完成",
                "reward_type": "bonus_points",
                "bonus_value": 10,
                "task_ids": [task_id],
            },
            headers=admin_token,
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        conds = client.get(
            f"/api/admin/conditions?group_id={group_ctx['id']}",
            headers=admin_token,
        ).json()
        assert len(conds) == 1
        assert conds[0]["name"] == "早起完成"
        assert conds[0]["bonus_value"] == 10
        assert "阅读" in conds[0]["task_names"]

    def test_create_condition_multiplier(self, client, group_ctx, admin_token):
        """Create a multiplier condition."""
        h = group_ctx["headers"]
        res = client.post("/api/tasks", json={"name": "运动", "emoji": "🏃", "base_points": 30}, headers=h)
        task_id = res.json()["id"]

        client.post(
            "/api/admin/conditions",
            json={
                "group_id": group_ctx["id"],
                "name": "超常发挥",
                "reward_type": "multiplier",
                "multiplier_value": 1.5,
                "task_ids": [task_id],
            },
            headers=admin_token,
        )
        conds = client.get(
            f"/api/admin/conditions?group_id={group_ctx['id']}",
            headers=admin_token,
        ).json()
        assert conds[0]["multiplier_value"] == 1.5

    def test_create_condition_both(self, client, group_ctx, admin_token):
        """Create a condition with both bonus and multiplier."""
        h = group_ctx["headers"]
        res = client.post("/api/tasks", json={"name": "练琴", "emoji": "🎵", "base_points": 25}, headers=h)
        task_id = res.json()["id"]

        client.post(
            "/api/admin/conditions",
            json={
                "group_id": group_ctx["id"],
                "name": "完美达成",
                "reward_type": "both",
                "bonus_value": 15,
                "multiplier_value": 2.0,
                "task_ids": [task_id],
            },
            headers=admin_token,
        )
        conds = client.get(
            f"/api/admin/conditions?group_id={group_ctx['id']}",
            headers=admin_token,
        ).json()
        assert conds[0]["reward_type"] == "both"
        assert conds[0]["bonus_value"] == 15
        assert float(conds[0]["multiplier_value"]) == 2.0

    def test_delete_condition(self, client, group_ctx, admin_token):
        """Delete a condition."""
        h = group_ctx["headers"]
        res = client.post("/api/tasks", json={"name": "阅读", "emoji": "📖", "base_points": 20}, headers=h)
        task_id = res.json()["id"]

        resp = client.post(
            "/api/admin/conditions",
            json={
                "group_id": group_ctx["id"],
                "name": "测试条件",
                "reward_type": "bonus_points",
                "bonus_value": 10,
                "task_ids": [task_id],
            },
            headers=admin_token,
        )
        cond_id = resp.json()["condition_id"]

        del_resp = client.delete(f"/api/admin/conditions/{cond_id}", headers=admin_token)
        assert del_resp.status_code == 200

        conds = client.get(
            f"/api/admin/conditions?group_id={group_ctx['id']}",
            headers=admin_token,
        ).json()
        assert len(conds) == 0

    def test_create_condition_validation(self, client, group_ctx, admin_token):
        """Invalid condition data returns 400."""
        resp = client.post(
            "/api/admin/conditions",
            json={"group_id": group_ctx["id"], "name": "", "reward_type": "bonus_points"},
            headers=admin_token,
        )
        assert resp.status_code == 400

        resp = client.post(
            "/api/admin/conditions",
            json={"group_id": group_ctx["id"], "name": "X", "reward_type": "invalid"},
            headers=admin_token,
        )
        assert resp.status_code == 400


class TestConditionAcceptance:
    """POST /api/tasks/conditions/accept."""

    def test_accept_condition(self, client, group_ctx, admin_token):
        """Accept a condition and verify it shows as accepted."""
        h = group_ctx["headers"]
        res = client.post("/api/tasks", json={"name": "阅读", "emoji": "📖", "base_points": 20}, headers=h)
        task_id = res.json()["id"]

        resp = client.post(
            "/api/admin/conditions",
            json={
                "group_id": group_ctx["id"],
                "name": "早起完成",
                "reward_type": "bonus_points",
                "bonus_value": 10,
                "task_ids": [task_id],
            },
            headers=admin_token,
        )
        cond_id = resp.json()["condition_id"]

        accept = client.post(
            "/api/tasks/conditions/accept",
            json={"condition_id": cond_id, "task_id": task_id},
            headers=h,
        )
        assert accept.status_code == 200

        conds = client.get("/api/tasks/conditions/today", headers=h).json()
        matching = [c for c in conds if c["id"] == cond_id]
        assert len(matching) == 1
        assert matching[0]["accepted"] is True


class TestCompleteTaskWithConditions:
    """Task completion with condition results."""

    def test_complete_with_bonus_condition_passed(self, client, group_ctx, admin_token):
        """Complete task with passed bonus condition adds bonus points."""
        h = group_ctx["headers"]
        res = client.post("/api/tasks", json={"name": "阅读", "emoji": "📖", "base_points": 20}, headers=h)
        task_id = res.json()["id"]

        resp = client.post(
            "/api/admin/conditions",
            json={
                "group_id": group_ctx["id"],
                "name": "早起完成",
                "reward_type": "bonus_points",
                "bonus_value": 10,
                "task_ids": [task_id],
            },
            headers=admin_token,
        )
        cond_id = resp.json()["condition_id"]

        client.post(
            "/api/tasks/conditions/accept",
            json={"condition_id": cond_id, "task_id": task_id},
            headers=h,
        )

        r = client.post(
            "/api/tasks/complete",
            json={
                "task_id": task_id,
                "star_rating": 4,
                "condition_results": [{"condition_id": cond_id, "passed": True}],
            },
            headers=h,
        )
        assert r.status_code == 200
        data = r.json()
        # base=20, star 4=1.0x=20, bonus +10 = 30
        assert data["earned_points"] >= 20 + 10

    def test_complete_with_bonus_condition_failed(self, client, group_ctx, admin_token):
        """Complete task with failed bonus condition subtracts bonus points (min 1)."""
        h = group_ctx["headers"]
        res = client.post("/api/tasks", json={"name": "阅读", "emoji": "📖", "base_points": 20}, headers=h)
        task_id = res.json()["id"]

        resp = client.post(
            "/api/admin/conditions",
            json={
                "group_id": group_ctx["id"],
                "name": "早起完成",
                "reward_type": "bonus_points",
                "bonus_value": 10,
                "task_ids": [task_id],
            },
            headers=admin_token,
        )
        cond_id = resp.json()["condition_id"]

        client.post(
            "/api/tasks/conditions/accept",
            json={"condition_id": cond_id, "task_id": task_id},
            headers=h,
        )

        r = client.post(
            "/api/tasks/complete",
            json={
                "task_id": task_id,
                "star_rating": 4,
                "condition_results": [{"condition_id": cond_id, "passed": False}],
            },
            headers=h,
        )
        assert r.status_code == 200
        data = r.json()
        # base=20, star 4=1.0x=20, bonus -10 = 10, max(1, 10) = 10
        assert data["earned_points"] <= 20
        assert data["earned_points"] >= 1

    def test_complete_with_multiplier_condition_passed(self, client, group_ctx, admin_token):
        """Complete task with passed multiplier condition multiplies points."""
        h = group_ctx["headers"]
        res = client.post("/api/tasks", json={"name": "运动", "emoji": "🏃", "base_points": 20}, headers=h)
        task_id = res.json()["id"]

        resp = client.post(
            "/api/admin/conditions",
            json={
                "group_id": group_ctx["id"],
                "name": "超常发挥",
                "reward_type": "multiplier",
                "multiplier_value": 1.5,
                "task_ids": [task_id],
            },
            headers=admin_token,
        )
        cond_id = resp.json()["condition_id"]

        client.post(
            "/api/tasks/conditions/accept",
            json={"condition_id": cond_id, "task_id": task_id},
            headers=h,
        )

        r = client.post(
            "/api/tasks/complete",
            json={
                "task_id": task_id,
                "star_rating": 4,
                "condition_results": [{"condition_id": cond_id, "passed": True}],
            },
            headers=h,
        )
        assert r.status_code == 200
        # base=20, star 4=1.0x, multiplier 1.5x → round(20 * 1.0 * 1.5) = 30
        assert r.json()["earned_points"] == 30

    def test_complete_with_multiplier_condition_failed(self, client, group_ctx, admin_token):
        """Complete task with failed multiplier condition divides points."""
        h = group_ctx["headers"]
        res = client.post("/api/tasks", json={"name": "运动", "emoji": "🏃", "base_points": 30}, headers=h)
        task_id = res.json()["id"]

        resp = client.post(
            "/api/admin/conditions",
            json={
                "group_id": group_ctx["id"],
                "name": "超常发挥",
                "reward_type": "multiplier",
                "multiplier_value": 1.5,
                "task_ids": [task_id],
            },
            headers=admin_token,
        )
        cond_id = resp.json()["condition_id"]

        client.post(
            "/api/tasks/conditions/accept",
            json={"condition_id": cond_id, "task_id": task_id},
            headers=h,
        )

        r = client.post(
            "/api/tasks/complete",
            json={
                "task_id": task_id,
                "star_rating": 4,
                "condition_results": [{"condition_id": cond_id, "passed": False}],
            },
            headers=h,
        )
        assert r.status_code == 200
        # base=30, star 4=1.0x, multiply by 1/1.5 → round(30 / 1.5) = 20
        assert r.json()["earned_points"] == 20

    def test_complete_without_accepting_condition(self, client, group_ctx, admin_token):
        """Completing a task without accepting conditions ignores them."""
        h = group_ctx["headers"]
        res = client.post("/api/tasks", json={"name": "阅读", "emoji": "📖", "base_points": 20}, headers=h)
        task_id = res.json()["id"]

        resp = client.post(
            "/api/admin/conditions",
            json={
                "group_id": group_ctx["id"],
                "name": "早起完成",
                "reward_type": "bonus_points",
                "bonus_value": 10,
                "task_ids": [task_id],
            },
            headers=admin_token,
        )
        cond_id = resp.json()["condition_id"]

        r = client.post(
            "/api/tasks/complete",
            json={
                "task_id": task_id,
                "star_rating": 4,
                "condition_results": [{"condition_id": cond_id, "passed": True}],
            },
            headers=h,
        )
        assert r.status_code == 200
        # Not accepted, so condition is ignored. Base only: 20 * 1.0 = 20
        assert r.json()["earned_points"] == 20


class TestTaskConditionsEndpoint:
    """GET /api/tasks/{task_id}/conditions returns conditions for a specific task."""

    def test_get_task_conditions(self, client, group_ctx, admin_token):
        """Get conditions bound to a specific task."""
        h = group_ctx["headers"]
        res = client.post("/api/tasks", json={"name": "阅读", "emoji": "📖", "base_points": 20}, headers=h)
        task_id = res.json()["id"]

        client.post(
            "/api/admin/conditions",
            json={
                "group_id": group_ctx["id"],
                "name": "早起完成",
                "reward_type": "bonus_points",
                "bonus_value": 10,
                "task_ids": [task_id],
            },
            headers=admin_token,
        )

        r = client.get(f"/api/tasks/{task_id}/conditions", headers=h)
        assert r.status_code == 200
        conds = r.json()
        # Need to trigger daily conditions first
        client.get("/api/tasks/conditions/today", headers=h)
        r2 = client.get(f"/api/tasks/{task_id}/conditions", headers=h)
        assert r2.status_code == 200

    def test_get_task_conditions_nonexistent_task(self, client, group_ctx):
        """Getting conditions for a non-existent task returns 404."""
        r = client.get("/api/tasks/99999/conditions", headers=group_ctx["headers"])
        assert r.status_code == 404
