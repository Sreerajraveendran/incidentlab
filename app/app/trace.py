"""Step-by-step record of what one request did inside the app.

Each step has the node it happened on (app, cache, db, network), what it was,
its outcome and its real duration. The dashboard replays these steps as an
animation, so nothing it shows is invented.
"""

import time


class Trace:
    def __init__(self, method: str, path: str, instance: str, version: str):
        self.method = method
        self.path = path
        self.instance = instance
        self.version = version
        self._t0 = time.perf_counter()
        self.steps: list[dict] = []
        self.scenario: dict = {}

    def _now_ms(self) -> float:
        return (time.perf_counter() - self._t0) * 1000

    def begin(self, node: str, name: str, **detail) -> dict:
        step = {
            "node": node,
            "name": name,
            "status": "running",
            "started_ms": round(self._now_ms(), 2),
            "duration_ms": None,
            "detail": detail,
        }
        self.steps.append(step)
        return step

    def end(self, step: dict, status: str = "ok", **detail) -> None:
        step["status"] = status
        step["duration_ms"] = round(self._now_ms() - step["started_ms"], 2)
        step["detail"].update(detail)

    def db_ms(self) -> float | None:
        times = [s["duration_ms"] for s in self.steps if s["node"] == "db" and s["duration_ms"]]
        return sum(times) if times else None

    def to_dict(self, status_code: int) -> dict:
        return {
            "instance": self.instance,
            "version": self.version,
            "method": self.method,
            "path": self.path,
            "status_code": status_code,
            "total_ms": round(self._now_ms(), 2),
            "scenario": self.scenario,
            "steps": self.steps,
        }
