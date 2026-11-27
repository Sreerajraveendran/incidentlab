"""Where runs are kept: a ConfigMap, not Postgres.

The db-down scenario stops Postgres, so incident tooling that kept its state
there would lose it during that very incident. A ConfigMap lives in the
Kubernetes API (etcd), which every scenario leaves alone, and survives Lab API
restarts. Small by design: the active run plus the last HISTORY_LIMIT runs.
"""

import copy
import json
import os

from app import cluster

STATE_CONFIGMAP = os.getenv("STATE_CONFIGMAP", "incidentlab-lab-state")
HISTORY_LIMIT = 30


class MemoryStore:
    """Same interface, in memory: for tests and for running without a cluster."""

    def __init__(self):
        self.active: dict | None = None
        self.history: list[dict] = []

    def load(self) -> tuple[dict | None, list[dict]]:
        return copy.deepcopy(self.active), copy.deepcopy(self.history)

    def save(self, active: dict | None, history: list[dict]) -> None:
        self.active = copy.deepcopy(active)
        self.history = copy.deepcopy(history[-HISTORY_LIMIT:])


class ConfigMapStore:
    """Blocking calls; the engine runs them in a thread.

    The ConfigMap itself is created from Git (deploy/k8s/base/lab-api.yaml);
    Argo CD ignores its data (ignoreDifferences), so it never resets a run.
    """

    def __init__(self):
        from kubernetes import client

        self.core = client.CoreV1Api()

    def load(self) -> tuple[dict | None, list[dict]]:
        cm = self.core.read_namespaced_config_map(STATE_CONFIGMAP, cluster.NAMESPACE)
        data = cm.data or {}
        active = json.loads(data["active"]) if data.get("active") else None
        history = json.loads(data["history"]) if data.get("history") else []
        return active, history

    def save(self, active: dict | None, history: list[dict]) -> None:
        body = {
            "data": {
                "active": json.dumps(active) if active else "",
                "history": json.dumps(history[-HISTORY_LIMIT:]),
            }
        }
        self.core.patch_namespaced_config_map(STATE_CONFIGMAP, cluster.NAMESPACE, body)
