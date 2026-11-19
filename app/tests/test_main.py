import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_liveness(client):
    r = client.get("/health/live")
    assert r.status_code == 200
    assert r.json()["status"] == "alive"


def test_readiness_does_not_depend_on_database(client):
    r = client.get("/health/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


def test_info_identifies_instance(client):
    r = client.get("/api/v1/info")
    body = r.json()
    assert r.status_code == 200
    assert body["instance"]
    assert body["version"]


def test_identity_headers(client):
    r = client.get("/api/v1/info", headers={"X-Request-ID": "abc123"})
    assert r.headers["X-Request-ID"] == "abc123"
    assert r.headers["X-Pod-Name"]
    assert r.headers["X-App-Version"]
    assert r.headers["Server-Timing"].startswith("app;dur=")


def test_request_id_generated_when_missing(client):
    r = client.get("/api/v1/info")
    assert len(r.headers["X-Request-ID"]) == 32


def test_items_without_database_returns_503(client):
    assert client.get("/api/v1/items").status_code == 503
    auth = {"Authorization": "Bearer test-token"}
    assert client.post("/api/v1/items", json={"name": "x"}, headers=auth).status_code == 503


def test_item_validation(client):
    auth = {"Authorization": "Bearer test-token"}
    assert client.post("/api/v1/items", json={"name": ""}, headers=auth).status_code == 422


def test_cpu_burns_requested_time(client):
    r = client.get("/api/v1/cpu", params={"ms": 20})
    assert r.status_code == 200
    assert r.json()["burned_ms"] == 20


@pytest.mark.parametrize(
    "path", ["/api/v1/cpu?ms=0", "/api/v1/cpu?ms=2001", "/api/v1/slow?ms=10001"]
)
def test_bounds_rejected(client, path):
    assert client.get(path).status_code == 422


def test_slow_waits(client):
    r = client.get("/api/v1/slow", params={"ms": 10})
    assert r.json()["waited_ms"] == 10


def test_metrics_exposed(client):
    client.get("/api/v1/info")
    r = client.get("/metrics")
    assert r.status_code == 200
    assert 'http_requests_total{method="GET",path="/api/v1/info",status="200"}' in r.text
