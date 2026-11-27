import os

os.environ["API_TOKEN"] = "test-token"

from fastapi.testclient import TestClient  # noqa: E402

from app import cluster, main  # noqa: E402


def test_load_requires_token(monkeypatch):
    calls = []
    monkeypatch.setattr(cluster, "set_load", calls.append)
    monkeypatch.setattr(main, "API_TOKEN", "test-token")
    with TestClient(main.app) as c:
        assert c.post("/api/lab/load", json={"level": 2}).status_code == 401
        bad = {"Authorization": "Bearer wrong"}
        assert c.post("/api/lab/load", json={"level": 2}, headers=bad).status_code == 401
        good = {"Authorization": "Bearer test-token"}
        assert c.post("/api/lab/load", json={"level": 9}, headers=good).status_code == 422
        r = c.post("/api/lab/load", json={"level": 2}, headers=good)
        assert r.status_code == 200 and calls == [2]


def test_snapshot_unavailable_is_503(monkeypatch):
    def boom():
        raise RuntimeError("no cluster")

    monkeypatch.setattr(cluster, "fetch_snapshot", boom)
    with TestClient(main.app) as c:
        r = c.get("/api/lab/cluster")
        assert r.status_code == 503 and r.json()["error"] == "cluster unavailable"
