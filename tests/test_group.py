"""Tests for group routes: create and join."""


def test_create_group(client):
    """POST /api/groups creates a new family group with default child."""
    res = client.post("/api/groups", json={"name": "测试家庭", "child_name": "小明"})
    assert res.status_code == 200
    data = res.json()
    assert "invite_code" in data
    assert len(data["invite_code"]) > 0
    assert data["name"] == "测试家庭"
    assert data["children"] == [{"name": "小明", "emoji": "👶", "total_points": 0}]


def test_create_group_defaults(client):
    """POST /api/groups with empty body uses defaults."""
    res = client.post("/api/groups", json={})
    assert res.status_code == 200
    data = res.json()
    assert data["name"] == "我们的家"


def test_get_group_by_invite_code(client):
    """GET /api/groups/{invite_code} returns group info."""
    create_res = client.post("/api/groups", json={"name": "测试", "child_name": "小红"})
    invite_code = create_res.json()["invite_code"]

    res = client.get(f"/api/groups/{invite_code}")
    assert res.status_code == 200
    data = res.json()
    assert data["name"] == "测试"
    assert data["invite_code"] == invite_code
    assert len(data["children"]) == 1
    assert data["children"][0]["name"] == "小红"


def test_get_group_not_found(client):
    """GET /api/groups/{nonexistent} returns 404."""
    res = client.get("/api/groups/nonexistent-code")
    assert res.status_code == 404
