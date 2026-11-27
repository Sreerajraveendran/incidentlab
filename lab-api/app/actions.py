"""What scenarios do to the cluster (actions) and how they look at it (checks).

KubePort makes the Kubernetes and HTTP calls. evaluate() turns what the port
returns into ok/not-ok plus a sentence; it is plain logic, so tests run it
against a fake port without a cluster.
"""

import os
import urllib.error
import urllib.request

from app import cluster

NAMESPACE = cluster.NAMESPACE
ARGO_NAMESPACE = os.getenv("ARGO_NAMESPACE", "argocd")
ARGO_APP = os.getenv("ARGO_APP", "incidentlab")
APP_URL = os.getenv("TARGET_APP_URL", f"http://incidentlab-app.{NAMESPACE}.svc:8000")


class KubePort:
    """Blocking calls; the engine runs them in a thread."""

    def __init__(self):
        from kubernetes import client

        self.client = client
        self.api = client.ApiClient()
        self.apps = client.AppsV1Api(self.api)
        self.core = client.CoreV1Api(self.api)
        self.hpa_api = client.AutoscalingV2Api(self.api)
        self.custom = client.CustomObjectsApi(self.api)
        self.plain = self.api.sanitize_for_serialization

    # ---- reads -------------------------------------------------------------
    def workload(self, kind: str, name: str) -> dict:
        read = (
            self.apps.read_namespaced_deployment
            if kind == "deployment"
            else self.apps.read_namespaced_stateful_set
        )
        return self.plain(read(name, NAMESPACE))

    def hpa(self, name: str) -> dict:
        return self.plain(self.hpa_api.read_namespaced_horizontal_pod_autoscaler(name, NAMESPACE))

    def pods(self, selector: str) -> list[dict]:
        return self.plain(self.core.list_namespaced_pod(NAMESPACE, label_selector=selector))[
            "items"
        ]

    def http_status(self, path: str) -> int | None:
        """Status code of GET APP_URL+path; None if the app can't be reached."""
        try:
            with urllib.request.urlopen(APP_URL + path, timeout=3) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
        except OSError:
            return None

    # ---- actions -----------------------------------------------------------
    def run_action(self, step: dict) -> str:
        """Apply one inject step; returns a sentence for the timeline. Idempotent."""
        a = step["action"]
        if a == "set_image":
            patch = {
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [{"name": step["container"], "image": step["image"]}]
                        }
                    }
                }
            }
            self.apps.patch_namespaced_deployment(step["name"], NAMESPACE, patch)
            return f"Deployed image {step['image']} to deployment/{step['name']}"
        if a == "scale":
            body = {"spec": {"replicas": step["replicas"]}}
            if step["kind"] == "deployment":
                self.apps.patch_namespaced_deployment_scale(step["name"], NAMESPACE, body)
            else:
                self.apps.patch_namespaced_stateful_set_scale(step["name"], NAMESPACE, body)
            return f"Scaled {step['kind']}/{step['name']} to {step['replicas']}"
        if a == "patch_hpa_max":
            self.hpa_api.patch_namespaced_horizontal_pod_autoscaler(
                step["name"], NAMESPACE, {"spec": {"maxReplicas": step["max"]}}
            )
            return f"Set hpa/{step['name']} maxReplicas to {step['max']}"
        if a == "set_load":
            cluster.set_load(step["level"])
            return f"Load generator set to level {step['level']}"
        raise ValueError(f"unknown action {a}")

    def set_load_off(self) -> None:
        cluster.set_load(0)

    # ---- Argo CD -----------------------------------------------------------
    def argo_auto_sync(self, enabled: bool) -> None:
        """Pause/resume automated sync; on resume also ask for an immediate refresh."""
        body: dict = {"spec": {"syncPolicy": {"automated": {"enabled": enabled}}}}
        if enabled:
            body["metadata"] = {"annotations": {"argocd.argoproj.io/refresh": "normal"}}
        self.custom.patch_namespaced_custom_object(
            "argoproj.io", "v1alpha1", ARGO_NAMESPACE, "applications", ARGO_APP, body
        )


# ---- checks (pure) -----------------------------------------------------------
def _label(step: dict) -> str:
    if step.get("label"):
        return step["label"]
    c = step["check"]
    if c == "ready_replicas":
        cmp = f"= {step['equals']}" if "equals" in step else f">= {step['at_least']}"
        return f"{step['kind']}/{step['name']} ready replicas {cmp}"
    if c == "pod_waiting_reason":
        return f"pods ({step['selector']}) waiting with {'/'.join(step['reasons'])}"
    if c == "deployment_rolled_out":
        return f"deployment/{step['name']} fully rolled out"
    if c == "deployment_image":
        return f"deployment/{step['name']} runs {step['image']}"
    if c == "hpa_max_at_least":
        return f"hpa/{step['name']} maxReplicas >= {step['value']}"
    if c == "hpa_replicas_above":
        return f"hpa/{step['name']} replicas > {step['value']}"
    if c == "hpa_at_max":
        return f"hpa/{step['name']} stuck at its maximum while CPU is above target"
    if c == "http_status":
        return f"GET {step['path']} returns {step['status']}"
    return c


def evaluate(step: dict, port) -> dict:
    """{label, ok, detail} for one check. Any error counts as not ok."""
    label = _label(step)
    try:
        ok, detail = _evaluate(step, port)
    except Exception as exc:  # e.g. object missing, API unreachable
        ok, detail = False, f"could not check: {str(exc)[:120]}"
    return {"label": label, "ok": ok, "detail": detail, "says": step.get("says") or label}


def _evaluate(step: dict, port) -> tuple[bool, str]:
    c = step["check"]
    if c == "ready_replicas":
        w = port.workload(step["kind"], step["name"])
        ready = (w.get("status") or {}).get("readyReplicas") or 0
        ok = ready == step["equals"] if "equals" in step else ready >= step["at_least"]
        return ok, f"{ready} ready"
    if c == "pod_waiting_reason":
        hits = []
        for p in port.pods(step["selector"]):
            for cs in (p.get("status") or {}).get("containerStatuses") or []:
                reason = ((cs.get("state") or {}).get("waiting") or {}).get("reason")
                if reason in step["reasons"]:
                    hits.append(f"{p['metadata']['name']}: {reason}")
        return bool(hits), "; ".join(hits) or "none"
    if c == "deployment_rolled_out":
        d = port.workload("deployment", step["name"])
        spec, st = d.get("spec") or {}, d.get("status") or {}
        want = spec.get("replicas", 1)
        updated, ready = st.get("updatedReplicas") or 0, st.get("readyReplicas") or 0
        total = st.get("replicas") or 0
        current = (st.get("observedGeneration") or 0) >= (d["metadata"].get("generation") or 0)
        ok = current and updated == want and ready == want and total == want
        return ok, f"{updated}/{want} updated, {ready} ready, {total} total"
    if c == "deployment_image":
        d = port.workload("deployment", step["name"])
        containers = d["spec"]["template"]["spec"]["containers"]
        image = next((x["image"] for x in containers if x["name"] == step["container"]), None)
        return image == step["image"], f"image {image}"
    if c in ("hpa_max_at_least", "hpa_replicas_above", "hpa_at_max"):
        h = port.hpa(step["name"])
        spec, st = h.get("spec") or {}, h.get("status") or {}
        mx, cur = spec.get("maxReplicas"), st.get("currentReplicas") or 0
        if c == "hpa_max_at_least":
            return (mx or 0) >= step["value"], f"maxReplicas {mx}"
        if c == "hpa_replicas_above":
            return cur > step["value"], f"{cur} replicas"
        shaped = cluster.shape_hpa(h)
        cpu, target = shaped["current_cpu_pct"], shaped["target_cpu_pct"]
        ok = cur >= (mx or 0) and cpu is not None and target is not None and cpu > target
        return ok, f"{cur}/{mx} replicas, CPU {cpu}% (target {target}%)"
    if c == "http_status":
        status = port.http_status(step["path"])
        return status == step["status"], f"HTTP {status if status is not None else 'unreachable'}"
    raise ValueError(f"unknown check {c}")
