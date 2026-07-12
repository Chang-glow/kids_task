"""Tests for children routes."""


class TestChildren:
    def test_add_child(self, client, group_ctx):
        """POST /api/children adds a child to the group."""
        res = client.post(
            "/api/children",
            json={"name": "小红", "emoji": "👧"},
            headers=group_ctx["headers"],
        )
        assert res.status_code == 200
        child = res.json()
        assert child["name"] == "小红"
        assert child["emoji"] == "👧"
        assert child["group_id"] == group_ctx["id"]

    def test_add_child_empty_name(self, client, group_ctx):
        """Empty name returns 400."""
        res = client.post(
            "/api/children",
            json={"name": "", "emoji": "👧"},
            headers=group_ctx["headers"],
        )
        assert res.status_code == 400

    def test_add_child_bad_group(self, client):
        """Nonexistent group returns 404."""
        res = client.post(
            "/api/children",
            json={"name": "小红", "emoji": "👧"},
            headers={"X-Group-Code": "nonexistent"},
        )
        assert res.status_code == 404

    def test_children_listed_in_group(self, client, group_ctx):
        """Children added via POST appear in group GET."""
        client.post(
            "/api/children",
            json={"name": "小红", "emoji": "👧"},
            headers=group_ctx["headers"],
        )
        info = client.get(f"/api/groups/{group_ctx['invite_code']}").json()
        names = [c["name"] for c in info["children"]]
        assert "小红" in names
