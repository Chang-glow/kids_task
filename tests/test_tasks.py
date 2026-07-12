"""Tests for task routes with group_id filtering."""


class TestTasks:
    """Task CRUD + complete with group_id filtering."""

    def test_add_and_list_tasks(self, client, group_ctx):
        """POST /api/tasks creates a task, GET returns it."""
        res = client.post(
            "/api/tasks",
            json={"name": "阅读", "emoji": "📖", "base_points": 20},
            headers=group_ctx["headers"],
        )
        assert res.status_code == 200
        task = res.json()
        assert task["name"] == "阅读"
        assert task["group_id"] == group_ctx["id"]

        res2 = client.get("/api/tasks", headers=group_ctx["headers"])
        assert res2.status_code == 200
        tasks = res2.json()
        ids = [t["id"] for t in tasks]
        assert task["id"] in ids

    def test_add_task_validation(self, client, group_ctx):
        """POST /api/tasks with empty name or zero points returns 400."""
        h = group_ctx["headers"]
        assert client.post("/api/tasks", json={"name": "", "emoji": "📖", "base_points": 10}, headers=h).status_code == 400
        assert client.post("/api/tasks", json={"name": "X", "emoji": "📖", "base_points": 0}, headers=h).status_code == 400

    def test_complete_non_repeatable_task(self, client, group_ctx):
        """Completing a non-repeatable task marks it done and awards points."""
        h = group_ctx["headers"]
        res = client.post(
            "/api/tasks",
            json={"name": "数学作业", "emoji": "✏️", "base_points": 25},
            headers=h,
        )
        task_id = res.json()["id"]

        res2 = client.post(
            "/api/tasks/complete",
            json={"task_id": task_id, "star_rating": 5},
            headers=h,
        )
        assert res2.status_code == 200
        data = res2.json()
        assert data["success"] is True
        assert data["earned_points"] == 30  # 25 * 1.2 = 30

        res3 = client.get("/api/tasks", headers=h)
        task = [t for t in res3.json() if t["id"] == task_id][0]
        assert task["status"] == "done"

        assert client.post(
            "/api/tasks/complete",
            json={"task_id": task_id, "star_rating": 3},
            headers=h,
        ).status_code == 400

    def test_complete_repeatable_task(self, client, group_ctx):
        """Completing a repeatable task stays pending, always re-completable."""
        h = group_ctx["headers"]
        res = client.post(
            "/api/tasks",
            json={"name": "整理房间", "emoji": "🧹", "base_points": 10, "is_repeatable": True},
            headers=h,
        )
        task_id = res.json()["id"]

        r1 = client.post(
            "/api/tasks/complete",
            json={"task_id": task_id, "star_rating": 4},
            headers=h,
        )
        assert r1.status_code == 200
        assert r1.json()["earned_points"] == 10

        res2 = client.get("/api/tasks", headers=h)
        task = [t for t in res2.json() if t["id"] == task_id][0]
        assert task["status"] == "pending"

        r2 = client.post(
            "/api/tasks/complete",
            json={"task_id": task_id, "star_rating": 3},
            headers=h,
        )
        assert r2.status_code == 200

    def test_complete_task_bad_star_rating(self, client, group_ctx):
        """Star rating must be 1-5."""
        h = group_ctx["headers"]
        res = client.post(
            "/api/tasks",
            json={"name": "X", "emoji": "📖", "base_points": 10},
            headers=h,
        )
        task_id = res.json()["id"]
        assert client.post(
            "/api/tasks/complete",
            json={"task_id": task_id, "star_rating": 0},
            headers=h,
        ).status_code == 400
        assert client.post(
            "/api/tasks/complete",
            json={"task_id": task_id, "star_rating": 6},
            headers=h,
        ).status_code == 400

    def test_complete_nonexistent_task(self, client, group_ctx):
        """Completing a missing task returns 404."""
        res = client.post(
            "/api/tasks/complete",
            json={"task_id": 99999, "star_rating": 5},
            headers=group_ctx["headers"],
        )
        assert res.status_code == 404

    def test_delete_task(self, client, group_ctx):
        """DELETE /api/tasks/{id} removes the task."""
        h = group_ctx["headers"]
        res = client.post(
            "/api/tasks",
            json={"name": "临时任务", "emoji": "🗑️", "base_points": 5},
            headers=h,
        )
        task_id = res.json()["id"]

        del_res = client.delete(f"/api/tasks/{task_id}", headers=h)
        assert del_res.status_code == 200
        assert del_res.json()["success"] is True

        assert client.delete(f"/api/tasks/{task_id}", headers=h).status_code == 404

    def test_tasks_isolated_by_group(self, client):
        """Tasks from group A should not appear in group B."""
        g1 = client.post("/api/groups", json={"name": "A", "child_name": "甲"}).json()
        g2 = client.post("/api/groups", json={"name": "B", "child_name": "乙"}).json()
        h1 = {"X-Group-Code": g1["invite_code"]}
        h2 = {"X-Group-Code": g2["invite_code"]}

        client.post("/api/tasks", json={"name": "Group A Task", "emoji": "🅰️", "base_points": 10}, headers=h1)
        client.post("/api/tasks", json={"name": "Group B Task", "emoji": "🅱️", "base_points": 20}, headers=h2)

        tasks_a = client.get("/api/tasks", headers=h1).json()
        tasks_b = client.get("/api/tasks", headers=h2).json()

        names_a = [t["name"] for t in tasks_a]
        names_b = [t["name"] for t in tasks_b]
        assert "Group A Task" in names_a
        assert "Group B Task" not in names_a
        assert "Group B Task" in names_b
        assert "Group A Task" not in names_b
