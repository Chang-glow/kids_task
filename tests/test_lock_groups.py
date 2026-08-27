"""Tests for key-task grouping: 同组任选其一，每组均需有完成的任务才解锁；lock_group 为 NULL 的钥匙任务不参与锁。"""


class TestKeyGrouping:
    def make_task(self, client, gid, admin_token, name, emoji="📖", points=10):
        t = client.post(f"/api/admin/groups/{gid}/tasks", json={
            "name": name, "emoji": emoji, "base_points": points,
        }, headers=admin_token)
        assert t.status_code == 200
        return t.json()["id"]

    def make_reward(self, client, gid, admin_token, name, cost=1):
        r = client.post(f"/api/admin/groups/{gid}/rewards", json={
            "name": name, "emoji": "🎁", "cost_points": cost,
        }, headers=admin_token)
        assert r.status_code == 200
        return r.json()["id"]

    def add_lock(self, client, gid, admin_token, reward_id, task_id, lock_group=None):
        body = {"reward_id": reward_id, "task_id": task_id, "group_id": gid}
        if lock_group is not None:
            body["lock_group"] = lock_group
        res = client.post("/api/admin/reward-locks", json=body, headers=admin_token)
        assert res.status_code == 200
        return res.json()

    def complete_task(self, client, group_ctx, task_id, star=5):
        res = client.post("/api/tasks/complete", json={
            "task_id": task_id, "star_rating": star,
        }, headers=group_ctx["headers"])
        assert res.status_code == 200

    def redeem(self, client, group_ctx, reward_id):
        return client.post("/api/rewards/redeem", json={
            "reward_id": reward_id,
        }, headers=group_ctx["headers"])

    def test_group_any_one_completed_unlocks(self, client, group_ctx, admin_token):
        """同组两个钥匙，完成其一 → 解锁。"""
        gid = group_ctx["id"]
        tid1 = self.make_task(client, gid, admin_token, "阅读A")
        tid2 = self.make_task(client, gid, admin_token, "阅读B")
        rid = self.make_reward(client, gid, admin_token, "电视")
        self.add_lock(client, gid, admin_token, rid, tid1, lock_group=1)
        self.add_lock(client, gid, admin_token, rid, tid2, lock_group=1)

        self.complete_task(client, group_ctx, tid1)
        # 给够积分，排除积分不足干扰
        child_id = group_ctx["children"][0]["id"]
        client.post("/api/admin/points", json={
            "child_id": child_id, "group_id": gid,
            "mode": "set", "value": 100,
        }, headers=admin_token)

        res = self.redeem(client, group_ctx, rid)
        assert res.status_code == 200
        assert res.json()["success"] is True

    def test_group_none_completed_locked(self, client, group_ctx, admin_token):
        """同组钥匙一个都没完成 → 锁定。"""
        gid = group_ctx["id"]
        tid1 = self.make_task(client, gid, admin_token, "阅读A")
        tid2 = self.make_task(client, gid, admin_token, "阅读B")
        rid = self.make_reward(client, gid, admin_token, "电视")
        self.add_lock(client, gid, admin_token, rid, tid1, lock_group=1)
        self.add_lock(client, gid, admin_token, rid, tid2, lock_group=1)

        res = self.redeem(client, group_ctx, rid)
        assert res.status_code == 403

    def test_two_groups_each_need_one(self, client, group_ctx, admin_token):
        """组A(t1,t2) + 组B(t3,t4)：只满足组A → 锁定；两组都满足 → 解锁。"""
        gid = group_ctx["id"]
        t1 = self.make_task(client, gid, admin_token, "A1")
        t2 = self.make_task(client, gid, admin_token, "A2")
        t3 = self.make_task(client, gid, admin_token, "B1")
        t4 = self.make_task(client, gid, admin_token, "B2")
        rid = self.make_reward(client, gid, admin_token, "电视")
        self.add_lock(client, gid, admin_token, rid, t1, lock_group=1)
        self.add_lock(client, gid, admin_token, rid, t2, lock_group=1)
        self.add_lock(client, gid, admin_token, rid, t3, lock_group=2)
        self.add_lock(client, gid, admin_token, rid, t4, lock_group=2)

        self.complete_task(client, group_ctx, t1)  # 满足组1
        res = self.redeem(client, group_ctx, rid)
        assert res.status_code == 403

        self.complete_task(client, group_ctx, t3)  # 满足组2
        res = self.redeem(client, group_ctx, rid)
        assert res.status_code == 200

    def test_ungrouped_tasks_never_lock(self, client, group_ctx, admin_token):
        """两个未分组钥匙（lock_group=None）→ 不参与锁，奖励始终可兑换。"""
        gid = group_ctx["id"]
        tid1 = self.make_task(client, gid, admin_token, "阅读")
        tid2 = self.make_task(client, gid, admin_token, "运动")
        rid = self.make_reward(client, gid, admin_token, "电视")
        self.add_lock(client, gid, admin_token, rid, tid1)
        self.add_lock(client, gid, admin_token, rid, tid2)

        # 两个都没做 → 仍可兑换（未分组钥匙不锁）
        # 给够积分，排除积分不足干扰
        child_id = group_ctx["children"][0]["id"]
        client.post("/api/admin/points", json={
            "child_id": child_id, "group_id": gid,
            "mode": "set", "value": 100,
        }, headers=admin_token)

        res = self.redeem(client, group_ctx, rid)
        assert res.status_code == 200
        assert res.json()["success"] is True

        # 做了也无关（本来就不在锁条件里）
        self.complete_task(client, group_ctx, tid1)
        res = self.redeem(client, group_ctx, rid)
        assert res.status_code == 200

    def test_mixed_group_and_ungrouped(self, client, group_ctx, admin_token):
        """组(t1,t2) + 未分组(t3)：未分组钥匙不参与锁判定。"""
        gid = group_ctx["id"]
        t1 = self.make_task(client, gid, admin_token, "组1A")
        t2 = self.make_task(client, gid, admin_token, "组1B")
        t3 = self.make_task(client, gid, admin_token, "单独任务")
        rid = self.make_reward(client, gid, admin_token, "电视")
        self.add_lock(client, gid, admin_token, rid, t1, lock_group=1)
        self.add_lock(client, gid, admin_token, rid, t2, lock_group=1)
        self.add_lock(client, gid, admin_token, rid, t3)

        # 只做未分组钥匙、组未满足 → 锁定，且提示不含未分组任务
        self.complete_task(client, group_ctx, t3)
        res = self.redeem(client, group_ctx, rid)
        assert res.status_code == 403
        assert "单独任务" not in res.json()["detail"]

        # 再满足组（完成 t1）→ 解锁（未分组钥匙无关紧要）
        self.complete_task(client, group_ctx, t1)
        res = self.redeem(client, group_ctx, rid)
        assert res.status_code == 200

    def test_add_lock_persists_group(self, client, group_ctx, admin_token):
        """add_reward_lock 持久化 lock_group，列表返回该值。"""
        gid = group_ctx["id"]
        tid = self.make_task(client, gid, admin_token, "钥匙")
        rid = self.make_reward(client, gid, admin_token, "奖励")
        res = self.add_lock(client, gid, admin_token, rid, tid, lock_group=3)
        assert res["lock_group"] == 3

        lst = client.get(f"/api/admin/reward-locks?group_id={gid}", headers=admin_token).json()
        assert any(lk["task_id"] == tid and lk["lock_group"] == 3 for lk in lst)

    def test_only_ungrouped_locks_never_block(self, client, group_ctx, admin_token):
        """奖励仅有未分组钥匙 → 任何情况下都可兑换。"""
        gid = group_ctx["id"]
        tid = self.make_task(client, gid, admin_token, "杂活")
        rid = self.make_reward(client, gid, admin_token, "冰淇淋")
        self.add_lock(client, gid, admin_token, rid, tid)

        # 给够积分，排除积分不足干扰
        child_id = group_ctx["children"][0]["id"]
        client.post("/api/admin/points", json={
            "child_id": child_id, "group_id": gid,
            "mode": "set", "value": 100,
        }, headers=admin_token)

        res = self.redeem(client, group_ctx, rid)
        assert res.status_code == 200
        assert res.json()["success"] is True
