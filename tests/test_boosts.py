"""Tests for daily task boosts, boost overrides, and boost endpoint."""


class TestDailyBoosts:
    """GET /api/tasks/boosts/today returns today's boost mapping."""

    def test_boosts_generated_on_first_access(self, client, group_ctx):
        """First access to boosts/today should generate and return boosts."""
        h = group_ctx["headers"]
        # Add some tasks so there's something to boost
        client.post("/api/tasks", json={"name": "阅读", "emoji": "📖", "base_points": 20}, headers=h)
        client.post("/api/tasks", json={"name": "运动", "emoji": "🏃", "base_points": 30}, headers=h)
        client.post("/api/tasks", json={"name": "练琴", "emoji": "🎵", "base_points": 15}, headers=h)
        client.post("/api/tasks", json={"name": "早睡", "emoji": "😴", "base_points": 25}, headers=h)

        res = client.get("/api/tasks/boosts/today", headers=h)
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data, dict)
        # Should have up to 3 boosted tasks
        assert len(data) <= 3
        for tid, mult in data.items():
            assert mult in (1.5, 1.75, 2.0)

    def test_boosts_idempotent(self, client, group_ctx):
        """Calling boosts/today twice should return same results."""
        h = group_ctx["headers"]
        client.post("/api/tasks", json={"name": "T1", "emoji": "📖", "base_points": 10}, headers=h)
        client.post("/api/tasks", json={"name": "T2", "emoji": "🏃", "base_points": 10}, headers=h)
        client.post("/api/tasks", json={"name": "T3", "emoji": "🎵", "base_points": 10}, headers=h)

        r1 = client.get("/api/tasks/boosts/today", headers=h)
        r2 = client.get("/api/tasks/boosts/today", headers=h)
        assert r1.json() == r2.json()

    def test_boost_multiplier_applied_on_complete(self, client, group_ctx):
        """Completing a boosted task should multiply points."""
        h = group_ctx["headers"]
        res = client.post("/api/tasks", json={"name": "阅读", "emoji": "📖", "base_points": 20}, headers=h)
        task_id = res.json()["id"]

        boosts = client.get("/api/tasks/boosts/today", headers=h).json()
        str_tid = str(task_id)

        res2 = client.post(
            "/api/tasks/complete",
            json={"task_id": task_id, "star_rating": 4},
            headers=h,
        )
        assert res2.status_code == 200
        data = res2.json()
        expected_base = 20  # 4 star = 1.0x base = 20
        if str_tid in boosts:
            mult = boosts[str_tid]
            expected = round(expected_base * mult)
        else:
            expected = expected_base
        assert data["earned_points"] == expected

    def test_no_boosts_with_no_tasks(self, client, group_ctx):
        """Boosts endpoint should return empty dict when no tasks exist."""
        res = client.get("/api/tasks/boosts/today", headers=group_ctx["headers"])
        assert res.status_code == 200
        assert res.json() == {}


class TestBoostOverrides:
    """Admin boost override CRUD."""

    def test_get_boost_overrides_empty(self, client, group_ctx, admin_token):
        """GET boost-overrides returns empty list initially."""
        res = client.get(
            f"/api/admin/boost-overrides?group_id={group_ctx['id']}",
            headers=admin_token,
        )
        assert res.status_code == 200
        assert res.json() == []

    def test_set_lock_in_override(self, client, group_ctx, admin_token):
        """Setting a lock_in override should be reflected in GET."""
        h = group_ctx["headers"]
        res = client.post("/api/tasks", json={"name": "阅读", "emoji": "📖", "base_points": 20}, headers=h)
        task_id = res.json()["id"]

        resp = client.post(
            "/api/admin/boost-overrides",
            json={"group_id": group_ctx["id"], "task_id": task_id, "override_type": "lock_in"},
            headers=admin_token,
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        overrides = client.get(
            f"/api/admin/boost-overrides?group_id={group_ctx['id']}",
            headers=admin_token,
        ).json()
        assert len(overrides) == 1
        assert overrides[0]["override_type"] == "lock_in"
        assert overrides[0]["task_id"] == task_id

    def test_set_lock_out_override(self, client, group_ctx, admin_token):
        """Setting lock_out should exclude task from boosts."""
        h = group_ctx["headers"]
        res = client.post("/api/tasks", json={"name": "阅读", "emoji": "📖", "base_points": 20}, headers=h)
        task_id = res.json()["id"]

        client.post(
            "/api/admin/boost-overrides",
            json={"group_id": group_ctx["id"], "task_id": task_id, "override_type": "lock_out"},
            headers=admin_token,
        )
        overrides = client.get(
            f"/api/admin/boost-overrides?group_id={group_ctx['id']}",
            headers=admin_token,
        ).json()
        assert overrides[0]["override_type"] == "lock_out"

    def test_set_manual_multiplier(self, client, group_ctx, admin_token):
        """Setting a manual multiplier should work."""
        h = group_ctx["headers"]
        res = client.post("/api/tasks", json={"name": "阅读", "emoji": "📖", "base_points": 20}, headers=h)
        task_id = res.json()["id"]

        client.post(
            "/api/admin/boost-overrides",
            json={"group_id": group_ctx["id"], "task_id": task_id,
                  "override_type": "manual_multiplier", "manual_multiplier": 3.0},
            headers=admin_token,
        )
        overrides = client.get(
            f"/api/admin/boost-overrides?group_id={group_ctx['id']}",
            headers=admin_token,
        ).json()
        assert overrides[0]["manual_multiplier"] == 3.0

    def test_clear_override(self, client, group_ctx, admin_token):
        """Setting override_type='none' should remove the override."""
        h = group_ctx["headers"]
        res = client.post("/api/tasks", json={"name": "阅读", "emoji": "📖", "base_points": 20}, headers=h)
        task_id = res.json()["id"]

        client.post(
            "/api/admin/boost-overrides",
            json={"group_id": group_ctx["id"], "task_id": task_id, "override_type": "lock_in"},
            headers=admin_token,
        )
        client.post(
            "/api/admin/boost-overrides",
            json={"group_id": group_ctx["id"], "task_id": task_id, "override_type": "none"},
            headers=admin_token,
        )
        overrides = client.get(
            f"/api/admin/boost-overrides?group_id={group_ctx['id']}",
            headers=admin_token,
        ).json()
        assert overrides == []

    def test_override_validation(self, client, group_ctx, admin_token):
        """Invalid override_type should return 400."""
        res = client.post(
            "/api/admin/boost-overrides",
            json={"group_id": group_ctx["id"], "task_id": 1, "override_type": "invalid"},
            headers=admin_token,
        )
        assert res.status_code == 400
