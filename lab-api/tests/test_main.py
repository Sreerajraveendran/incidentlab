from fastapi.testclient import TestClient

from app import cluster, main


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


# ---- incident endpoints --------------------------------------------------------
def with_engine(monkeypatch):
    from app.engine import Engine
    from app.store import MemoryStore
    from tests.test_engine import Clock, FakePort

    port = FakePort()
    monkeypatch.setattr(main, "engine", Engine(main.SCENARIOS, MemoryStore(), port, now=Clock()))
    return port


GOOD = {"Authorization": "Bearer test-token"}


def test_scenarios_list_hides_answers():
    with TestClient(main.app) as c:
        items = c.get("/api/lab/scenarios").json()["scenarios"]
    assert {s["id"] for s in items} == {"bad-deploy", "db-down", "traffic-spike"}
    assert all("hints" not in s and "root_cause_correct" not in s for s in items)


def test_every_incident_command_needs_the_token(monkeypatch):
    monkeypatch.setattr(main, "API_TOKEN", "test-token")
    with_engine(monkeypatch)
    with TestClient(main.app) as c:
        assert c.post("/api/lab/runs", json={"scenario_id": "db-down"}).status_code == 401
        for path in ("hint", "abort"):
            assert c.post(f"/api/lab/runs/x/{path}").status_code == 401
        assert c.post("/api/lab/runs/x/root-cause", json={"choice": 0}).status_code == 401


def test_incident_lifecycle_over_http(monkeypatch):
    monkeypatch.setattr(main, "API_TOKEN", "test-token")
    port = with_engine(monkeypatch)
    with TestClient(main.app) as c:
        r = c.post("/api/lab/runs", json={"scenario_id": "db-down"}, headers=GOOD)
        assert r.status_code == 201 and port.argo is False
        run_id = r.json()["id"]
        again = c.post("/api/lab/runs", json={"scenario_id": "bad-deploy"}, headers=GOOD)
        assert again.status_code == 409
        unknown = c.post("/api/lab/runs", json={"scenario_id": "nope"}, headers=GOOD)
        assert unknown.status_code == 404
        assert c.post(f"/api/lab/runs/{run_id}/hint", headers=GOOD).json()["hints"]
        assert c.get("/api/lab/runs").json()["active"]["id"] == run_id
        r = c.post(f"/api/lab/runs/{run_id}/abort", headers=GOOD)
        assert r.json()["state"] == "aborted" and port.argo is True
        assert c.get(f"/api/lab/runs/{run_id}").json()["state"] == "aborted"
        assert c.get("/api/lab/runs/missing").status_code == 404


def test_engine_unavailable_is_503(monkeypatch):
    monkeypatch.setattr(main, "engine", None)
    with TestClient(main.app) as c:
        assert c.get("/api/lab/runs").status_code == 503
