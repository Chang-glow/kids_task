"""Tests for reward routes with group_id filtering."""


class TestRewards:
    """Reward CRUD + redeem with group_id filtering."""

    def test_add_and_list_rewards(self, client, group_ctx):
        """POST /api/rewards creates reward, GET returns it."""
        h = group_ctx["headers"]
        res = client.post(
            "/api/rewards",
            json={"name": "看电视", "emoji": "📺", "cost_points": 30},
            headers=h,
        )
        assert res.status_code == 200
        reward = res.json()
        assert reward["name"] == "看电视"
        assert reward["group_id"] == group_ctx["id"]

        res2 = client.get("/api/rewards", headers=h)
        assert res2.status_code == 200
        ids = [r["id"] for r in res2.json()]
        assert reward["id"] in ids

    def test_add_reward_validation(self, client, group_ctx):
        """POST /api/rewards with empty name or zero cost returns 400."""
        h = group_ctx["headers"]
        assert client.post("/api/rewards", json={"name": "", "emoji": "📺", "cost_points": 10}, headers=h).status_code == 400
        assert client.post("/api/rewards", json={"name": "X", "emoji": "📺", "cost_points": 0}, headers=h).status_code == 400

    def test_redeem_reward(self, client, group_ctx):
        """Redeeming a reward deducts points from the first child."""
        h = group_ctx["headers"]
        res = client.post(
            "/api/rewards",
            json={"name": "零食", "emoji": "🍬", "cost_points": 40},
            headers=h,
        )
        reward_id = res.json()["id"]

        task_res = client.post(
            "/api/tasks",
            json={"name": "赚积分", "emoji": "💰", "base_points": 100},
            headers=h,
        )
        client.post(
            "/api/tasks/complete",
            json={"task_id": task_res.json()["id"], "star_rating": 5},
            headers=h,
        )

        res2 = client.post(
            "/api/rewards/redeem",
            json={"reward_id": reward_id},
            headers=h,
        )
        assert res2.status_code == 200
        data = res2.json()
        assert data["success"] is True
        assert data["spent_points"] == 40

    def test_redeem_insufficient_points(self, client, group_ctx):
        """Redeeming with insufficient points returns 400."""
        h = group_ctx["headers"]
        res = client.post(
            "/api/rewards",
            json={"name": "大奖励", "emoji": "🎁", "cost_points": 999999},
            headers=h,
        )
        reward_id = res.json()["id"]

        res2 = client.post(
            "/api/rewards/redeem",
            json={"reward_id": reward_id},
            headers=h,
        )
        assert res2.status_code == 400
        assert "积分不够" in res2.json()["detail"]

    def test_redeem_nonexistent_reward(self, client, group_ctx):
        """Redeeming a missing reward returns 404."""
        res = client.post(
            "/api/rewards/redeem",
            json={"reward_id": 99999},
            headers=group_ctx["headers"],
        )
        assert res.status_code == 404

    def test_delete_reward(self, client, group_ctx):
        """DELETE /api/rewards/{id} removes the reward."""
        h = group_ctx["headers"]
        res = client.post(
            "/api/rewards",
            json={"name": "临时", "emoji": "🗑️", "cost_points": 5},
            headers=h,
        )
        reward_id = res.json()["id"]

        del_res = client.delete(f"/api/rewards/{reward_id}", headers=h)
        assert del_res.status_code == 200
        assert del_res.json()["success"] is True

        assert client.delete(f"/api/rewards/{reward_id}", headers=h).status_code == 404

    def test_rewards_isolated_by_group(self, client):
        """Rewards from group A should not appear in group B."""
        g1 = client.post("/api/groups", json={"name": "A", "child_name": "甲"}).json()
        g2 = client.post("/api/groups", json={"name": "B", "child_name": "乙"}).json()
        h1 = {"X-Group-Code": g1["invite_code"]}
        h2 = {"X-Group-Code": g2["invite_code"]}

        client.post("/api/rewards", json={"name": "Reward A", "emoji": "🅰️", "cost_points": 10}, headers=h1)
        client.post("/api/rewards", json={"name": "Reward B", "emoji": "🅱️", "cost_points": 20}, headers=h2)

        rewards_a = client.get("/api/rewards", headers=h1).json()
        rewards_b = client.get("/api/rewards", headers=h2).json()

        names_a = [r["name"] for r in rewards_a]
        names_b = [r["name"] for r in rewards_b]
        assert "Reward A" in names_a
        assert "Reward B" not in names_a
        assert "Reward B" in names_b
        assert "Reward A" not in names_b
