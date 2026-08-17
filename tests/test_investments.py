"""投资系统测试：投资章 → 解锁券 → 绕过奖励锁。"""
import pytest


class TestInvestmentMedals:
    """投资章发放（通过 hook 在任务完成时触发）。"""

    def test_medal_awarded_on_task_complete(self, client, group_ctx):
        """完成任务 → 自动获得 1 枚投资章。"""
        child_id = group_ctx["children"][0]["id"]
        h = group_ctx["headers"]
        res = client.post("/api/tasks", json={"name": "任务A", "emoji": "📝", "base_points": 10}, headers=h)
        task_id = res.json()["id"]

        client.post(f"/api/tasks/complete", json={
            "task_id": task_id, "star_rating": 5
        }, headers=h)

        r = client.get(f"/api/investments/medals?child_id={child_id}", headers=h)
        assert r.status_code == 200
        assert r.json()["medals_today"] == 1

    def test_same_task_no_duplicate(self, client, group_ctx):
        """同一任务当天重复完成不重复给章。"""
        child_id = group_ctx["children"][0]["id"]
        h = group_ctx["headers"]
        res = client.post("/api/tasks", json={
            "name": "任务A", "emoji": "📝", "base_points": 10, "is_repeatable": True
        }, headers=h)
        task_id = res.json()["id"]

        client.post(f"/api/tasks/complete", json={
            "task_id": task_id, "star_rating": 5
        }, headers=h)
        client.post(f"/api/tasks/complete", json={
            "task_id": task_id, "star_rating": 4
        }, headers=h)

        r = client.get(f"/api/investments/medals?child_id={child_id}", headers=h)
        assert r.json()["medals_today"] == 1  # 不是 2

    def test_different_tasks_separate_medals(self, client, group_ctx):
        """完成 5 种不同任务 → 得 5 枚投资章。"""
        child_id = group_ctx["children"][0]["id"]
        h = group_ctx["headers"]
        task_ids = []
        for i in range(5):
            res = client.post("/api/tasks", json={
                "name": f"任务{chr(65+i)}", "emoji": "📝", "base_points": 10
            }, headers=h)
            task_ids.append(res.json()["id"])

        for tid in task_ids:
            client.post(f"/api/tasks/complete", json={
                "task_id": tid, "star_rating": 5
            }, headers=h)

        r = client.get(f"/api/investments/medals?child_id={child_id}", headers=h)
        assert r.json()["medals_today"] == 5


class TestExchangeCoupon:
    """投资章 → 解锁券兑换。"""

    @pytest.fixture
    def five_medals(self, client, group_ctx):
        """准备 5 枚投资章（5 种不同任务）。"""
        child_id = group_ctx["children"][0]["id"]
        h = group_ctx["headers"]
        task_ids = []
        for i in range(5):
            res = client.post("/api/tasks", json={
                "name": f"任务{chr(65+i)}", "emoji": "📝", "base_points": 10
            }, headers=h)
            task_ids.append(res.json()["id"])
        for tid in task_ids:
            client.post(f"/api/tasks/complete", json={
                "task_id": tid, "star_rating": 5
            }, headers=h)
        return child_id, h

    def test_exchange_5_medals(self, client, group_ctx, five_medals):
        """5 枚章兑 1 张解锁券，额外支付 50%。"""
        child_id, h = five_medals
        r = client.post("/api/investments/exchange", json={
            "child_id": child_id, "medal_count": 5
        }, headers=h)
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        assert data["coupon_id"] > 0
        assert data["medal_count"] == 5
        assert data["unlock_extra_pct"] == 50
        assert data["medals_remaining"] == 0

    def test_exchange_8_medals(self, client, group_ctx, five_medals):
        """8 枚章兑解锁券，额外支付 20%。"""
        child_id, h = five_medals
        # 再创建 3 种任务
        task_ids = []
        for i in range(3):
            res = client.post("/api/tasks", json={
                "name": f"任务2{chr(65+i)}", "emoji": "📝", "base_points": 10
            }, headers=h)
            task_ids.append(res.json()["id"])
        for tid in task_ids:
            client.post(f"/api/tasks/complete", json={
                "task_id": tid, "star_rating": 5
            }, headers=h)

        r = client.post("/api/investments/exchange", json={
            "child_id": child_id, "medal_count": 8
        }, headers=h)
        assert r.status_code == 200
        data = r.json()
        assert data["unlock_extra_pct"] == 20

    def test_exchange_10_medals_zero_extra(self, client, group_ctx, five_medals):
        """10 枚章兑解锁券，额外支付 0%（完全解锁）。"""
        child_id, h = five_medals
        task_ids = []
        for i in range(5):
            res = client.post("/api/tasks", json={
                "name": f"任务2{chr(65+i)}", "emoji": "📝", "base_points": 10
            }, headers=h)
            task_ids.append(res.json()["id"])
        for tid in task_ids:
            client.post(f"/api/tasks/complete", json={
                "task_id": tid, "star_rating": 5
            }, headers=h)

        r = client.post("/api/investments/exchange", json={
            "child_id": child_id, "medal_count": 10
        }, headers=h)
        assert r.status_code == 200
        data = r.json()
        assert data["unlock_extra_pct"] == 0

    def test_exchange_any_count_above_5(self, client, group_ctx, five_medals):
        """≥5 任意数量均可兑换（不再限制 5 的倍数）。"""
        child_id, h = five_medals
        # 再创建 2 种 → 共 7 章
        for i in range(2):
            res = client.post("/api/tasks", json={
                "name": f"任务2{chr(65+i)}", "emoji": "📝", "base_points": 10
            }, headers=h)
            client.post(f"/api/tasks/complete", json={
                "task_id": res.json()["id"], "star_rating": 5
            }, headers=h)

        r = client.post("/api/investments/exchange", json={
            "child_id": child_id, "medal_count": 7
        }, headers=h)
        assert r.status_code == 200
        assert r.json()["medal_count"] == 7

    def test_exchange_below_5(self, client, group_ctx, five_medals):
        """不足 5 枚不能兑换。"""
        child_id, h = five_medals
        r = client.post("/api/investments/exchange", json={
            "child_id": child_id, "medal_count": 3
        }, headers=h)
        assert r.status_code == 400

    def test_exchange_insufficient_medals(self, client, group_ctx, five_medals):
        """章不足时拒绝。"""
        child_id, h = five_medals
        r = client.post("/api/investments/exchange", json={
            "child_id": child_id, "medal_count": 15
        }, headers=h)
        assert r.status_code == 400


class TestUseUnlockCoupon:
    """使用解锁券：标记已用。"""

    @pytest.fixture
    def ready_coupon(self, client, group_ctx):
        """准备 1 张解锁券。"""
        child_id = group_ctx["children"][0]["id"]
        h = group_ctx["headers"]
        task_ids = []
        for i in range(5):
            res = client.post("/api/tasks", json={
                "name": f"任务{chr(65+i)}", "emoji": "📝", "base_points": 10
            }, headers=h)
            task_ids.append(res.json()["id"])
        for tid in task_ids:
            client.post(f"/api/tasks/complete", json={
                "task_id": tid, "star_rating": 5
            }, headers=h)
        r = client.post("/api/investments/exchange", json={
            "child_id": child_id, "medal_count": 5
        }, headers=h)
        coupon_id = r.json()["coupon_id"]
        return child_id, coupon_id, h

    def test_use_unlock_coupon_success(self, client, group_ctx, ready_coupon):
        """使用解锁券标记成功。"""
        child_id, coupon_id, h = ready_coupon
        r = client.post("/api/investments/use", json={
            "child_id": child_id, "coupon_id": coupon_id
        }, headers=h)
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        assert data["unlock_extra_pct"] == 50

    def test_coupon_cannot_use_twice(self, client, group_ctx, ready_coupon):
        """解锁券只能用一次。"""
        child_id, coupon_id, h = ready_coupon
        client.post("/api/investments/use", json={
            "child_id": child_id, "coupon_id": coupon_id
        }, headers=h)
        r = client.post("/api/investments/use", json={
            "child_id": child_id, "coupon_id": coupon_id
        }, headers=h)
        assert r.status_code == 400

    def test_use_nonexistent_coupon(self, client, group_ctx, ready_coupon):
        """使用不存在的解锁券。"""
        child_id, _, h = ready_coupon
        r = client.post("/api/investments/use", json={
            "child_id": child_id, "coupon_id": 99999
        }, headers=h)
        assert r.status_code == 400


class TestUnlockCouponRedeem:
    """解锁券配合奖励兑换。"""

    @pytest.fixture
    def locked_reward_with_coupon(self, client, group_ctx):
        """准备：锁定奖励 + 解锁券 + 足够积分。"""
        child_id = group_ctx["children"][0]["id"]
        h = group_ctx["headers"]

        # 创建钥匙任务
        tr = client.post("/api/tasks", json={
            "name": "钥匙任务", "emoji": "🔑", "base_points": 50
        }, headers=h)
        key_task_id = tr.json()["id"]

        # 创建奖励
        rr = client.post("/api/rewards", json={
            "name": "锁定奖励", "emoji": "🎁", "cost_points": 50
        }, headers=h)
        reward_id = rr.json()["id"]

        # 添加锁
        from api.models.database import get_db
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO reward_locks (reward_id, task_id, group_id) VALUES (%s, %s, %s)",
            (reward_id, key_task_id, group_ctx["id"]),
        )
        conn.commit()
        conn.close()

        # 攒积分：完成 5 种任务（包含钥匙任务）
        for i in range(5):
            res = client.post("/api/tasks", json={
                "name": f"积分任务{chr(65+i)}", "emoji": "📝", "base_points": 50
            }, headers=h)
            client.post(f"/api/tasks/complete", json={
                "task_id": res.json()["id"], "star_rating": 5
            }, headers=h)

        # 兑换解锁券
        ex = client.post("/api/investments/exchange", json={
            "child_id": child_id, "medal_count": 5
        }, headers=h)
        coupon_id = ex.json()["coupon_id"]

        return child_id, reward_id, coupon_id, h

    def test_redeem_locked_without_coupon_403(self, client, group_ctx, locked_reward_with_coupon):
        """锁定奖励无解锁券 → 403。"""
        _, reward_id, _, h = locked_reward_with_coupon
        r = client.post("/api/rewards/redeem", json={
            "reward_id": reward_id
        }, headers=h)
        assert r.status_code == 403

    def test_redeem_locked_with_coupon_success(self, client, group_ctx, locked_reward_with_coupon):
        """锁定奖励 + 解锁券 → 兑换成功，含额外支付。"""
        child_id, reward_id, coupon_id, h = locked_reward_with_coupon

        cr = client.get(f"/api/groups/{group_ctx['invite_code']}", headers=h)
        before = cr.json()["children"][0]["total_points"]

        r = client.post("/api/rewards/redeem", json={
            "reward_id": reward_id, "investment_coupon_id": coupon_id
        }, headers=h)
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True

        cr2 = client.get(f"/api/groups/{group_ctx['invite_code']}", headers=h)
        after = cr2.json()["children"][0]["total_points"]
        spent = before - after
        # 基础价格 50 分 + 50% 解锁额外 + 可能时段定价
        assert spent >= 50, f"should spend at least 50 base, spent {spent}"
        assert spent == data["spent_points"], f"balance delta {spent} should match api {data['spent_points']}"

    def test_unlock_coupon_used_after_redeem(self, client, group_ctx, locked_reward_with_coupon):
        """兑换后解锁券标记为已使用。"""
        child_id, reward_id, coupon_id, h = locked_reward_with_coupon

        client.post("/api/rewards/redeem", json={
            "reward_id": reward_id, "investment_coupon_id": coupon_id
        }, headers=h)

        # 再次使用同一张券应失败
        r = client.post("/api/investments/use", json={
            "child_id": child_id, "coupon_id": coupon_id
        }, headers=h)
        assert r.status_code == 400


class TestCouponList:
    """解锁券列表。"""

    def test_list_empty(self, client, group_ctx):
        """还没有兑换时列表为空。"""
        child_id = group_ctx["children"][0]["id"]
        h = group_ctx["headers"]
        r = client.get(f"/api/investments/coupons?child_id={child_id}", headers=h)
        assert r.status_code == 200
        assert r.json()["coupons"] == []

    def test_list_after_exchange(self, client, group_ctx):
        """兑换后出现在列表中。"""
        child_id = group_ctx["children"][0]["id"]
        h = group_ctx["headers"]
        for i in range(5):
            res = client.post("/api/tasks", json={
                "name": f"任务{chr(65+i)}", "emoji": "📝", "base_points": 10
            }, headers=h)
            client.post(f"/api/tasks/complete", json={
                "task_id": res.json()["id"], "star_rating": 5
            }, headers=h)
        client.post("/api/investments/exchange", json={
            "child_id": child_id, "medal_count": 5
        }, headers=h)
        r = client.get(f"/api/investments/coupons?child_id={child_id}", headers=h)
        assert len(r.json()["coupons"]) == 1


class TestBackwardCompat:
    """存量投资兼容：process_daily_payouts + active investments 依然可用。"""

    def test_active_investments_empty(self, client, group_ctx):
        """无存量投资时返回空列表。"""
        child_id = group_ctx["children"][0]["id"]
        h = group_ctx["headers"]
        r = client.get(f"/api/investments/active?child_id={child_id}", headers=h)
        assert r.status_code == 200
        assert r.json()["investments"] == []

    def test_daily_payout_runs(self, client, group_ctx):
        """payout cron 端点仍可调用（处理存量投资）。"""
        import os
        os.environ["CRON_SECRET"] = "testsecret"
        r = client.get("/api/cron/investment-payout?secret=testsecret")
        assert r.status_code == 200
        assert "investments_processed" in r.json()


class TestAdminStats:
    """Admin 投资统计。"""

    def test_stats_includes_investment_medals(self, client, group_ctx):
        """stats 返回投资章数。"""
        h = group_ctx["headers"]
        r = client.get("/api/investments/stats", headers=h)
        assert r.status_code == 200
        assert "children" in r.json()
        child = r.json()["children"][0]
        assert "investment_medals_today" in child
        assert "investment_coupons_unused" in child
        assert "active_investments" in child


class TestDatabaseTables:
    """验证新表正确创建。"""

    def test_tables_exist(self, app):
        """三张新表在 init_db 后存在。"""
        from api.models.database import get_db
        conn = get_db()
        cur = conn.cursor()
        tables = ["investment_medals", "investment_coupons", "investments"]
        for t in tables:
            cur.execute(
                "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = %s)",
                (t,),
            )
            assert cur.fetchone()["exists"] is True, f"Table {t} should exist"
        conn.close()


class TestAdminUnlockCoupon:
    """后台发放解锁券 → 孩子正常消耗 + 后台删除。"""

    def _locked_reward(self, client, group_ctx, admin_token):
        """准备一个被钥匙锁定的奖励，返回 reward_id。"""
        h = group_ctx["headers"]
        kt = client.post("/api/tasks", json={
            "name": "钥匙", "emoji": "🔑", "base_points": 50,
        }, headers=h).json()
        rr = client.post("/api/rewards", json={
            "name": "奖励", "emoji": "🎁", "cost_points": 50,
        }, headers=h).json()
        client.post("/api/admin/reward-locks", json={
            "reward_id": rr["id"], "task_id": kt["id"], "group_id": group_ctx["id"],
        }, headers=admin_token)
        return rr["id"]

    def _issue(self, client, admin_token, gid, child_id, medal_count=10):
        return client.post("/api/admin/investment-coupons/issue", json={
            "child_id": child_id, "medal_count": medal_count, "group_id": gid,
        }, headers=admin_token)

    def _give_points(self, client, group_ctx, admin_token, child_id, points=100):
        return client.post("/api/admin/points", json={
            "child_id": child_id, "group_id": group_ctx["id"],
            "mode": "set", "value": points,
        }, headers=admin_token)

    def test_admin_issued_coupon_redeemable(self, client, group_ctx, admin_token):
        """后台发放给首孩子的解锁券可被正常消耗。"""
        h = group_ctx["headers"]
        child_id = group_ctx["children"][0]["id"]
        reward_id = self._locked_reward(client, group_ctx, admin_token)

        res = self._issue(client, admin_token, group_ctx["id"], child_id, 10)
        assert res.status_code == 200, res.text
        coupon_id = res.json()["coupon_id"]

        self._give_points(client, group_ctx, admin_token, child_id, 100)

        r = client.post("/api/rewards/redeem", json={
            "reward_id": reward_id, "investment_coupon_id": coupon_id,
        }, headers=h)
        assert r.status_code == 200, r.text

    def test_redeem_with_explicit_child_id(self, client, group_ctx, admin_token):
        """后台发给第二个孩子的券，显式传 child_id 后可消耗。"""
        h = group_ctx["headers"]
        c2 = client.post("/api/children", json={"name": "老二", "emoji": "👧"}, headers=h).json()
        reward_id = self._locked_reward(client, group_ctx, admin_token)

        res = self._issue(client, admin_token, group_ctx["id"], c2["id"], 10)
        assert res.status_code == 200, res.text
        coupon_id = res.json()["coupon_id"]

        self._give_points(client, group_ctx, admin_token, c2["id"], 100)

        r = client.post("/api/rewards/redeem", json={
            "reward_id": reward_id, "investment_coupon_id": coupon_id, "child_id": c2["id"],
        }, headers=h)
        assert r.status_code == 200, r.text

    def test_redeem_wrong_child_id_rejected(self, client, group_ctx, admin_token):
        """券属于首孩子，用第二个孩子的 child_id 兑换被拒（IDOR 防护）。"""
        h = group_ctx["headers"]
        child1 = group_ctx["children"][0]["id"]
        c2 = client.post("/api/children", json={"name": "老二", "emoji": "👧"}, headers=h).json()
        reward_id = self._locked_reward(client, group_ctx, admin_token)

        res = self._issue(client, admin_token, group_ctx["id"], child1, 10)
        coupon_id = res.json()["coupon_id"]

        r = client.post("/api/rewards/redeem", json={
            "reward_id": reward_id, "investment_coupon_id": coupon_id, "child_id": c2["id"],
        }, headers=h)
        assert r.status_code == 400, r.text

    def test_admin_delete_unlock_coupon(self, client, group_ctx, admin_token):
        """后台可删除未使用的解锁券。"""
        child_id = group_ctx["children"][0]["id"]
        res = self._issue(client, admin_token, group_ctx["id"], child_id, 10)
        coupon_id = res.json()["coupon_id"]

        before = client.get(
            f"/api/admin/investment-coupons?group_id={group_ctx['id']}", headers=admin_token,
        ).json()
        assert any(c["id"] == coupon_id for c in before)

        d = client.delete(
            f"/api/admin/investment-coupons/{coupon_id}?group_id={group_ctx['id']}",
            headers=admin_token,
        )
        assert d.status_code == 200, d.text

        after = client.get(
            f"/api/admin/investment-coupons?group_id={group_ctx['id']}", headers=admin_token,
        ).json()
        assert all(c["id"] != coupon_id for c in after)

    def test_admin_delete_used_coupon_noop(self, client, group_ctx, admin_token):
        """已使用的解锁券不可删除（保留审计）。"""
        h = group_ctx["headers"]
        child_id = group_ctx["children"][0]["id"]
        res = self._issue(client, admin_token, group_ctx["id"], child_id, 10)
        coupon_id = res.json()["coupon_id"]

        client.post("/api/investments/use", json={
            "child_id": child_id, "coupon_id": coupon_id,
        }, headers=h)

        d = client.delete(
            f"/api/admin/investment-coupons/{coupon_id}?group_id={group_ctx['id']}",
            headers=admin_token,
        )
        assert d.status_code == 200, d.text

        after = client.get(
            f"/api/admin/investment-coupons?group_id={group_ctx['id']}", headers=admin_token,
        ).json()
        assert any(c["id"] == coupon_id for c in after)
