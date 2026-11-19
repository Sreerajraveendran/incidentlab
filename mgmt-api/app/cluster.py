"""Read-only view of the MiniCloud namespace for the autoscaling visualizer.

Fetching (Kubernetes API calls) is kept apart from shaping (plain dicts in,
plain dicts out) so the shaping logic is testable without a cluster.
"""

import os
from datetime import UTC, datetime

NAMESPACE = os.getenv("TARGET_NAMESPACE", "minicloud")
APP_SELECTOR = os.getenv("APP_SELECTOR", "app=minicloud-app")
HPA_NAME = os.getenv("HPA_NAME", "minicloud-app")
LOADGEN_DEPLOYMENT = os.getenv("LOADGEN_DEPLOYMENT", "minicloud-loadgen")
EVENT_LIMIT = 30

# Dashboard load level -> load-generator pods. Each pod sends LOADGEN_RATE req/s
# (must match RATE in loadgen.yaml), so Off/Light/Medium/Heavy = 0/4/6/12 req/s.
# Measured: ~32m CPU per req/s, HPA target 50m per pod -> about 2/3/4/6 app pods.
LEVEL_PODS = (0, 2, 3, 6)
LOADGEN_RATE = float(os.getenv("LOADGEN_RATE", "2"))


def level_for_pods(pods: int) -> int:
    """The highest level whose pod count is reached (e.g. after a manual kubectl scale)."""
    return max(i for i, n in enumerate(LEVEL_PODS) if pods >= n)


def shape_load(pods: int) -> dict:
    return {
        "load_level": level_for_pods(pods),
        "load_pods": pods,
        "load_rps": pods * LOADGEN_RATE,
        "load_rate_per_pod": LOADGEN_RATE,
    }


_UNITS = {"n": 1e-9, "u": 1e-6, "m": 1e-3}
_BYTES = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "K": 1e3, "M": 1e6, "G": 1e9}


def cpu_millicores(q: str | None) -> float | None:
    """'12345678n' / '250m' / '1' -> millicores."""
    if not q:
        return None
    if q[-1] in _UNITS:
        return float(q[:-1]) * _UNITS[q[-1]] * 1000
    return float(q) * 1000


def mem_mib(q: str | None) -> float | None:
    if not q:
        return None
    for suffix in ("Ki", "Mi", "Gi", "K", "M", "G"):
        if q.endswith(suffix):
            return float(q[: -len(suffix)]) * _BYTES[suffix] / 2**20
    return float(q) / 2**20


def _condition_times(pod: dict) -> dict:
    return {
        c["type"]: c.get("lastTransitionTime")
        for c in pod.get("status", {}).get("conditions") or []
        if c.get("status") == "True"
    }


def _stage(pod: dict) -> str:
    """One word for where the pod is in its life, as the diagram shows it."""
    meta, status = pod["metadata"], pod.get("status", {})
    if meta.get("deletionTimestamp"):
        return "Terminating"
    phase = status.get("phase", "Unknown")
    if phase != "Running":
        for cs in status.get("containerStatuses") or []:
            waiting = (cs.get("state") or {}).get("waiting")
            if waiting:
                return waiting.get("reason", phase)
        return phase
    ready = _condition_times(pod).get("Ready")
    return "Ready" if ready else "Running"


def shape_pod(pod: dict, usage: dict) -> dict:
    meta, spec, status = pod["metadata"], pod.get("spec", {}), pod.get("status", {})
    container = (spec.get("containers") or [{}])[0]
    requests = (container.get("resources") or {}).get("requests") or {}
    restarts = sum(cs.get("restartCount", 0) for cs in status.get("containerStatuses") or [])
    cpu = usage.get(meta["name"], {}).get("cpu")
    req = cpu_millicores(requests.get("cpu"))
    return {
        "name": meta["name"],
        "stage": _stage(pod),
        "node": spec.get("nodeName"),
        "ip": status.get("podIP"),
        "created": meta.get("creationTimestamp"),
        "conditions": _condition_times(pod),
        "restarts": restarts,
        "cpu_m": cpu,
        "cpu_request_m": req,
        "cpu_pct_of_request": round(cpu / req * 100) if cpu is not None and req else None,
        "mem_mib": usage.get(meta["name"], {}).get("mem"),
    }


def shape_usage(metrics: dict) -> dict:
    usage = {}
    for item in metrics.get("items", []):
        containers = item.get("containers", [])
        cpu = sum(cpu_millicores(c["usage"].get("cpu")) or 0 for c in containers)
        mem = sum(mem_mib(c["usage"].get("memory")) or 0 for c in containers)
        usage[item["metadata"]["name"]] = {"cpu": round(cpu, 1), "mem": round(mem, 1)}
    return usage


def shape_hpa(hpa: dict) -> dict:
    spec, status = hpa.get("spec", {}), hpa.get("status", {})
    target = None
    for m in spec.get("metrics") or []:
        if m.get("type") == "Resource" and m["resource"]["name"] == "cpu":
            target = m["resource"]["target"].get("averageUtilization")
    current = None
    for m in status.get("currentMetrics") or []:
        if m.get("type") == "Resource" and m["resource"]["name"] == "cpu":
            current = m["resource"]["current"].get("averageUtilization")
    behavior = spec.get("behavior") or {}
    return {
        "name": hpa["metadata"]["name"],
        "min_replicas": spec.get("minReplicas", 1),
        "max_replicas": spec.get("maxReplicas"),
        "target_cpu_pct": target,
        "current_cpu_pct": current,
        "current_replicas": status.get("currentReplicas"),
        "desired_replicas": status.get("desiredReplicas"),
        "last_scale_time": status.get("lastScaleTime"),
        "scale_down_window_s": (behavior.get("scaleDown") or {}).get(
            "stabilizationWindowSeconds", 300
        ),
        "conditions": [
            {
                "type": c["type"],
                "status": c["status"],
                "reason": c.get("reason"),
                "message": c.get("message"),
            }
            for c in status.get("conditions") or []
        ],
    }


def shape_nodes(nodes: dict) -> list[dict]:
    out = []
    for n in nodes.get("items", []):
        labels = n["metadata"].get("labels") or {}
        ready = any(
            c["type"] == "Ready" and c["status"] == "True"
            for c in n.get("status", {}).get("conditions") or []
        )
        out.append(
            {
                "name": n["metadata"]["name"],
                "role": "control-plane"
                if "node-role.kubernetes.io/control-plane" in labels
                else "worker",
                "ready": ready,
                "schedulable": not any(
                    t.get("effect") == "NoSchedule" for t in n.get("spec", {}).get("taints") or []
                ),
            }
        )
    return sorted(out, key=lambda n: (n["role"] != "control-plane", n["name"]))


def shape_events(events: dict) -> list[dict]:
    def when(e):
        return (
            e.get("lastTimestamp")
            or e.get("eventTime")
            or e["metadata"].get("creationTimestamp")
            or ""
        )

    items = sorted(events.get("items", []), key=when, reverse=True)[:EVENT_LIMIT]
    return [
        {
            "time": when(e),
            "type": e.get("type"),
            "reason": e.get("reason"),
            "object": f'{e["involvedObject"].get("kind")}/{e["involvedObject"].get("name")}',
            "message": e.get("message"),
            "count": e.get("count", 1),
        }
        for e in items
    ]


def fetch_snapshot() -> dict:
    """Call the Kubernetes API (blocking; run in a thread)."""
    from kubernetes import client

    api = client.ApiClient()
    core = client.CoreV1Api(api)
    plain = api.sanitize_for_serialization

    pods = plain(core.list_namespaced_pod(NAMESPACE, label_selector=APP_SELECTOR))
    try:
        metrics = client.CustomObjectsApi(api).list_namespaced_custom_object(
            "metrics.k8s.io", "v1beta1", NAMESPACE, "pods"
        )
        usage = shape_usage(metrics)
    except client.ApiException:
        usage = {}  # metrics-server not ready yet: shown as unavailable, never as 0
    hpa = plain(
        client.AutoscalingV2Api(api).read_namespaced_horizontal_pod_autoscaler(HPA_NAME, NAMESPACE)
    )
    loadgen = client.AppsV1Api(api).read_namespaced_deployment_scale(LOADGEN_DEPLOYMENT, NAMESPACE)

    return {
        "as_of": datetime.now(UTC).isoformat(),
        "namespace": NAMESPACE,
        "metrics_available": bool(usage),
        "nodes": shape_nodes(plain(core.list_node())),
        "pods": sorted(
            (shape_pod(p, usage) for p in pods.get("items", [])), key=lambda p: p["created"] or ""
        ),
        "hpa": shape_hpa(hpa),
        **shape_load(loadgen.spec.replicas or 0),
        "events": shape_events(plain(core.list_namespaced_event(NAMESPACE))),
    }


def set_load(level: int) -> None:
    from kubernetes import client

    client.AppsV1Api().patch_namespaced_deployment_scale(
        LOADGEN_DEPLOYMENT, NAMESPACE, {"spec": {"replicas": LEVEL_PODS[level]}}
    )
