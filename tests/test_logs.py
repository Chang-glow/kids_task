"""Tests for logs, punish, and stats routes."""


class TestLogs:
    def test_get_logs_with_group(self, client, group_ctx):
        """GET /api/logs returns paginated logs."""
        h = group_ctx["headers"]
        task_res = client.post(
            "/api/tasks",
            json={"name": "X", "emoji": "📖", "base_points": 10},
            headers=h,
        )
        client.post(
            "/api/tasks/complete",
            json={"task_id": task_res.json()["id"], "star_rating": 5},
            headers=h,
        )

        res = client.get("/api/logs", headers=h)
        assert res.status_code == 200
        data = res.json()
        assert "total" in data
        assert data["total"] >= 1
        assert len(data["logs"]) >= 1

    def test_get_logs_pagination(self, client, group_ctx):
        """Logs support offset/limit pagination."""
        res = client.get("/api/logs?offset=0&limit=1", headers=group_ctx["headers"])
        assert res.status_code == 200
        assert len(res.json()["logs"]) <= 1


class TestPunish:
    def test_punish_deducts_points(self, client, group_ctx):
        """Punish deducts from first child, never below zero."""
        h = group_ctx["headers"]
        task_res = client.post(
            "/api/tasks",
            json={"name": "赚积分", "emoji": "💰", "base_points": 50},
            headers=h,
        )
        client.post(
            "/api/tasks/complete",
            json={"task_id": task_res.json()["id"], "star_rating": 5},
            headers=h,
        )

        res = client.post(
            "/api/punish",
            json={"name": "没做作业", "emoji": "😤", "penalty_points": 5},
            headers=h,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["deducted_points"] == 5

    def test_punish_validation(self, client, group_ctx):
        """Punish requires positive points and non-empty name."""
        h = group_ctx["headers"]
        assert client.post("/api/punish", json={"name": "", "emoji": "😤", "penalty_points": 5}, headers=h).status_code == 400
        assert client.post("/api/punish", json={"name": "X", "emoji": "😤", "penalty_points": 0}, headers=h).status_code == 400


class TestStats:
    def test_get_stats_with_group(self, client, group_ctx):
        """GET /api/stats returns day/week/month aggregation."""
        res = client.get("/api/stats", headers=group_ctx["headers"])
        assert res.status_code == 200
        data = res.json()
        for period in ["day", "week", "month"]:
            assert period in data
            assert isinstance(data[period], list)
