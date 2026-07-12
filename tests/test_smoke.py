"""Smoke tests: verify test infrastructure and app boot."""


def test_root_returns_html(client):
    """GET / should return the index.html page."""
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]


def test_get_user_returns_default(client):
    """GET /api/user should return the default user."""
    res = client.get("/api/user")
    assert res.status_code == 200
    data = res.json()
    assert data["name"] == "小主人"
    assert "total_points" in data


def test_get_tasks_returns_list(client):
    """GET /api/tasks with X-Group-Code header should return a list."""
    g = client.post("/api/groups", json={"name": "smoke", "child_name": "test"}).json()
    res = client.get("/api/tasks", headers={"X-Group-Code": g["invite_code"]})
    assert res.status_code == 200
    assert isinstance(res.json(), list)
