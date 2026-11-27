"""The scenario engine: a run's life from injecting the fault to cleanup.

    injecting -> active -> cleanup -> resolved | aborted

- injecting: pause Argo CD auto-sync, then apply the scenario's inject steps.
  Steps are idempotent, so a restart in the middle simply repeats them.
- active: every tick, record symptoms the first time they show and evaluate
  the recovery checks. All of them passing on RECOVERY_STREAK ticks in a row
  (15 s) means the incident is resolved. The time limit or "give up" aborts.
- cleanup: turn the load generator off (Argo CD ignores its replica count),
  resume Argo CD auto-sync so Git becomes the truth again. Retried every tick
  until it works; only then does the run end.

Everything mutating goes through one lock: the tick loop and the HTTP
handlers run in different threads. The run is saved after every change, so a
Lab API restart continues where it stopped.
"""

import logging
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from app.actions import evaluate
from app.scenarios import Scenario

log = logging.getLogger("lab-api.engine")

TICK_SECONDS = 5
RECOVERY_STREAK = 3
LIVE_STATES = ("injecting", "active", "cleanup")


class EngineError(Exception):
    status = 400


class NotFound(EngineError):
    status = 404


class Conflict(EngineError):
    status = 409


def base_score(seconds_to_recover: float, hints_used: int) -> int:
    """100, minus 1 per 30 s after the first 2 minutes, minus 10 per hint; at least 10."""
    slow = max(0, int(seconds_to_recover - 120) // 30)
    return max(10, 100 - slow - 10 * hints_used)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


class Engine:
    def __init__(
        self,
        scenarios: dict[str, Scenario],
        store,
        port,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.scenarios = scenarios
        self.store = store
        self.port = port
        self.now = now
        self.lock = threading.Lock()
        self.active, self.history = store.load()

    # ---- helpers -------------------------------------------------------------
    def _event(self, run: dict, kind: str, message: str) -> None:
        run["events"].append({"at": _iso(self.now()), "kind": kind, "message": message})

    def _save(self) -> None:
        self.store.save(self.active, self.history)

    def _scenario(self, run: dict) -> Scenario:
        return self.scenarios[run["scenario_id"]]

    def _find(self, run_id: str) -> dict:
        if self.active and self.active["id"] == run_id:
            return self.active
        for run in self.history:
            if run["id"] == run_id:
                return run
        raise NotFound("no such run")

    def view(self, run: dict) -> dict:
        """The run as the dashboard sees it: hints only once used, answer only once given."""
        s = self.scenarios.get(run["scenario_id"])
        out = {k: v for k, v in run.items() if k not in ("symptoms_seen", "ok_streak")}
        out["scenario"] = s.public() if s else {"id": run["scenario_id"]}
        out["hints"] = list(s.hints[: run["hints_used"]]) if s else []
        if s and run["outcome"] == "resolved":
            out["root_cause_options"] = list(s.root_cause_options)
            if run["root_cause_choice"] is not None:
                out["root_cause_answer"] = s.root_cause_correct
                out["explanation"] = s.explanation
        return out

    # ---- queries -------------------------------------------------------------
    def list_runs(self) -> dict:
        with self.lock:
            return {
                "active": self.view(self.active) if self.active else None,
                "history": [self.view(r) for r in reversed(self.history)],
            }

    def get_run(self, run_id: str) -> dict:
        with self.lock:
            return self.view(self._find(run_id))

    # ---- commands ------------------------------------------------------------
    def start(self, scenario_id: str) -> dict:
        with self.lock:
            if scenario_id not in self.scenarios:
                raise NotFound("no such scenario")
            if self.active:
                raise Conflict("another incident is still running")
            s = self.scenarios[scenario_id]
            self.active = {
                "id": uuid.uuid4().hex[:12],
                "scenario_id": scenario_id,
                "state": "injecting",
                "outcome": None,
                "started_at": _iso(self.now()),
                "active_at": None,
                "resolved_at": None,
                "ended_at": None,
                "seconds_to_recover": None,
                "hints_used": 0,
                "symptoms_seen": [],
                "ok_streak": 0,
                "checks": [],
                "argo_paused": False,
                "abort_reason": None,
                "last_error": None,
                "score_base": None,
                "root_cause_choice": None,
                "root_cause_correct": None,
                "score": None,
                "events": [],
            }
            run = self.active
            self._event(run, "info", f"Incident started: {s.title}")
            self._save()
            self._inject(run)  # on failure this ends the run, so keep our own reference
            return self.view(run)

    def hint(self, run_id: str) -> dict:
        with self.lock:
            run = self._find(run_id)
            if run["state"] != "active":
                raise Conflict("hints are only available while the incident is active")
            s = self._scenario(run)
            if run["hints_used"] >= len(s.hints):
                raise Conflict("no more hints")
            run["hints_used"] += 1
            self._event(run, "hint", f"Hint {run['hints_used']} used (-10 points)")
            self._save()
            return self.view(run)

    def abort(self, run_id: str, reason: str = "given up") -> dict:
        with self.lock:
            run = self._find(run_id)
            if run["state"] not in ("injecting", "active"):
                raise Conflict("this run is not in progress")
            self._begin_cleanup(run, "aborted", reason)
            self._cleanup(run)
            return self.view(run)

    def answer(self, run_id: str, choice: int) -> dict:
        with self.lock:
            run = self._find(run_id)
            s = self._scenario(run)
            if run["outcome"] != "resolved":
                raise Conflict("answer the root cause after the incident is resolved")
            if run["root_cause_choice"] is not None:
                raise Conflict("root cause already answered")
            if not 0 <= choice < len(s.root_cause_options):
                raise EngineError("no such option")
            correct = choice == s.root_cause_correct
            run["root_cause_choice"] = choice
            run["root_cause_correct"] = correct
            run["score"] = run["score_base"] + (20 if correct else 0)
            self._event(
                run,
                "answer",
                f"Root cause: {s.root_cause_options[choice]} "
                + ("(correct, +20)" if correct else "(not quite)"),
            )
            self._save()
            return self.view(run)

    # ---- the loop ------------------------------------------------------------
    def tick(self) -> None:
        with self.lock:
            run = self.active
            if run is None:
                return
            if run["state"] == "injecting":
                self._inject(run)
            elif run["state"] == "active":
                self._watch(run)
            elif run["state"] == "cleanup":
                self._cleanup(run)

    def _inject(self, run: dict) -> None:
        s = self._scenario(run)
        try:
            if not run["argo_paused"]:
                self.port.argo_auto_sync(False)
                run["argo_paused"] = True
                self._event(
                    run,
                    "info",
                    "Argo CD auto-sync paused: the cluster may now differ from Git "
                    "(OutOfSync). That difference is your incident.",
                )
            for step in s.inject:
                self._event(run, "inject", self.port.run_action(step))
        except Exception as exc:
            log.warning("inject failed for %s: %s", run["id"], exc)
            self._event(run, "error", f"Could not inject the fault: {str(exc)[:200]}")
            self._begin_cleanup(run, "aborted", "injection failed")
            self._cleanup(run)
            return
        run["state"] = "active"
        run["active_at"] = _iso(self.now())
        self._event(run, "info", s.briefing)
        self._save()

    def _watch(self, run: dict) -> None:
        s = self._scenario(run)
        started = datetime.fromisoformat(run["active_at"])
        if (self.now() - started).total_seconds() > s.time_limit_minutes * 60:
            self._begin_cleanup(run, "aborted", "time limit reached")
            self._cleanup(run)
            return
        for i, step in enumerate(s.symptoms):
            if i in run["symptoms_seen"]:
                continue
            result = evaluate(step, self.port)
            if result["ok"]:
                run["symptoms_seen"].append(i)
                self._event(run, "symptom", f"{result['says']} ({result['detail']})")
        run["checks"] = [evaluate(step, self.port) for step in s.recovery]
        if all(c["ok"] for c in run["checks"]):
            run["ok_streak"] += 1
        else:
            run["ok_streak"] = 0
        if run["ok_streak"] >= RECOVERY_STREAK:
            now = self.now()
            run["resolved_at"] = _iso(now)
            run["seconds_to_recover"] = round((now - started).total_seconds())
            run["score_base"] = base_score(run["seconds_to_recover"], run["hints_used"])
            run["score"] = run["score_base"]
            mins, secs = divmod(run["seconds_to_recover"], 60)
            self._event(
                run,
                "resolved",
                f"Recovered: every check passed for {RECOVERY_STREAK * TICK_SECONDS} s. "
                f"Time to recover {mins}:{secs:02d}, score {run['score_base']}.",
            )
            self._begin_cleanup(run, "resolved", None)
            self._cleanup(run)
            return
        self._save()

    def _begin_cleanup(self, run: dict, outcome: str, reason: str | None) -> None:
        run["state"] = "cleanup"
        run["outcome"] = outcome
        run["abort_reason"] = reason
        if outcome == "aborted":
            self._event(run, "aborted", f"Incident ended: {reason}")
        self._save()

    def _cleanup(self, run: dict) -> None:
        s = self._scenario(run)
        try:
            if s.uses_load:
                self.port.set_load_off()
            if run["argo_paused"]:
                self.port.argo_auto_sync(True)
                run["argo_paused"] = False
        except Exception as exc:
            message = f"Cleanup failed, retrying every {TICK_SECONDS} s: {str(exc)[:200]}"
            if run["last_error"] != message:
                run["last_error"] = message
                self._event(run, "error", message)
            log.warning("cleanup failed for %s: %s", run["id"], exc)
            self._save()
            return
        run["last_error"] = None
        self._event(
            run,
            "info",
            "Argo CD auto-sync resumed: Git is the source of truth again and reverts "
            "anything that still differs.",
        )
        run["state"] = run["outcome"]
        run["ended_at"] = _iso(self.now())
        self.history.append(run)
        self.active = None
        self._save()
