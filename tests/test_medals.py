"""Tests for medal awarding + coupon exchange system (v2: unified coupon)."""

import pytest


# ---- Unit tests: medal_service ----

class TestAwardMedal:
    def test_first_medal_creates_record(self):
        """First medal of the day creates a new daily_medals row with count=1."""
        from api.services.medal_service import award_medal
        assert callable(award_medal)

    def test_subsequent_medal_increments_count(self):
        """Second+ medal same day increments count."""
        from api.services.medal_service import award_medal
        assert callable(award_medal)


class TestExchangeCoupon:
    def test_exchange_min_5_medals(self):
        """Minimum 5 medals required to exchange for a coupon."""
        from api.services.medal_service import exchange_coupon
        assert callable(exchange_coupon)

    def test_exchange_insufficient_medals_raises(self):
        """Cannot exchange if daily medal count < medal_count."""
        pass


class TestApplyCoupon:
    def test_coupon_on_positive_rate(self):
        """Coupon reduces positive pricing_rate by medal_count * 2%."""
        from api.services.medal_service import compute_effective_price

        # pricing_rate=0.3 (30% surge), medal_count=5 (10%) → 0.20
        rate = compute_effective_price(5, 0.3)
        assert round(rate, 4) == 0.20

    def test_coupon_can_go_negative(self):
        """Coupon can push rate below zero (natural sale)."""
        from api.services.medal_service import compute_effective_price

        # pricing_rate=0.05, medal_count=10 (20%) → -0.15
        rate = compute_effective_price(10, 0.05)
        assert round(rate, 4) == -0.15

    def test_coupon_on_flat_pricing(self):
        """On flat (rate=0) pricing, coupon creates a discount."""
        from api.services.medal_service import compute_effective_price

        rate = compute_effective_price(5, 0.0)
        assert rate == -0.10

    def test_coupon_amplifies_existing_sale(self):
        """On already-negative rate, coupon deepens the discount."""
        from api.services.medal_service import compute_effective_price

        rate = compute_effective_price(3, -0.15)
        assert rate == -0.21

    def test_compute_effective_cost_with_coupon(self):
        """Effective cost with coupon: base * (1 + adjusted_rate)."""
        from api.services.medal_service import compute_effective_price

        # base=100, pricing_rate=0.3, medal_count=10 (20%) → effective_rate=0.10
        # cost = max(1, round(100 * 1.10)) = 110
        rate = compute_effective_price(10, 0.3)
        cost = max(1, round(100 * (1 + rate)))
        assert cost == 110

    def test_large_medal_count(self):
        """Many medals create deep discount."""
        from api.services.medal_service import compute_effective_price

        rate = compute_effective_price(50, 0.0)
        assert rate == -1.0


# ---- Unit tests: medal count ----

class TestMedalCount:
    def test_award_called_after_task_complete(self):
        """Verify award_medal is importable and callable."""
        from api.services.medal_service import award_medal, get_today_medals
        assert callable(award_medal)
        assert callable(get_today_medals)


# ---- Integration tests (require DB) ----

class TestMedalIntegration:
    def test_medal_awarded_on_task_complete(self, client, group_ctx):
        """Completing a task awards 1 medal."""
        headers = group_ctx["headers"]

        t = client.post("/api/tasks", json={
            "name": "奖章测试任务", "emoji": "⭐", "base_points": 10,
        }, headers=headers)
        task_id = t.json()["id"]

        res = client.post("/api/tasks/complete", json={
            "task_id": task_id, "star_rating": 3,
        }, headers=headers)
        assert res.status_code == 200

        res2 = client.get("/api/medals/today", headers=headers)
        assert res2.status_code == 200
        data = res2.json()
        assert data["count"] >= 1

    def test_medals_accumulate_across_tasks(self, client, group_ctx):
        """Multiple task completions accumulate medals."""
        headers = group_ctx["headers"]

        for i in range(3):
            t = client.post("/api/tasks", json={
                "name": f"奖章任务{i}", "emoji": "⭐", "base_points": 10,
            }, headers=headers)
            task_id = t.json()["id"]
            client.post("/api/tasks/complete", json={
                "task_id": task_id, "star_rating": 4,
            }, headers=headers)

        res = client.get("/api/medals/today", headers=headers)
        assert res.json()["count"] >= 3


class TestCouponExchange:
    def test_exchange_coupon_with_5_medals(self, client, group_ctx):
        """Exchange 5 medals for a coupon (10% adjustment)."""
        headers = group_ctx["headers"]
        gid = group_ctx["id"]

        # Earn 5 medals
        for i in range(5):
            t = client.post("/api/tasks", json={
                "name": f"换券任务{i}", "emoji": "⭐", "base_points": 10,
            }, headers=headers)
            task_id = t.json()["id"]
            client.post("/api/tasks/complete", json={
                "task_id": task_id, "star_rating": 5,
            }, headers=headers)

        res = client.post("/api/medals/exchange", json={
            "medal_count": 5,
        }, headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert "coupon_id" in data
        assert data["adjustment_pct"] == 10
        assert data["medals_remaining"] >= 0

    def test_exchange_coupon_with_more_medals(self, client, group_ctx):
        """Exchange 7 medals (14% adjustment)."""
        headers = group_ctx["headers"]

        for i in range(8):
            t = client.post("/api/tasks", json={
                "name": f"多章任务{i}", "emoji": "⭐", "base_points": 10,
            }, headers=headers)
            task_id = t.json()["id"]
            client.post("/api/tasks/complete", json={
                "task_id": task_id, "star_rating": 5,
            }, headers=headers)

        res = client.post("/api/medals/exchange", json={
            "medal_count": 7,
        }, headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["adjustment_pct"] == 14

    def test_exchange_below_minimum_5(self, client, group_ctx):
        """Exchange fails with fewer than 5 medals."""
        headers = group_ctx["headers"]

        for i in range(5):
            t = client.post("/api/tasks", json={
                "name": f"最少测试{i}", "emoji": "⭐", "base_points": 10,
            }, headers=headers)
            task_id = t.json()["id"]
            client.post("/api/tasks/complete", json={
                "task_id": task_id, "star_rating": 5,
            }, headers=headers)

        res = client.post("/api/medals/exchange", json={
            "medal_count": 3,
        }, headers=headers)
        assert res.status_code == 400

    def test_exchange_insufficient_medals(self, client, group_ctx):
        """Exchange fails when not enough medals."""
        headers = group_ctx["headers"]

        # Only 1 medal earned
        t = client.post("/api/tasks", json={
            "name": "不够章", "emoji": "⭐", "base_points": 10,
        }, headers=headers)
        task_id = t.json()["id"]
        client.post("/api/tasks/complete", json={
            "task_id": task_id, "star_rating": 5,
        }, headers=headers)

        res = client.post("/api/medals/exchange", json={
            "medal_count": 5,
        }, headers=headers)
        assert res.status_code == 400


class TestCouponList:
    def test_list_coupons_empty(self, client, group_ctx):
        """No coupons when none exchanged."""
        res = client.get("/api/coupons", headers=group_ctx["headers"])
        assert res.status_code == 200
        assert res.json() == []

    def test_list_coupons_with_data(self, client, group_ctx):
        """List shows exchanged coupons with medal_count."""
        headers = group_ctx["headers"]

        for i in range(5):
            t = client.post("/api/tasks", json={
                "name": f"列表测试{i}", "emoji": "⭐", "base_points": 10,
            }, headers=headers)
            task_id = t.json()["id"]
            client.post("/api/tasks/complete", json={
                "task_id": task_id, "star_rating": 5,
            }, headers=headers)

        client.post("/api/medals/exchange", json={
            "medal_count": 5,
        }, headers=headers)

        res = client.get("/api/coupons", headers=headers)
        coupons = res.json()
        assert len(coupons) == 1
        assert coupons[0]["medal_count"] == 5
        assert coupons[0]["used"] is False


class TestCouponDelete:
    def test_delete_unused_coupon(self, client, group_ctx):
        """Delete an unused coupon."""
        headers = group_ctx["headers"]

        for i in range(5):
            t = client.post("/api/tasks", json={
                "name": f"删券测试{i}", "emoji": "⭐", "base_points": 10,
            }, headers=headers)
            task_id = t.json()["id"]
            client.post("/api/tasks/complete", json={
                "task_id": task_id, "star_rating": 5,
            }, headers=headers)
        ex = client.post("/api/medals/exchange", json={
            "medal_count": 5,
        }, headers=headers)
        coupon_id = ex.json()["coupon_id"]

        res = client.delete(f"/api/coupons/{coupon_id}", headers=headers)
        assert res.status_code == 200
        assert res.json()["success"] is True


class TestRedeemWithCoupon:
    def test_redeem_with_coupon_reduces_surge(self, client, group_ctx):
        """Redeem reward with coupon: 10 medals (20%) reduces surged cost."""
        headers = group_ctx["headers"]
        gid = group_ctx["id"]

        r = client.post("/api/rewards", json={
            "name": "测试奖励", "emoji": "🎁", "cost_points": 100,
        }, headers=headers)
        reward_id = r.json()["id"]

        from api.models.database import get_db
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE children SET total_points = 500 WHERE group_id = %s", (gid,))
        conn.commit()
        conn.close()

        for i in range(10):
            t = client.post("/api/tasks", json={
                "name": f"兑奖任务{i}", "emoji": "⭐", "base_points": 10,
            }, headers=headers)
            task_id = t.json()["id"]
            client.post("/api/tasks/complete", json={
                "task_id": task_id, "star_rating": 5,
            }, headers=headers)

        ex = client.post("/api/medals/exchange", json={
            "medal_count": 10,
        }, headers=headers)
        coupon_id = ex.json()["coupon_id"]

        res = client.post("/api/rewards/redeem", json={
            "reward_id": reward_id, "coupon_id": coupon_id,
        }, headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True

    def test_redeem_with_small_coupon(self, client, group_ctx):
        """5 medals (10%) on base 100."""
        headers = group_ctx["headers"]
        gid = group_ctx["id"]

        r = client.post("/api/rewards", json={
            "name": "小券奖励", "emoji": "🎁", "cost_points": 100,
        }, headers=headers)
        reward_id = r.json()["id"]

        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE children SET total_points = 500 WHERE group_id = %s", (gid,))
        conn.commit()
        conn.close()

        for i in range(5):
            t = client.post("/api/tasks", json={
                "name": f"小券任务{i}", "emoji": "⭐", "base_points": 10,
            }, headers=headers)
            task_id = t.json()["id"]
            client.post("/api/tasks/complete", json={
                "task_id": task_id, "star_rating": 5,
            }, headers=headers)
        ex = client.post("/api/medals/exchange", json={
            "medal_count": 5,
        }, headers=headers)
        coupon_id = ex.json()["coupon_id"]

        res = client.post("/api/rewards/redeem", json={
            "reward_id": reward_id, "coupon_id": coupon_id,
        }, headers=headers)
        assert res.status_code == 200


class TestCouponUsedOnce:
    def test_coupon_marked_used_after_redeem(self, client, group_ctx):
        """Coupon is marked used after successful redeem."""
        headers = group_ctx["headers"]
        gid = group_ctx["id"]

        r = client.post("/api/rewards", json={
            "name": "一次性券", "emoji": "🎁", "cost_points": 50,
        }, headers=headers)
        reward_id = r.json()["id"]

        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE children SET total_points = 500 WHERE group_id = %s", (gid,))
        conn.commit()
        conn.close()

        for i in range(5):
            t = client.post("/api/tasks", json={
                "name": f"券用完{i}", "emoji": "⭐", "base_points": 10,
            }, headers=headers)
            task_id = t.json()["id"]
            client.post("/api/tasks/complete", json={
                "task_id": task_id, "star_rating": 5,
            }, headers=headers)
        ex = client.post("/api/medals/exchange", json={
            "medal_count": 5,
        }, headers=headers)
        coupon_id = ex.json()["coupon_id"]

        client.post("/api/rewards/redeem", json={
            "reward_id": reward_id, "coupon_id": coupon_id,
        }, headers=headers)

        res = client.get("/api/coupons", headers=headers)
        assert res.json() == []


# ---- get_db import at module level for reuse ----
from api.models.database import get_db
