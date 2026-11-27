from datetime import UTC, datetime, timedelta

import pytest

from app import scenarios
from app.actions import evaluate
from app.engine import RECOVERY_STREAK, Conflict, Engine, base_score
from app.store import MemoryStore

SCENARIOS = scenarios.load_all()


class Clock:
    def __init__(self):
        self.t = datetime(2026, 11, 28, 10, 0, tzinfo=UTC)

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += timedelta(seconds=seconds)


class FakePort:
    """A tiny in-memory cluster: just the fields the checks read."""

    def __init__(self):
        self.argo = True
        self.load_level = 0
        self.fail_argo = False
        self.image = "incidentlab-app:dev"
        self.postgres_ready = 1
        self.hpa_max = 6
        self.hpa_current = 2
        self.waiting = []  # waiting reasons of app pods
        self.items_status = 200
        self.actions = []

    # reads
    def workload(self, kind, name):
        if kind == "statefulset":
            return {"status": {"readyReplicas": self.postgres_ready}}
        ready = 0 if self.waiting else 2
        return {
            "metadata": {"generation": 3},
            "spec": {
                "replicas": 2,
                "template": {"spec": {"containers": [{"name": "app", "image": self.image}]}},
            },
            "status": {
                "observedGeneration": 3,
                "updatedReplicas": ready,
                "readyReplicas": 2,
                "replicas": 2 + (1 if self.waiting else 0),
            },
        }

    def pods(self, selector):
        return [
            {
                "metadata": {"name": f"p{i}"},
                "status": {"containerStatuses": [{"state": {"waiting": {"reason": r}}}]},
            }
            for i, r in enumerate(self.waiting)
        ]

    def hpa(self, name):
        return {
            "metadata": {"name": name},
            "spec": {
                "maxReplicas": self.hpa_max,
                "metrics": [
                    {
                        "type": "Resource",
                        "resource": {
                            "name": "cpu",
                            "target": {"type": "Utilization", "averageUtilization": 50},
                        },
                    }
                ],
            },
            "status": {
                "currentReplicas": self.hpa_current,
                "currentMetrics": [
                    {
                        "type": "Resource",
                        "resource": {"name": "cpu", "current": {"averageUtilization": 140}},
                    }
                ],
            },
        }

    def http_status(self, path):
        return self.items_status if path == "/api/v1/items" else 200

    # writes
    def run_action(self, step):
        self.actions.append(step["action"])
        if step["action"] == "scale":
            self.postgres_ready = step["replicas"]
            self.items_status = 503 if step["replicas"] == 0 else 200
        elif step["action"] == "set_image":
            self.image = step["image"]
            self.waiting = ["ImagePullBackOff"]
        elif step["action"] == "patch_hpa_max":
            self.hpa_max = step["max"]
        elif step["action"] == "set_load":
            self.load_level = step["level"]
        return f"did {step['action']}"

    def set_load_off(self):
        self.load_level = 0

    def argo_auto_sync(self, enabled):
        if self.fail_argo:
            raise RuntimeError("argocd unreachable")
        self.argo = enabled


@pytest.fixture
def setup():
    clock, port, store = Clock(), FakePort(), MemoryStore()
    return Engine(SCENARIOS, store, port, now=clock), port, store, clock


def kinds(run):
    return [e["kind"] for e in run["events"]]


def test_db_down_full_run(setup):
    engine, port, store, clock = setup
    run = engine.start("db-down")
    assert run["state"] == "active" and port.argo is False and port.postgres_ready == 0

    clock.advance(5)
    engine.tick()
    run = engine.get_run(run["id"])
    assert kinds(run).count("symptom") == 3  # each symptom recorded once
    assert [c["ok"] for c in run["checks"]] == [False, False]
    engine.tick()
    assert kinds(engine.get_run(run["id"])).count("symptom") == 3  # not repeated

    with pytest.raises(Conflict):
        engine.start("bad-deploy")  # one incident at a time

    port.postgres_ready, port.items_status = 1, 200  # the user's fix
    for _ in range(RECOVERY_STREAK):
        clock.advance(5)
        engine.tick()
    run = engine.get_run(run["id"])
    assert run["state"] == "resolved" and port.argo is True
    assert run["seconds_to_recover"] == 5 * (RECOVERY_STREAK + 1)
    assert run["score"] == 100
    assert engine.list_runs()["active"] is None
    assert run["root_cause_options"] and "root_cause_answer" not in run

    run = engine.answer(run["id"], SCENARIOS["db-down"].root_cause_correct)
    assert run["score"] == 120 and run["root_cause_answer"] == 1 and run["explanation"]
    with pytest.raises(Conflict):
        engine.answer(run["id"], 0)
    assert store.history[-1]["score"] == 120  # persisted


def test_streak_resets_when_a_check_fails_again(setup):
    engine, port, _, clock = setup
    run = engine.start("db-down")
    port.postgres_ready, port.items_status = 1, 200
    engine.tick()
    engine.tick()
    port.items_status = 503  # flapping
    engine.tick()
    port.items_status = 200
    engine.tick()
    assert engine.get_run(run["id"])["state"] == "active"


def test_hints_cost_points_and_are_revealed_one_by_one(setup):
    engine, port, _, clock = setup
    run = engine.start("bad-deploy")
    assert run["hints"] == []
    run = engine.hint(run["id"])
    assert len(run["hints"]) == 1
    engine.hint(run["id"])
    engine.hint(run["id"])
    with pytest.raises(Conflict):
        engine.hint(run["id"])
    port.image, port.waiting = "incidentlab-app:dev", []
    for _ in range(RECOVERY_STREAK):
        engine.tick()
    assert engine.get_run(run["id"])["score"] == 70


def test_time_limit_aborts_and_cleans_up(setup):
    engine, port, _, clock = setup
    run = engine.start("traffic-spike")
    assert port.load_level == 2 and port.hpa_max == 2
    clock.advance(15 * 60 + 1)
    engine.tick()
    run = engine.get_run(run["id"])
    assert run["state"] == "aborted" and run["abort_reason"] == "time limit reached"
    assert port.load_level == 0 and port.argo is True and run["score"] is None


def test_give_up(setup):
    engine, port, _, _ = setup
    run = engine.abort(engine.start("traffic-spike")["id"])
    assert run["state"] == "aborted" and port.load_level == 0 and port.argo is True
    with pytest.raises(Conflict):
        engine.answer(run["id"], 0)


def test_cleanup_retries_until_argo_is_reachable(setup):
    engine, port, _, _ = setup
    run = engine.start("db-down")
    port.fail_argo = True
    engine.abort(run["id"])
    assert engine.get_run(run["id"])["state"] == "cleanup"
    engine.tick()
    run = engine.get_run(run["id"])
    assert kinds(run).count("error") == 1  # the same error isn't logged every tick
    port.fail_argo = False
    engine.tick()
    assert engine.get_run(run["id"])["state"] == "aborted" and port.argo is True


def test_a_restart_continues_the_run(setup):
    engine, port, store, clock = setup
    run = engine.start("db-down")
    restarted = Engine(SCENARIOS, store, port, now=clock)  # new process, same ConfigMap
    port.postgres_ready, port.items_status = 1, 200
    for _ in range(RECOVERY_STREAK):
        restarted.tick()
    assert restarted.get_run(run["id"])["state"] == "resolved"


def test_failed_injection_aborts_and_resumes_argo(setup):
    engine, port, _, _ = setup

    def boom(step):
        raise RuntimeError("forbidden")

    port.run_action = boom
    run = engine.start("db-down")
    assert run["state"] == "aborted" and run["abort_reason"] == "injection failed"
    assert port.argo is True


def test_base_score():
    assert base_score(60, 0) == 100
    assert base_score(120 + 30 * 10, 0) == 90
    assert base_score(120, 2) == 80
    assert base_score(10_000, 5) == 10  # floor


def test_checks_against_fake_cluster():
    port = FakePort()
    port.waiting = ["ImagePullBackOff"]
    step = {"check": "pod_waiting_reason", "selector": "app=x", "reasons": ["ImagePullBackOff"]}
    assert evaluate(step, port)["ok"]
    rolled = {"check": "deployment_rolled_out", "name": "incidentlab-app"}
    assert not evaluate(rolled, port)["ok"]
    port.waiting = []
    assert evaluate(rolled, port)["ok"]
    at_max = {"check": "hpa_at_max", "name": "incidentlab-app"}
    port.hpa_max = 2
    assert evaluate(at_max, port)["ok"]
    port.hpa_max = 6
    assert not evaluate(at_max, port)["ok"]


def test_a_check_error_counts_as_not_ok():
    class Broken(FakePort):
        def hpa(self, name):
            raise RuntimeError("API down")

    result = evaluate({"check": "hpa_max_at_least", "name": "x", "value": 6}, Broken())
    assert not result["ok"] and "could not check" in result["detail"]
