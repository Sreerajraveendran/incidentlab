import copy

import pytest

from app import scenarios
from app.scenarios import ScenarioError, parse

VALID = {
    "id": "x",
    "title": "T",
    "briefing": "B",
    "explanation": "E",
    "time_limit_minutes": 10,
    "inject": [{"action": "scale", "kind": "statefulset", "name": "postgres", "replicas": 0}],
    "symptoms": [{"check": "http_status", "path": "/", "status": 503}],
    "recovery": [
        {"check": "ready_replicas", "kind": "statefulset", "name": "postgres", "at_least": 1}
    ],
    "hints": ["h1"],
    "root_cause": {"options": ["a", "b"], "correct": 1},
}


def with_change(**changes):
    data = copy.deepcopy(VALID)
    data.update(changes)
    return data


def test_shipped_scenarios_load():
    loaded = scenarios.load_all()
    assert set(loaded) == {"bad-deploy", "db-down", "traffic-spike"}
    assert loaded["traffic-spike"].uses_load and not loaded["db-down"].uses_load


def test_public_view_hides_hints_and_answer():
    public = parse(VALID).public()
    assert public["hint_count"] == 1
    assert "hints" not in public and "root_cause_correct" not in public


@pytest.mark.parametrize(
    "bad",
    [
        with_change(inject=[{"action": "rm -rf", "name": "x"}]),  # not in the vocabulary
        with_change(inject=[{"action": "scale", "kind": "statefulset", "name": "postgres"}]),
        with_change(inject=[{"action": "scale", "kind": "secret", "name": "x", "replicas": 0}]),
        with_change(recovery=[{"check": "http_status", "path": "/", "status": 200, "x": 1}]),
        with_change(
            recovery=[{"check": "ready_replicas", "kind": "deployment", "name": "a"}]
        ),  # needs equals or at_least
        with_change(symptoms=[]),
        with_change(time_limit_minutes=0),
        with_change(root_cause={"options": ["a", "b"], "correct": 5}),
        with_change(title=""),
    ],
)
def test_invalid_scenarios_are_rejected(bad):
    with pytest.raises(ScenarioError):
        parse(bad)


def test_duplicate_ids_are_rejected(tmp_path):
    for name in ("a.yaml", "b.yaml"):
        (tmp_path / name).write_text(
            "id: same\ntitle: T\nbriefing: B\nexplanation: E\ntime_limit_minutes: 5\n"
            "inject: [{action: set_load, level: 1}]\n"
            "symptoms: [{check: http_status, path: /, status: 503}]\n"
            "recovery: [{check: http_status, path: /, status: 200}]\n"
            "root_cause: {options: [a, b], correct: 0}\n",
            encoding="utf-8",
        )
    with pytest.raises(ScenarioError, match="duplicate"):
        scenarios.load_all(tmp_path)
