"""Tests for daily conditions, condition CRUD, acceptance, and completion with results."""


class TestDailyConditions:
    """GET /api/tasks/conditions/today returns today's conditions."""

    def test_conditions_today_empty(self, client, group_ctx):
        """Conditions/today returns empty list when no conditions exist."""
        res = client.get("/api/tasks/conditions/today", headers=group_ctx["headers"])
        assert res.status_code == 200
        assert res.json()["conditions"] == []

    def test_conditions_today_idempotent(self, client, group_ctx):
        """Calling conditions/today twice returns same results."""
        res1 = client.get("/api/tasks/conditions/today", headers=group_ctx["headers"])
        res2 = client.get("/api/tasks/conditions/today", headers=group_ctx["headers"])
        assert res1.json()["conditions"] == res2.json()["conditions"]


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

        conds = client.get("/api/tasks/conditions/today", headers=h).json()["conditions"]
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
        """Complete task with failed multiplier condition — delta 加算惩罚，对称于奖励。"""
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
        # base=30, star 4=1.0x, fail ×1.5 → delta=-(1.5-1)=-0.5, cond=0.5 → round(30*0.5)=15
        assert r.json()["earned_points"] == 15

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


class TestConditionOverrides:
    """Admin condition lock_in / lock_out."""

    def test_lock_in_condition(self, client, group_ctx, admin_token):
        """A lock_in condition always appears in daily selections."""
        h = group_ctx["headers"]
        res = client.post("/api/tasks", json={"name": "阅读", "emoji": "📖", "base_points": 20}, headers=h)
        task_id = res.json()["id"]

        resp = client.post(
            "/api/admin/conditions",
            json={
                "group_id": group_ctx["id"],
                "name": "锁定条件",
                "reward_type": "bonus_points",
                "bonus_value": 10,
                "task_ids": [task_id],
            },
            headers=admin_token,
        )
        cond_id = resp.json()["condition_id"]

        # set lock_in
        r = client.post(
            "/api/admin/condition-overrides",
            json={"group_id": group_ctx["id"], "condition_id": cond_id, "override_type": "lock_in"},
            headers=admin_token,
        )
        assert r.status_code == 200

        conds = client.get("/api/tasks/conditions/today", headers=h).json()["conditions"]
        matching = [c for c in conds if c["id"] == cond_id]
        assert len(matching) == 1

    def test_lock_out_condition(self, client, group_ctx, admin_token):
        """A lock_out condition never appears in daily selections."""
        h = group_ctx["headers"]
        res = client.post("/api/tasks", json={"name": "阅读", "emoji": "📖", "base_points": 20}, headers=h)
        task_id = res.json()["id"]

        resp = client.post(
            "/api/admin/conditions",
            json={
                "group_id": group_ctx["id"],
                "name": "免疫条件",
                "reward_type": "bonus_points",
                "bonus_value": 10,
                "task_ids": [task_id],
            },
            headers=admin_token,
        )
        cond_id = resp.json()["condition_id"]

        # set lock_out
        client.post(
            "/api/admin/condition-overrides",
            json={"group_id": group_ctx["id"], "condition_id": cond_id, "override_type": "lock_out"},
            headers=admin_token,
        )

        conds = client.get("/api/tasks/conditions/today", headers=h).json()["conditions"]
        matching = [c for c in conds if c["id"] == cond_id]
        assert len(matching) == 0

    def test_clear_override(self, client, group_ctx, admin_token):
        """Clearing an override returns condition to normal selection."""
        h = group_ctx["headers"]
        res = client.post("/api/tasks", json={"name": "阅读", "emoji": "📖", "base_points": 20}, headers=h)
        task_id = res.json()["id"]

        resp = client.post(
            "/api/admin/conditions",
            json={
                "group_id": group_ctx["id"],
                "name": "普通条件",
                "reward_type": "bonus_points",
                "bonus_value": 10,
                "task_ids": [task_id],
            },
            headers=admin_token,
        )
        cond_id = resp.json()["condition_id"]

        client.post(
            "/api/admin/condition-overrides",
            json={"group_id": group_ctx["id"], "condition_id": cond_id, "override_type": "lock_in"},
            headers=admin_token,
        )
        client.post(
            "/api/admin/condition-overrides",
            json={"group_id": group_ctx["id"], "condition_id": cond_id, "override_type": "none"},
            headers=admin_token,
        )

        r = client.get(
            f"/api/admin/condition-overrides?group_id={group_ctx['id']}",
            headers=admin_token,
        )
        matching = [o for o in r.json() if o["condition_id"] == cond_id]
        assert len(matching) == 0


class TestConditionRefresh:
    """POST /api/tasks/conditions/refresh."""

    def test_refresh_free(self, client, group_ctx, admin_token):
        """First two refreshes are free."""
        h = group_ctx["headers"]
        res = client.post("/api/tasks", json={"name": "阅读", "emoji": "📖", "base_points": 20}, headers=h)
        task_id = res.json()["id"]

        client.post(
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

        r = client.post("/api/tasks/conditions/refresh", headers=h)
        assert r.status_code == 200
        data = r.json()
        assert data["point_cost"] == 0
        assert data["free_refreshes_left"] == 1

    def test_refresh_second_free(self, client, group_ctx, admin_token):
        """Second refresh is also free."""
        h = group_ctx["headers"]
        res = client.post("/api/tasks", json={"name": "阅读", "emoji": "📖", "base_points": 20}, headers=h)
        task_id = res.json()["id"]

        client.post(
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

        client.post("/api/tasks/conditions/refresh", headers=h)
        r = client.post("/api/tasks/conditions/refresh", headers=h)
        assert r.status_code == 200
        data = r.json()
        assert data["point_cost"] == 0
        assert data["free_refreshes_left"] == 0

    def test_refresh_keeps_accepted(self, client, group_ctx, admin_token):
        """Refresh only replaces unaccepted conditions, keeps accepted ones."""
        h = group_ctx["headers"]
        # create two tasks, two conditions
        res1 = client.post("/api/tasks", json={"name": "任务A", "emoji": "📖", "base_points": 20}, headers=h)
        res2 = client.post("/api/tasks", json={"name": "任务B", "emoji": "📖", "base_points": 20}, headers=h)
        t1 = res1.json()["id"]
        t2 = res2.json()["id"]

        c1 = client.post(
            "/api/admin/conditions",
            json={"group_id": group_ctx["id"], "name": "条件A", "reward_type": "bonus_points",
                  "bonus_value": 10, "task_ids": [t1]},
            headers=admin_token,
        ).json()["condition_id"]
        c2 = client.post(
            "/api/admin/conditions",
            json={"group_id": group_ctx["id"], "name": "条件B", "reward_type": "bonus_points",
                  "bonus_value": 10, "task_ids": [t2]},
            headers=admin_token,
        ).json()["condition_id"]

        # force lock_in both so they appear
        client.post("/api/admin/condition-overrides",
                    json={"group_id": group_ctx["id"], "condition_id": c1, "override_type": "lock_in"},
                    headers=admin_token)
        client.post("/api/admin/condition-overrides",
                    json={"group_id": group_ctx["id"], "condition_id": c2, "override_type": "lock_in"},
                    headers=admin_token)

        # accept c1
        client.post("/api/tasks/conditions/accept", json={"condition_id": c1, "task_id": t1}, headers=h)

        # refresh
        r = client.post("/api/tasks/conditions/refresh", headers=h)
        assert r.status_code == 200

        conds = client.get("/api/tasks/conditions/today", headers=h).json()["conditions"]
        accepted_ids = [c["id"] for c in conds if c["accepted"]]
        assert c1 in accepted_ids  # still here

    def test_refresh_response_format(self, client, group_ctx, admin_token):
        """Conditions/today returns conditions list and refresh info."""
        h = group_ctx["headers"]
        res = client.get("/api/tasks/conditions/today", headers=h)
        assert res.status_code == 200
        data = res.json()
        assert "conditions" in data
        assert "refresh" in data
        assert "free_refreshes_left" in data["refresh"]
        assert "next_refresh_cost" in data["refresh"]


class TestTaskSetRandomEager:
    """task_set_random randomly selects subset and shows to child."""

    def test_taskset_random_shows_selected_tasks(self, client, group_ctx, admin_token):
        """task_set_random conditions show selected_task_names before any completion."""
        h = group_ctx["headers"]
        t1 = client.post("/api/tasks", json={"name": "任务1", "emoji": "📖", "base_points": 20}, headers=h).json()["id"]
        t2 = client.post("/api/tasks", json={"name": "任务2", "emoji": "📖", "base_points": 20}, headers=h).json()["id"]
        t3 = client.post("/api/tasks", json={"name": "任务3", "emoji": "📖", "base_points": 20}, headers=h).json()["id"]

        resp = client.post(
            "/api/admin/conditions",
            json={
                "group_id": group_ctx["id"],
                "name": "随机任务集",
                "reward_type": "bonus_points",
                "condition_type": "task_set_random",
                "bonus_value": 20,
                "subset_size": 2,
                "task_ids": [t1, t2, t3],
            },
            headers=admin_token,
        )
        assert resp.status_code == 200

        conds = client.get("/api/tasks/conditions/today", headers=h).json()["conditions"]
        ts_conds = [c for c in conds if c["condition_type"] == "task_set_random"]
        assert len(ts_conds) == 1
        tp = ts_conds[0]["taskset_progress"]
        assert tp is not None
        assert len(tp["selected_tasks"]) == 2
        assert len(tp["selected_task_names"]) == 2
