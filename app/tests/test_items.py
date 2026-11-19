from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app import cache, db, items
from app.main import app

AUTH = {"Authorization": "Bearer test-token", "X-Trace": "1"}
TRACE = {"X-Trace": "1"}


class FakeBackends:
    """In-memory stand-ins for Postgres and Redis, so cache hit/miss logic is testable."""

    def __init__(self):
        self.rows: dict[int, dict] = {}
        self.cached: dict[str, str] = {}
        self.next_id = 1

    async def create_item(self, name, created_by):
        row = {
            "id": self.next_id,
            "name": name,
            "created_by": created_by,
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        }
        self.rows[row["id"]] = row
        self.next_id += 1
        return row

    async def get_item(self, item_id):
        return self.rows.get(item_id)

    async def update_item(self, item_id, name):
        if item_id not in self.rows:
            return None
        self.rows[item_id]["name"] = name
        return self.rows[item_id]

    async def delete_item(self, item_id):
        return self.rows.pop(item_id, None) is not None

    async def cache_get(self, key):
        return self.cached.get(key)

    async def cache_set(self, key, value, ttl_s):
        self.cached[key] = value

    async def cache_delete(self, key):
        return 1 if self.cached.pop(key, None) is not None else 0


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def fake(monkeypatch):
    f = FakeBackends()
    for name in ("create_item", "get_item", "update_item", "delete_item"):
        monkeypatch.setattr(db, name, getattr(f, name))
    monkeypatch.setattr(cache, "get", f.cache_get)
    monkeypatch.setattr(cache, "set", f.cache_set)
    monkeypatch.setattr(cache, "delete", f.cache_delete)
    return f


def steps(r):
    return [(s["node"], s["name"], s["status"]) for s in r.json()["trace"]["steps"]]


def test_trace_only_when_asked(client, fake):
    fake.rows[1] = {"id": 1, "name": "a", "created_by": "t", "created_at": "x"}
    plain = client.get("/api/v1/items/1").json()
    traced = client.get("/api/v1/items/1", headers=TRACE).json()
    assert "trace" not in plain and plain["item"]["id"] == 1
    assert traced["result"]["item"]["id"] == 1
    assert traced["trace"]["status_code"] == 200


def test_write_without_token_is_rejected_at_auth(client, fake):
    r = client.post("/api/v1/items", json={"name": "a"}, headers=TRACE)
    assert r.status_code == 401
    assert steps(r)[-1] == ("app", "Auth check", "error")
    assert r.json()["trace"]["steps"][-1]["detail"]["result"] == "no token sent"


def test_wrong_token_is_rejected_and_never_echoed(client, fake):
    r = client.post(
        "/api/v1/items",
        json={"name": "a"},
        headers={"Authorization": "Bearer nope", "X-Trace": "1"},
    )
    assert r.status_code == 401
    assert "nope" not in r.text


def test_invalid_payload_stops_at_validation(client, fake):
    r = client.post("/api/v1/items", json={"name": ""}, headers=AUTH)
    assert r.status_code == 422
    assert steps(r)[-1] == ("app", "Validate payload", "error")


def test_create_writes_db_then_cache(client, fake):
    r = client.post("/api/v1/items", json={"name": "first"}, headers=AUTH)
    assert r.status_code == 201
    assert steps(r) == [
        ("app", "Request received", "ok"),
        ("app", "Auth check", "ok"),
        ("app", "Validate payload", "ok"),
        ("db", "INSERT item", "ok"),
        ("cache", "Cache write (write-through)", "ok"),
    ]
    assert "item:1" in fake.cached


def test_get_miss_then_hit(client, fake):
    fake.rows[7] = {"id": 7, "name": "a", "created_by": "t", "created_at": "x"}
    first = client.get("/api/v1/items/7", headers=TRACE)
    second = client.get("/api/v1/items/7", headers=TRACE)
    assert ("cache", "Cache lookup", "miss") in steps(first)
    assert ("db", "SELECT item by id", "ok") in steps(first)
    assert first.json()["result"]["source"] == "database"
    assert ("cache", "Cache lookup", "hit") in steps(second)
    assert all(node != "db" for node, _, _ in steps(second))
    assert second.json()["result"]["source"] == "cache"


def test_force_miss_evicts_first(client, fake):
    fake.rows[2] = {"id": 2, "name": "a", "created_by": "t", "created_at": "x"}
    client.get("/api/v1/items/2")  # now cached
    r = client.get("/api/v1/items/2", headers=TRACE | {"X-Sim-Cache": "force-miss"})
    assert steps(r)[1:4] == [
        ("app", "Auth check", "skipped"),
        ("cache", "Evict key (scenario: force miss)", "ok"),
        ("cache", "Cache lookup", "miss"),
    ]


def test_force_hit_warms_first(client, fake):
    fake.rows[3] = {"id": 3, "name": "a", "created_by": "t", "created_at": "x"}
    r = client.get("/api/v1/items/3", headers=TRACE | {"X-Sim-Cache": "force-hit"})
    assert ("cache", "Warm cache (scenario: force hit)", "ok") in steps(r)
    assert ("cache", "Cache lookup", "hit") in steps(r)


def test_missing_item_is_404_after_db(client, fake):
    r = client.get("/api/v1/items/99", headers=TRACE)
    assert r.status_code == 404
    assert steps(r)[-1] == ("db", "SELECT item by id", "ok")


def test_simulated_db_error(client, fake):
    r = client.get("/api/v1/items/1", headers=TRACE | {"X-Sim-DB-Error": "1"})
    assert r.status_code == 503
    last = r.json()["trace"]["steps"][-1]
    assert (last["node"], last["status"]) == ("db", "error")
    assert last["detail"]["simulated"] is True


def test_slow_network_adds_steps_and_time(client, fake):
    fake.rows[1] = {"id": 1, "name": "a", "created_by": "t", "created_at": "x"}
    r = client.get("/api/v1/items/1", headers=TRACE | {"X-Sim-Network-Ms": "100"})
    trace = r.json()["trace"]
    names = [s["name"] for s in trace["steps"]]
    assert names[1] == "Slow network (inbound)" and names[-1] == "Slow network (outbound)"
    assert trace["total_ms"] >= 100


def test_update_invalidates_cache(client, fake):
    fake.rows[4] = {"id": 4, "name": "a", "created_by": "t", "created_at": "x"}
    fake.cached["item:4"] = "{}"
    r = client.put("/api/v1/items/4", json={"name": "b"}, headers=AUTH)
    assert r.status_code == 200
    assert steps(r)[-1] == ("cache", "Invalidate cache", "ok")
    assert "item:4" not in fake.cached


def test_delete(client, fake):
    fake.rows[5] = {"id": 5, "name": "a", "created_by": "t", "created_at": "x"}
    r = client.delete("/api/v1/items/5", headers=AUTH)
    assert r.status_code == 200 and r.json()["result"] == {"deleted": 5}
    assert client.delete("/api/v1/items/5", headers=AUTH).status_code == 404


def test_cache_down_falls_back_to_database(client, fake, monkeypatch):
    async def broken(*_):
        raise cache.CacheUnavailable("connection refused")

    monkeypatch.setattr(cache, "get", broken)
    monkeypatch.setattr(cache, "set", broken)
    fake.rows[6] = {"id": 6, "name": "a", "created_by": "t", "created_at": "x"}
    r = client.get("/api/v1/items/6", headers=TRACE)
    assert r.status_code == 200
    assert ("cache", "Cache lookup", "error") in steps(r)
    assert r.json()["result"]["source"] == "database"


def test_scenarios_ignored_when_simulation_disabled(client, fake, monkeypatch):
    monkeypatch.setattr(items, "SIMULATION_ENABLED", False)
    fake.rows[1] = {"id": 1, "name": "a", "created_by": "t", "created_at": "x"}
    r = client.get("/api/v1/items/1", headers=TRACE | {"X-Sim-DB-Error": "1"})
    assert r.status_code == 200
    assert r.json()["trace"]["scenario"]["enabled"] is False
