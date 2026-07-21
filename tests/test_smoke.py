"""Smoke tests: verify test infrastructure and app boot."""


def test_health_returns_ok(client):
    """GET /api/health should return ok."""
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_get_tasks_returns_list(client):
    """GET /api/tasks with X-Group-Code header should return a list."""
    g = client.post("/api/groups", json={"name": "smoke", "child_name": "test"}).json()
    res = client.get("/api/tasks", headers={"X-Group-Code": g["invite_code"]})
    assert res.status_code == 200
    assert isinstance(res.json(), list)
