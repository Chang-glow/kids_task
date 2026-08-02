"""Tests for time-based reward pricing feature (replaces surge_service)."""

import pytest


class TestComputeRateAtTime:
    """Pure function: compute_rate_at_time(params, hour, minute) -> float."""

    def test_flat_returns_zero(self):
        """is_flat=True always returns 0.0 regardless of time."""
        from api.services.pricing_service import compute_rate_at_time

        params = {
            "surge_peak_rate": 0.25,
            "sale_trough_rate": 0.15,
            "plateau_minutes": 0,
            "partial_peak_factor": 1.0,
            "is_flat": True,
        }
        assert compute_rate_at_time(params, 8, 0) == 0.0
        assert compute_rate_at_time(params, 11, 0) == 0.0
        assert compute_rate_at_time(params, 1, 0) == 0.0
        assert compute_rate_at_time(params, 18, 30) == 0.0

    def test_zero_at_8am(self):
        """Rate must be 0 at exactly 8:00."""
        from api.services.pricing_service import compute_rate_at_time

        params = {
            "surge_peak_rate": 0.30,
            "sale_trough_rate": 0.20,
            "plateau_minutes": 0,
            "partial_peak_factor": 1.0,
            "is_flat": False,
        }
        assert compute_rate_at_time(params, 8, 0) == 0.0

    def test_zero_at_1830(self):
        """Rate must be 0 at exactly 18:30."""
        from api.services.pricing_service import compute_rate_at_time

        params = {
            "surge_peak_rate": 0.30,
            "sale_trough_rate": 0.20,
            "plateau_minutes": 0,
            "partial_peak_factor": 1.0,
            "is_flat": False,
        }
        assert compute_rate_at_time(params, 18, 30) == 0.0

    def test_peak_at_11am_no_plateau(self):
        """Without plateau, 11:00 should hit the surge peak rate."""
        from api.services.pricing_service import compute_rate_at_time

        params = {
            "surge_peak_rate": 0.30,
            "sale_trough_rate": 0.20,
            "plateau_minutes": 0,
            "partial_peak_factor": 1.0,
            "is_flat": False,
        }
        assert compute_rate_at_time(params, 11, 0) == pytest.approx(0.30)

    def test_trough_at_1am_no_plateau(self):
        """Without plateau, 1:00 should hit the sale trough (negative rate)."""
        from api.services.pricing_service import compute_rate_at_time

        params = {
            "surge_peak_rate": 0.30,
            "sale_trough_rate": 0.20,
            "plateau_minutes": 0,
            "partial_peak_factor": 1.0,
            "is_flat": False,
        }
        assert compute_rate_at_time(params, 1, 0) == pytest.approx(-0.20)

    def test_plateau_keeps_peak_flat(self):
        """With plateau=60min, rate at 10:30, 11:00, 11:30 should all equal peak."""
        from api.services.pricing_service import compute_rate_at_time

        params = {
            "surge_peak_rate": 0.25,
            "sale_trough_rate": 0.15,
            "plateau_minutes": 60,
            "partial_peak_factor": 1.0,
            "is_flat": False,
        }
        # Plateau: 10:30 to 11:30 should all be at peak
        r1 = compute_rate_at_time(params, 10, 30)
        r2 = compute_rate_at_time(params, 11, 0)
        r3 = compute_rate_at_time(params, 11, 30)
        assert r1 == pytest.approx(0.25)
        assert r2 == pytest.approx(0.25)
        assert r3 == pytest.approx(0.25)

    def test_plateau_keeps_trough_flat(self):
        """With plateau=60min, rate at 00:30, 1:00, 1:30 should all equal trough."""
        from api.services.pricing_service import compute_rate_at_time

        params = {
            "surge_peak_rate": 0.25,
            "sale_trough_rate": 0.15,
            "plateau_minutes": 60,
            "partial_peak_factor": 1.0,
            "is_flat": False,
        }
        r1 = compute_rate_at_time(params, 0, 30)
        r2 = compute_rate_at_time(params, 1, 0)
        r3 = compute_rate_at_time(params, 1, 30)
        assert r1 == pytest.approx(-0.15)
        assert r2 == pytest.approx(-0.15)
        assert r3 == pytest.approx(-0.15)

    def test_partial_peak_factor(self):
        """partial_peak_factor=0.5 halves the effective peak and trough."""
        from api.services.pricing_service import compute_rate_at_time

        params = {
            "surge_peak_rate": 0.30,
            "sale_trough_rate": 0.20,
            "plateau_minutes": 0,
            "partial_peak_factor": 0.5,
            "is_flat": False,
        }
        # With factor=0.5, maximum surge = 0.30 * 0.5 = 0.15
        assert compute_rate_at_time(params, 11, 0) == pytest.approx(0.15)
        # Maximum sale = -0.20 * 0.5 = -0.10
        assert compute_rate_at_time(params, 1, 0) == pytest.approx(-0.10)

    def test_rate_between_8am_and_11am(self):
        """Rate rises from 0 at 8:00 toward peak at 11:00."""
        from api.services.pricing_service import compute_rate_at_time

        params = {
            "surge_peak_rate": 0.30,
            "sale_trough_rate": 0.20,
            "plateau_minutes": 0,
            "partial_peak_factor": 1.0,
            "is_flat": False,
        }
        # 9:30 = midpoint between 8:00 and 11:00 (180 min, 90 min in)
        r = compute_rate_at_time(params, 9, 30)
        assert 0 < r < 0.30

    def test_rate_between_11am_and_1830(self):
        """Rate falls from peak at 11:00 toward 0 at 18:30."""
        from api.services.pricing_service import compute_rate_at_time

        params = {
            "surge_peak_rate": 0.30,
            "sale_trough_rate": 0.20,
            "plateau_minutes": 0,
            "partial_peak_factor": 1.0,
            "is_flat": False,
        }
        # 14:45 = midpoint between 11:00 and 18:30
        r = compute_rate_at_time(params, 14, 45)
        assert 0 < r < 0.30

    def test_rate_between_1830_and_1am(self):
        """Rate goes negative (sale) from 18:30 toward trough at 1:00 next day."""
        from api.services.pricing_service import compute_rate_at_time

        params = {
            "surge_peak_rate": 0.30,
            "sale_trough_rate": 0.20,
            "plateau_minutes": 0,
            "partial_peak_factor": 1.0,
            "is_flat": False,
        }
        # 21:45 = midpoint between 18:30 and 1:00 (next day)
        r = compute_rate_at_time(params, 21, 45)
        assert r < 0  # Should be negative (sale)

    def test_rate_between_1am_and_8am(self):
        """Rate returns from trough at 1:00 toward 0 at 8:00."""
        from api.services.pricing_service import compute_rate_at_time

        params = {
            "surge_peak_rate": 0.30,
            "sale_trough_rate": 0.20,
            "plateau_minutes": 0,
            "partial_peak_factor": 1.0,
            "is_flat": False,
        }
        # 4:30 = midpoint between 1:00 and 8:00
        r = compute_rate_at_time(params, 4, 30)
        assert r < 0  # Still negative but moving toward zero

    def test_symmetric_linear_interpolation(self):
        """Linear interpolation should give expected values at midpoints."""
        from api.services.pricing_service import compute_rate_at_time

        params = {
            "surge_peak_rate": 0.30,
            "sale_trough_rate": 0.20,
            "plateau_minutes": 0,
            "partial_peak_factor": 1.0,
            "is_flat": False,
        }
        # Exactly halfway between 8:00 and 11:00 → 50% of peak = 0.15
        r = compute_rate_at_time(params, 9, 30)
        assert r == pytest.approx(0.15)


class TestPricingService:
    """Integration: daily pricing generation and persistence."""

    def test_pricing_generated_daily(self, client, group_ctx):
        """Daily pricing is lazily generated for rewards."""
        h = group_ctx["headers"]
        for i in range(6):
            client.post(
                "/api/rewards",
                json={"name": f"Reward {i}", "emoji": "🎁", "cost_points": 10 + i * 5},
                headers=h,
            )

        res = client.get("/api/rewards/pricing/today", headers=h)
        assert res.status_code == 200
        pricing = res.json()
        assert isinstance(pricing, dict)

    def test_pricing_rate_range(self, client, group_ctx):
        """Each pricing rate is within valid bounds."""
        h = group_ctx["headers"]
        for i in range(6):
            client.post(
                "/api/rewards",
                json={"name": f"Price {i}", "emoji": "🎁", "cost_points": 10 + i * 5},
                headers=h,
            )

        res = client.get("/api/rewards/pricing/today", headers=h)
        pricing = res.json()
        for info in pricing.values():
            rate = info["rate"]
            assert -0.25 <= rate <= 0.50, f"Rate {rate} out of bounds"

    def test_pricing_injected_in_rewards_list(self, client, group_ctx):
        """GET /api/rewards includes pricing_rate and surged_cost fields."""
        h = group_ctx["headers"]
        client.post(
            "/api/rewards",
            json={"name": "Priced Item", "emoji": "🎁", "cost_points": 30},
            headers=h,
        )

        res = client.get("/api/rewards", headers=h)
        assert res.status_code == 200
        rewards = res.json()
        for r in rewards:
            assert "pricing_rate" in r
            assert "surged_cost" in r
            if r["surged_cost"] is not None:
                expected = round(r["cost_points"] * (1 + r["pricing_rate"]))
                if expected < 1:
                    expected = 1
                assert r["surged_cost"] == expected, \
                    f"surged_cost={r['surged_cost']} != expected={expected} (rate={r['pricing_rate']})"

    def test_floor_ensures_at_least_one_non_flat(self, client, group_ctx):
        """当所有奖励随机到 is_flat 时，兜底强制至少一个参与波动。"""
        from unittest import mock
        from api.services import pricing_service

        h = group_ctx["headers"]
        for i in range(5):
            client.post(
                "/api/rewards",
                json={"name": f"F{i}", "emoji": "🎁", "cost_points": 10},
                headers=h,
            )

        # 强制 _random_params 始终返回 is_flat=True
        always_flat = {"is_flat": True}
        with mock.patch.object(pricing_service, "_random_params", return_value=always_flat):
            res = client.get("/api/rewards/pricing/today", headers=h)
        assert res.status_code == 200

        pricing = res.json()
        assert len(pricing) >= 1
        flat_count = sum(1 for v in pricing.values() if v["is_flat"])
        assert flat_count < len(pricing), \
            f"All {len(pricing)} rewards are flat; floor should have forced at least 1 non-flat"


class TestPricingRedeem:
    """Redeem with time-based pricing."""

    def test_pricing_affects_redeem_cost(self, client, group_ctx):
        """Redeeming a priced reward uses the adjusted cost."""
        h = group_ctx["headers"]
        reward_res = client.post(
            "/api/rewards",
            json={"name": "Priced Item", "emoji": "🎁", "cost_points": 30},
            headers=h,
        )
        reward_id = reward_res.json()["id"]

        # Earn enough points
        task_res = client.post(
            "/api/tasks",
            json={"name": "Earn Big", "emoji": "💰", "base_points": 200},
            headers=h,
        )
        client.post(
            "/api/tasks/complete",
            json={"task_id": task_res.json()["id"], "star_rating": 5},
            headers=h,
        )

        rewards = client.get("/api/rewards", headers=h).json()
        reward = next(r for r in rewards if r["id"] == reward_id)
        surged_cost = reward.get("surged_cost") or reward["cost_points"]

        redeem_res = client.post("/api/rewards/redeem", json={"reward_id": reward_id}, headers=h)
        assert redeem_res.status_code == 200
        data = redeem_res.json()
        assert data["spent_points"] == surged_cost


class TestPricingOverride:
    """Admin override management for time-based pricing."""

    def test_lock_in_forces_pricing(self, client, group_ctx, admin_token):
        """lock_in forces a reward to participate in pricing."""
        h = group_ctx["headers"]
        admin_h = admin_token
        gid = group_ctx["id"]

        for i in range(3):
            client.post(
                "/api/rewards",
                json={"name": f"Lock {i}", "emoji": "🎁", "cost_points": 10 + i * 5},
                headers=h,
            )
        rewards = client.get(f"/api/admin/rewards?group_id={gid}", headers=admin_h).json()
        target_id = rewards[0]["id"]

        resp = client.post(
            "/api/admin/pricing-overrides",
            json={"group_id": gid, "reward_id": target_id, "override_type": "lock_in"},
            headers=admin_h,
        )
        assert resp.status_code == 200

        pricing = client.get("/api/rewards/pricing/today", headers=h).json()
        assert str(target_id) in pricing

    def test_lock_out_excludes_reward(self, client, group_ctx, admin_token):
        """lock_out excludes a reward from pricing (is_flat=True)."""
        h = group_ctx["headers"]
        admin_h = admin_token
        gid = group_ctx["id"]

        for i in range(6):
            client.post(
                "/api/rewards",
                json={"name": f"Out {i}", "emoji": "🎁", "cost_points": 10 + i * 5},
                headers=h,
            )
        rewards = client.get(f"/api/admin/rewards?group_id={gid}", headers=admin_h).json()
        target_id = rewards[0]["id"]

        resp = client.post(
            "/api/admin/pricing-overrides",
            json={"group_id": gid, "reward_id": target_id, "override_type": "lock_out"},
            headers=admin_h,
        )
        assert resp.status_code == 200

        pricing = client.get("/api/rewards/pricing/today", headers=h).json()
        info = pricing.get(str(target_id))
        # lock_out means is_flat=True → rate=0 or not in pricing map at all
        if info is not None:
            assert info["rate"] == 0.0

    def test_manual_params_overrides_curve(self, client, group_ctx, admin_token):
        """manual_params sets exact pricing parameters."""
        h = group_ctx["headers"]
        admin_h = admin_token
        gid = group_ctx["id"]

        for i in range(3):
            client.post(
                "/api/rewards",
                json={"name": f"Manual {i}", "emoji": "🎁", "cost_points": 10 + i * 5},
                headers=h,
            )
        rewards = client.get(f"/api/admin/rewards?group_id={gid}", headers=admin_h).json()
        target_id = rewards[0]["id"]

        resp = client.post(
            "/api/admin/pricing-overrides",
            json={
                "group_id": gid,
                "reward_id": target_id,
                "override_type": "manual_params",
                "surge_peak_rate": 0.40,
                "sale_trough_rate": 0.25,
                "plateau_minutes": 120,
                "partial_peak_factor": 0.8,
            },
            headers=admin_h,
        )
        assert resp.status_code == 200

        # Verify override data
        overrides = client.get(f"/api/admin/pricing-overrides?group_id={gid}", headers=admin_h).json()
        found = next(o for o in overrides if o["reward_id"] == target_id)
        assert found["override_type"] == "manual_params"
        assert float(found["manual_surge_peak"]) == 0.40

    def test_override_with_duration(self, client, group_ctx, admin_token):
        """Override with duration_days sets expires_at."""
        h = group_ctx["headers"]
        admin_h = admin_token
        gid = group_ctx["id"]

        for i in range(3):
            client.post(
                "/api/rewards",
                json={"name": f"Dur {i}", "emoji": "🎁", "cost_points": 10 + i * 5},
                headers=h,
            )
        rewards = client.get(f"/api/admin/rewards?group_id={gid}", headers=admin_h).json()
        target_id = rewards[0]["id"]

        client.post(
            "/api/admin/pricing-overrides",
            json={
                "group_id": gid,
                "reward_id": target_id,
                "override_type": "lock_in",
                "duration_days": 7,
            },
            headers=admin_h,
        )

        overrides = client.get(f"/api/admin/pricing-overrides?group_id={gid}", headers=admin_h).json()
        found = next(o for o in overrides if o["reward_id"] == target_id)
        assert found["expires_at"] is not None

    def test_pricing_override_undo(self, client, group_ctx, admin_token):
        """Pricing override changes can be undone."""
        h = group_ctx["headers"]
        admin_h = admin_token
        gid = group_ctx["id"]

        for i in range(3):
            client.post(
                "/api/rewards",
                json={"name": f"Undo {i}", "emoji": "🎁", "cost_points": 10 + i * 5},
                headers=h,
            )
        rewards = client.get(f"/api/admin/rewards?group_id={gid}", headers=admin_h).json()
        target_id = rewards[0]["id"]

        client.post(
            "/api/admin/pricing-overrides",
            json={"group_id": gid, "reward_id": target_id, "override_type": "lock_in"},
            headers=admin_h,
        )

        overrides = client.get(f"/api/admin/pricing-overrides?group_id={gid}", headers=admin_h).json()
        assert any(o["reward_id"] == target_id for o in overrides)

        ops = client.get(f"/api/admin/operations?group_id={gid}", headers=admin_h).json()
        pricing_op = next(o for o in ops if o["operation_type"] == "pricing_override_change")
        undo_resp = client.post(f"/api/admin/undo/{pricing_op['id']}", headers=admin_h)
        assert undo_resp.status_code == 200

        overrides = client.get(f"/api/admin/pricing-overrides?group_id={gid}", headers=admin_h).json()
        assert not any(o["reward_id"] == target_id for o in overrides)
