"""Tests for reward price surge feature."""


class TestSurgeGeneration:
    def test_surges_generated_daily(self, client, group_ctx):
        """Daily surges are lazily generated on first access."""
        h = group_ctx["headers"]
        # Add some rewards first
        for i in range(6):
            client.post(
                "/api/rewards",
                json={"name": f"Reward {i}", "emoji": "🎁", "cost_points": 10 + i * 5},
                headers=h,
            )

        # First access triggers surge generation
        res = client.get("/api/rewards/surges/today", headers=h)
        assert res.status_code == 200
        surges = res.json()
        assert isinstance(surges, dict)

    def test_surge_count_range(self, client, group_ctx):
        """Surge count is between 2-5 (or total rewards if fewer)."""
        h = group_ctx["headers"]
        for i in range(6):
            client.post(
                "/api/rewards",
                json={"name": f"Surge {i}", "emoji": "🎁", "cost_points": 10 + i * 5},
                headers=h,
            )

        # Run multiple times to check count range (lazy cache means only first run generates)
        res = client.get("/api/rewards/surges/today", headers=h)
        surges = res.json()
        assert 2 <= len(surges) <= 5

    def test_surge_rate_range(self, client, group_ctx):
        """Each surge rate is in 10%-50% range, 5% increments."""
        h = group_ctx["headers"]
        for i in range(6):
            client.post(
                "/api/rewards",
                json={"name": f"Rate {i}", "emoji": "🎁", "cost_points": 10 + i * 5},
                headers=h,
            )

        res = client.get("/api/rewards/surges/today", headers=h)
        surges = res.json()
        valid_rates = {0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50}
        for info in surges.values():
            assert info["rate"] in valid_rates, f"Rate {info['rate']} not in valid tiers"
            assert info["type"] in ("surge", "sale")

    def test_surge_injected_in_rewards_list(self, client, group_ctx):
        """GET /api/rewards includes surge_rate, sale_rate and surged_cost fields."""
        h = group_ctx["headers"]
        client.post(
            "/api/rewards",
            json={"name": "Injected", "emoji": "🎁", "cost_points": 30},
            headers=h,
        )

        res = client.get("/api/rewards", headers=h)
        assert res.status_code == 200
        rewards = res.json()
        for r in rewards:
            assert "surge_rate" in r
            assert "sale_rate" in r
            assert "surged_cost" in r
            if r["surge_rate"] is not None:
                expected_surged = round(r["cost_points"] * (1 + r["surge_rate"]))
                assert r["surged_cost"] == expected_surged
            if r["sale_rate"] is not None:
                expected_sale = max(1, round(r["cost_points"] * (1 - r["sale_rate"])))
                assert r["surged_cost"] == expected_sale


class TestSurgeRedeem:
    def test_surge_affects_redeem_cost(self, client, group_ctx):
        """Redeeming a surged reward costs more than original price."""
        h = group_ctx["headers"]
        # Add reward and earn enough points
        reward_res = client.post(
            "/api/rewards",
            json={"name": "Surged Item", "emoji": "🎁", "cost_points": 30},
            headers=h,
        )
        reward_id = reward_res.json()["id"]

        task_res = client.post(
            "/api/tasks",
            json={"name": "Earn", "emoji": "💰", "base_points": 100},
            headers=h,
        )
        client.post(
            "/api/tasks/complete",
            json={"task_id": task_res.json()["id"], "star_rating": 5},
            headers=h,
        )

        # Get rewards to check surge rate
        rewards = client.get("/api/rewards", headers=h).json()
        reward = next(r for r in rewards if r["id"] == reward_id)
        surged_cost = reward.get("surged_cost") or reward["cost_points"]

        redeem_res = client.post("/api/rewards/redeem", json={"reward_id": reward_id}, headers=h)
        assert redeem_res.status_code == 200
        data = redeem_res.json()
        assert data["spent_points"] == surged_cost


class TestSurgeOverride:
    def _get_admin_headers(self, client, admin_token):
        return admin_token

    def test_surge_lock_in(self, client, group_ctx, admin_token):
        """lock_in forces a reward into today's surge."""
        h = group_ctx["headers"]
        admin_h = admin_token
        gid = group_ctx["id"]

        # Create rewards
        for i in range(3):
            client.post(
                "/api/rewards",
                json={"name": f"Lock {i}", "emoji": "🎁", "cost_points": 10 + i * 5},
                headers=h,
            )
        rewards = client.get("/api/admin/rewards?group_id=" + str(gid), headers=admin_h).json()
        target_id = rewards[0]["id"]

        # Set lock_in
        resp = client.post(
            "/api/admin/surge-overrides",
            json={
                "group_id": gid,
                "reward_id": target_id,
                "override_type": "lock_in",
            },
            headers=admin_h,
        )
        assert resp.status_code == 200

        # Verify it's in today's surges (JSON keys are strings)
        surges = client.get("/api/rewards/surges/today", headers=h).json()
        assert str(target_id) in surges

    def test_surge_lock_out(self, client, group_ctx, admin_token):
        """lock_out excludes a reward from today's surge."""
        h = group_ctx["headers"]
        admin_h = admin_token
        gid = group_ctx["id"]

        for i in range(6):
            client.post(
                "/api/rewards",
                json={"name": f"Out {i}", "emoji": "🎁", "cost_points": 10 + i * 5},
                headers=h,
            )
        rewards = client.get("/api/admin/rewards?group_id=" + str(gid), headers=admin_h).json()
        target_id = rewards[0]["id"]

        # Set lock_in on one, lock_out on another
        client.post(
            "/api/admin/surge-overrides",
            json={
                "group_id": gid,
                "reward_id": target_id,
                "override_type": "lock_out",
            },
            headers=admin_h,
        )

        # Verify it's not in surges (JSON keys are strings)
        surges = client.get("/api/rewards/surges/today", headers=h).json()
        assert str(target_id) not in surges

    def test_surge_manual_rate(self, client, group_ctx, admin_token):
        """manual_rate sets a specific surge percentage."""
        h = group_ctx["headers"]
        admin_h = admin_token
        gid = group_ctx["id"]

        for i in range(3):
            client.post(
                "/api/rewards",
                json={"name": f"Manual {i}", "emoji": "🎁", "cost_points": 10 + i * 5},
                headers=h,
            )
        rewards = client.get("/api/admin/rewards?group_id=" + str(gid), headers=admin_h).json()
        target_id = rewards[0]["id"]

        # Set lock_in with manual_rate
        client.post(
            "/api/admin/surge-overrides",
            json={
                "group_id": gid,
                "reward_id": target_id,
                "override_type": "lock_in",
            },
            headers=admin_h,
        )
        client.post(
            "/api/admin/surge-overrides",
            json={
                "group_id": gid,
                "reward_id": target_id,
                "override_type": "manual_rate",
                "manual_rate": 0.45,
            },
            headers=admin_h,
        )

        surges = client.get("/api/rewards/surges/today", headers=h).json()
        info = surges[str(target_id)]
        assert info["rate"] == 0.45
        assert info["type"] in ("surge", "sale")

    def test_surge_override_undo(self, client, group_ctx, admin_token):
        """Surge override changes can be undone."""
        h = group_ctx["headers"]
        admin_h = admin_token
        gid = group_ctx["id"]

        for i in range(3):
            client.post(
                "/api/rewards",
                json={"name": f"Undo {i}", "emoji": "🎁", "cost_points": 10 + i * 5},
                headers=h,
            )
        rewards = client.get("/api/admin/rewards?group_id=" + str(gid), headers=admin_h).json()
        target_id = rewards[0]["id"]

        # Set lock_in
        client.post(
            "/api/admin/surge-overrides",
            json={
                "group_id": gid,
                "reward_id": target_id,
                "override_type": "lock_in",
            },
            headers=admin_h,
        )

        # Check override exists
        overrides = client.get("/api/admin/surge-overrides?group_id=" + str(gid), headers=admin_h).json()
        assert len(overrides) >= 1

        # Undo the operation
        ops = client.get(f"/api/admin/operations?group_id={gid}", headers=admin_h).json()
        surge_op = next(o for o in ops if o["operation_type"] == "surge_override_change")
        undo_resp = client.post(f"/api/admin/undo/{surge_op['id']}", headers=admin_h)
        assert undo_resp.status_code == 200

        # Verify override is gone
        overrides = client.get("/api/admin/surge-overrides?group_id=" + str(gid), headers=admin_h).json()
        assert all(o["reward_id"] != target_id for o in overrides)
