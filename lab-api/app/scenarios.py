"""Incident scenarios: loaded from YAML, validated against a fixed vocabulary.

A scenario can only combine the actions and checks listed here; each is
implemented in Python (app/actions.py). A YAML file cannot run arbitrary
commands or touch objects the Lab API's RBAC doesn't allow.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

SCENARIO_DIR = Path(__file__).resolve().parent.parent / "scenarios"

# name -> required parameters (beyond "action"/"check")
ACTIONS = {
    "set_image": {"name", "container", "image"},
    "scale": {"kind", "name", "replicas"},
    "patch_hpa_max": {"name", "max"},
    "set_load": {"level"},
}
CHECKS = {
    "ready_replicas": {"kind", "name"},  # plus exactly one of: equals, at_least
    "pod_waiting_reason": {"selector", "reasons"},
    "deployment_rolled_out": {"name"},
    "deployment_image": {"name", "container", "image"},
    "hpa_max_at_least": {"name", "value"},
    "hpa_replicas_above": {"name", "value"},
    "hpa_at_max": {"name"},
    "http_status": {"path", "status"},
}
KINDS = {"deployment", "statefulset"}
OPTIONAL = {"says", "label", "equals", "at_least"}


class ScenarioError(ValueError):
    pass


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    briefing: str
    time_limit_minutes: int
    inject: tuple[dict, ...]
    symptoms: tuple[dict, ...]
    recovery: tuple[dict, ...]
    hints: tuple[str, ...]
    root_cause_options: tuple[str, ...]
    root_cause_correct: int
    explanation: str
    uses_load: bool

    def public(self) -> dict:
        """What the dashboard may see before a run: no hints, no answer."""
        return {
            "id": self.id,
            "title": self.title,
            "briefing": self.briefing,
            "time_limit_minutes": self.time_limit_minutes,
            "hint_count": len(self.hints),
        }


def _check_steps(sid: str, section: str, steps, vocab: dict, key: str) -> tuple[dict, ...]:
    if not isinstance(steps, list) or not steps:
        raise ScenarioError(f"{sid}: '{section}' must be a non-empty list")
    out = []
    for i, step in enumerate(steps):
        where = f"{sid}: {section}[{i}]"
        if not isinstance(step, dict) or step.get(key) not in vocab:
            name = step.get(key) if isinstance(step, dict) else step
            raise ScenarioError(f"{where}: unknown {key} {name!r}")
        required = vocab[step[key]]
        missing = required - step.keys()
        unknown = step.keys() - required - OPTIONAL - {key}
        if missing:
            raise ScenarioError(f"{where}: missing {sorted(missing)}")
        if unknown:
            raise ScenarioError(f"{where}: unknown fields {sorted(unknown)}")
        if "kind" in step and step["kind"] not in KINDS:
            raise ScenarioError(f"{where}: kind must be one of {sorted(KINDS)}")
        if step[key] == "ready_replicas" and ("equals" in step) == ("at_least" in step):
            raise ScenarioError(f"{where}: ready_replicas needs exactly one of equals/at_least")
        out.append(dict(step))
    return tuple(out)


def parse(data: dict, source: str = "?") -> Scenario:
    if not isinstance(data, dict):
        raise ScenarioError(f"{source}: not a mapping")
    sid = data.get("id")
    if not isinstance(sid, str) or not sid:
        raise ScenarioError(f"{source}: missing id")
    for field in ("title", "briefing", "explanation"):
        if not isinstance(data.get(field), str) or not data[field].strip():
            raise ScenarioError(f"{sid}: missing {field}")
    limit = data.get("time_limit_minutes")
    if not isinstance(limit, int) or not 1 <= limit <= 60:
        raise ScenarioError(f"{sid}: time_limit_minutes must be 1-60")
    hints = data.get("hints") or []
    if not isinstance(hints, list) or not all(isinstance(h, str) for h in hints):
        raise ScenarioError(f"{sid}: hints must be a list of strings")
    rc = data.get("root_cause") or {}
    options = rc.get("options")
    correct = rc.get("correct")
    if not isinstance(options, list) or len(options) < 2:
        raise ScenarioError(f"{sid}: root_cause.options needs at least 2 options")
    if not isinstance(correct, int) or not 0 <= correct < len(options):
        raise ScenarioError(f"{sid}: root_cause.correct must index into options")
    inject = _check_steps(sid, "inject", data.get("inject"), ACTIONS, "action")
    return Scenario(
        id=sid,
        title=data["title"],
        briefing=data["briefing"].strip(),
        time_limit_minutes=limit,
        inject=inject,
        symptoms=_check_steps(sid, "symptoms", data.get("symptoms"), CHECKS, "check"),
        recovery=_check_steps(sid, "recovery", data.get("recovery"), CHECKS, "check"),
        hints=tuple(hints),
        root_cause_options=tuple(str(o) for o in options),
        root_cause_correct=correct,
        explanation=data["explanation"].strip(),
        uses_load=any(a["action"] == "set_load" for a in inject),
    )


def load_all(directory: Path = SCENARIO_DIR) -> dict[str, Scenario]:
    scenarios: dict[str, Scenario] = {}
    for path in sorted(directory.glob("*.yaml")):
        s = parse(yaml.safe_load(path.read_text(encoding="utf-8")), path.name)
        if s.id in scenarios:
            raise ScenarioError(f"duplicate scenario id {s.id}")
        scenarios[s.id] = s
    return scenarios
