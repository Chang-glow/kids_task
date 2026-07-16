"""Tests for loan routes with group_id filtering."""


class TestLoanStatus:
    """GET /api/loans/status — eligibility check."""

    def test_default_eligibility(self, client, group_ctx):
        """Default credit 100 → max 200, 1/week, eligible."""
        h = group_ctx["headers"]
        res = client.get("/api/loans/status", headers=h)
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 1
        child = data[0]
        assert child["eligible"] is True
        assert child["credit_score"] == 100
        assert child["limits"]["max_amount"] == 200
        assert child["limits"]["loans_per_week"] == 1
        assert child["limits"]["cooldown_days"] == 7

    def test_no_group_header(self, client):
        """Missing X-Group-Code returns 422 (FastAPI validation)."""
        res = client.get("/api/loans/status")
        assert res.status_code == 422


class TestBorrowAndRepay:
    """Full borrow → repay lifecycle."""

    def test_borrow_and_full_repay(self, client, group_ctx):
        """Borrow 50, verify points increase, repay, verify loan repaid."""
        h = group_ctx["headers"]
        info = client.get(f"/api/groups/{group_ctx['invite_code']}", headers=h).json()
        initial_points = info["children"][0]["total_points"]

        # Borrow
        res = client.post("/api/loans", json={"amount": 50}, headers=h)
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["amount"] == 50
        assert data["total_points"] == initial_points + 50

        # Verify active loan
        loans = client.get("/api/loans", headers=h).json()
        active = [l for l in loans if l["status"] == "active"]
        assert len(active) == 1
        assert active[0]["amount"] == 50
        assert active[0]["remaining_principal"] == 50
        loan_id = active[0]["id"]

        # Repay full amount
        repay_amount = active[0]["total_owed"]
        res2 = client.post(f"/api/loans/{loan_id}/repay", json={"amount": repay_amount}, headers=h)
        assert res2.status_code == 200
        data2 = res2.json()
        assert data2["success"] is True
        assert data2["fully_repaid"] is True
        assert data2["principal_paid"] == 50

        # Verify loan repaid
        loans2 = client.get("/api/loans", headers=h).json()
        repaid = [l for l in loans2 if l["id"] == loan_id]
        assert repaid[0]["status"] == "repaid"

        # Verify points deducted
        info2 = client.get(f"/api/groups/{group_ctx['invite_code']}", headers=h).json()
        assert info2["children"][0]["total_points"] == initial_points + 50 - repay_amount

    def test_partial_repay(self, client, group_ctx):
        """Partially repay principal — loan stays active, principal decreases."""
        h = group_ctx["headers"]
        client.post("/api/loans", json={"amount": 100}, headers=h)
        loans = client.get("/api/loans", headers=h).json()
        active = [l for l in loans if l["status"] == "active"]
        loan_id = active[0]["id"]

        # Repay 40 (partial)
        res = client.post(f"/api/loans/{loan_id}/repay", json={"amount": 40}, headers=h)
        assert res.status_code == 200
        data = res.json()
        assert data["principal_paid"] == 40
        assert data["fully_repaid"] is False
        assert data["new_remaining_principal"] == 60

        # Loan still active
        loans2 = client.get("/api/loans", headers=h).json()
        active2 = [l for l in loans2 if l["status"] == "active"]
        assert len(active2) == 1
        assert active2[0]["remaining_principal"] == 60


class TestBorrowValidation:
    """Validation on borrow endpoint."""

    def test_borrow_exceeds_max(self, client, group_ctx):
        """Borrowing more than max_amount returns 400."""
        h = group_ctx["headers"]
        res = client.post("/api/loans", json={"amount": 9999}, headers=h)
        assert res.status_code == 400
        assert "超过上限" in res.json()["detail"]

    def test_borrow_zero_or_negative(self, client, group_ctx):
        """Zero or negative amount returns 400."""
        h = group_ctx["headers"]
        assert client.post("/api/loans", json={"amount": 0}, headers=h).status_code == 400
        assert client.post("/api/loans", json={"amount": -10}, headers=h).status_code == 400

    def test_cannot_borrow_while_active(self, client, group_ctx):
        """Having an active loan prevents new borrowing."""
        h = group_ctx["headers"]
        res = client.post("/api/loans", json={"amount": 50}, headers=h)
        assert res.status_code == 200
        res2 = client.post("/api/loans", json={"amount": 30}, headers=h)
        assert res2.status_code == 400
        assert "还清" in res2.json()["detail"]


class TestRepayValidation:
    """Validation on repay endpoint."""

    def test_repay_nonexistent_loan(self, client, group_ctx):
        """Repaying a missing loan returns 404."""
        res = client.post("/api/loans/99999/repay", json={"amount": 10}, headers=group_ctx["headers"])
        assert res.status_code == 404

    def test_repay_zero_or_negative(self, client, group_ctx):
        """Zero or negative repay returns 400."""
        h = group_ctx["headers"]
        client.post("/api/loans", json={"amount": 50}, headers=h)
        loans = client.get("/api/loans", headers=h).json()
        active = [l for l in loans if l["status"] == "active"]
        loan_id = active[0]["id"]
        assert client.post(f"/api/loans/{loan_id}/repay", json={"amount": 0}, headers=h).status_code == 400
        assert client.post(f"/api/loans/{loan_id}/repay", json={"amount": -5}, headers=h).status_code == 400

    def test_repay_insufficient_points(self, client, group_ctx):
        """Repaying more than available points returns 400."""
        h = group_ctx["headers"]
        client.post("/api/loans", json={"amount": 20}, headers=h)
        loans = client.get("/api/loans", headers=h).json()
        active = [l for l in loans if l["status"] == "active"]
        loan_id = active[0]["id"]
        res = client.post(f"/api/loans/{loan_id}/repay", json={"amount": 99999}, headers=h)
        assert res.status_code == 400
        assert "不足" in res.json()["detail"]


class TestCreditScore:
    """Credit score updates on repayment."""

    def test_credit_score_increases_on_immediate_repay(self, client, group_ctx):
        """Repay immediately → credit +5."""
        h = group_ctx["headers"]
        client.post("/api/loans", json={"amount": 20}, headers=h)
        loans = client.get("/api/loans", headers=h).json()
        active = [l for l in loans if l["status"] == "active"]
        loan_id = active[0]["id"]

        res = client.post(
            f"/api/loans/{loan_id}/repay",
            json={"amount": active[0]["total_owed"]},
            headers=h,
        )
        assert res.status_code == 200
        assert res.json()["credit_score_change"] == 5

        status = client.get("/api/loans/status", headers=h).json()
        assert status[0]["credit_score"] == 105

    def test_credit_score_affects_limits(self, client, group_ctx):
        """Credit 105 → still in 100-149 tier, max 200."""
        h = group_ctx["headers"]
        # Borrow and immediately repay to get +5 credit
        client.post("/api/loans", json={"amount": 20}, headers=h)
        loans = client.get("/api/loans", headers=h).json()
        active = [l for l in loans if l["status"] == "active"]
        client.post(
            f"/api/loans/{active[0]['id']}/repay",
            json={"amount": active[0]["total_owed"]},
            headers=h,
        )

        # Check limits: 105 → still same tier as 100
        status = client.get("/api/loans/status", headers=h).json()
        assert status[0]["credit_score"] == 105
        assert status[0]["limits"]["max_amount"] == 200


class TestGroupIsolation:
    """Loans from one group should not leak to another."""

    def test_loans_isolated_by_group(self, client):
        """Loans from group A not visible in group B."""
        g1 = client.post("/api/groups", json={"name": "A", "child_name": "甲"}).json()
        g2 = client.post("/api/groups", json={"name": "B", "child_name": "乙"}).json()
        h1 = {"X-Group-Code": g1["invite_code"]}
        h2 = {"X-Group-Code": g2["invite_code"]}

        client.post("/api/loans", json={"amount": 30}, headers=h1)
        loans_a = client.get("/api/loans", headers=h1).json()
        loans_b = client.get("/api/loans", headers=h2).json()

        assert len(loans_a) > 0
        assert len(loans_b) == 0

    def test_repay_wrong_group_blocked(self, client):
        """Cannot repay another group's loan."""
        g1 = client.post("/api/groups", json={"name": "A", "child_name": "甲"}).json()
        g2 = client.post("/api/groups", json={"name": "B", "child_name": "乙"}).json()
        h1 = {"X-Group-Code": g1["invite_code"]}
        h2 = {"X-Group-Code": g2["invite_code"]}

        client.post("/api/loans", json={"amount": 30}, headers=h1)
        loans = client.get("/api/loans", headers=h1).json()
        loan_id = loans[0]["id"]

        res = client.post(f"/api/loans/{loan_id}/repay", json={"amount": 30}, headers=h2)
        assert res.status_code == 404


class TestAdminLoanSettings:
    """Admin loan settings read/write."""

    def test_default_settings(self, client, admin_token):
        """Default settings: rate 5%, max 200."""
        res = client.get("/api/admin/loan-settings", headers=admin_token)
        assert res.status_code == 200
        data = res.json()
        assert data["interest_rate"] == 5.0
        assert data["max_amount"] == 200

    def test_save_and_read_settings(self, client, admin_token):
        """Save custom settings and read them back."""
        res = client.post(
            "/api/admin/loan-settings",
            json={"interest_rate": 3.5, "max_amount": 500},
            headers=admin_token,
        )
        assert res.status_code == 200

        res2 = client.get("/api/admin/loan-settings", headers=admin_token)
        assert res2.status_code == 200
        data = res2.json()
        assert data["interest_rate"] == 3.5
        assert data["max_amount"] == 500

        # Settings affect user-facing status
        # Create a group and check that the new max applies
        g = client.post("/api/groups", json={"name": "Test", "child_name": "Kid"}).json()
        h = {"X-Group-Code": g["invite_code"]}
        status = client.get("/api/loans/status", headers=h).json()
        assert status[0]["limits"]["max_amount"] == 500
        assert status[0]["limits"]["interest_rate"] == 3.5

        # Reset to defaults for other tests
        client.post(
            "/api/admin/loan-settings",
            json={"interest_rate": 5, "max_amount": 200},
            headers=admin_token,
        )


class TestLoanList:
    """GET /api/loans — loan listing with calculated fields."""

    def test_loan_list_includes_total_owed(self, client, group_ctx):
        """Each loan in the list includes total_owed and accrued_interest."""
        h = group_ctx["headers"]
        client.post("/api/loans", json={"amount": 50}, headers=h)
        loans = client.get("/api/loans", headers=h).json()
        assert len(loans) == 1
        assert "total_owed" in loans[0]
        assert "accrued_interest" in loans[0]
        assert "child_name" in loans[0]
