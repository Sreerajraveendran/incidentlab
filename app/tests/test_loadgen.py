import http.client
import urllib.error

import pytest

from app import loadgen


@pytest.fixture(autouse=True)
def reset_state():
    loadgen.counts.update(ok=0, failed=0, skipped=0)
    loadgen.in_flight = 0
    yield


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return b"{}"


def test_success_releases_slot(monkeypatch):
    monkeypatch.setattr(loadgen.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
    assert loadgen.try_acquire(5)
    loadgen.one_request("http://x")
    assert loadgen.in_flight == 0
    assert loadgen.counts["ok"] == 1


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.URLError("refused"),
        TimeoutError(),
        ConnectionResetError(),
        http.client.IncompleteRead(b""),  # previously escaped and leaked the slot
        ValueError("bad url"),
    ],
)
def test_every_failure_releases_slot(monkeypatch, error):
    def boom(*_a, **_k):
        raise error

    monkeypatch.setattr(loadgen.urllib.request, "urlopen", boom)
    assert loadgen.try_acquire(5)
    loadgen.one_request("http://x")
    assert loadgen.in_flight == 0
    assert loadgen.counts["failed"] == 1


def test_skips_when_full():
    assert loadgen.try_acquire(1)
    assert not loadgen.try_acquire(1)
    assert loadgen.counts["skipped"] == 1
    assert loadgen.in_flight == 1


@pytest.mark.parametrize("rate", ["0", "-1"])
def test_rate_must_be_positive(monkeypatch, rate):
    monkeypatch.setenv("RATE", rate)
    with pytest.raises(SystemExit):
        loadgen.read_config()


def test_default_target_is_the_ingress(monkeypatch):
    monkeypatch.delenv("TARGET_URL", raising=False)
    monkeypatch.delenv("RATE", raising=False)
    target, rate, _ = loadgen.read_config()
    assert "ingress-nginx-controller" in target
    assert rate == 2.0
