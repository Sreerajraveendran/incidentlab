from app import cluster


def test_cpu_quantities():
    assert cluster.cpu_millicores("250m") == 250
    assert cluster.cpu_millicores("1") == 1000
    assert round(cluster.cpu_millicores("12345678n"), 2) == 12.35
    assert cluster.cpu_millicores(None) is None


def test_memory_quantities():
    assert cluster.mem_mib("1024Ki") == 1
    assert cluster.mem_mib("64Mi") == 64


def pod(name, phase="Running", ready=True, deleting=False, waiting=None, node="incidentlab-worker"):
    conditions = [{"type": "PodScheduled", "status": "True", "lastTransitionTime": "t0"}]
    if ready:
        conditions.append({"type": "Ready", "status": "True", "lastTransitionTime": "t2"})
    statuses = [{"restartCount": 1, "state": {"waiting": {"reason": waiting}} if waiting else {}}]
    return {
        "metadata": {
            "name": name,
            "creationTimestamp": "2026-11-08T10:00:00Z",
            **({"deletionTimestamp": "x"} if deleting else {}),
        },
        "spec": {"nodeName": node, "containers": [{"resources": {"requests": {"cpu": "100m"}}}]},
        "status": {
            "phase": phase,
            "podIP": "10.0.0.1",
            "conditions": conditions,
            "containerStatuses": statuses,
        },
    }


def test_pod_stages():
    assert cluster.shape_pod(pod("a"), {})["stage"] == "Ready"
    assert cluster.shape_pod(pod("a", ready=False), {})["stage"] == "Running"
    assert (
        cluster.shape_pod(pod("a", phase="Pending", ready=False, waiting="ContainerCreating"), {})[
            "stage"
        ]
        == "ContainerCreating"
    )
    assert cluster.shape_pod(pod("a", phase="Pending", ready=False), {})["stage"] == "Pending"
    assert cluster.shape_pod(pod("a", deleting=True), {})["stage"] == "Terminating"


def test_pod_cpu_against_request():
    shaped = cluster.shape_pod(pod("a"), {"a": {"cpu": 75.0, "mem": 40.0}})
    assert shaped["cpu_m"] == 75.0
    assert shaped["cpu_pct_of_request"] == 75
    assert shaped["restarts"] == 1


def test_missing_metrics_are_none_not_zero():
    shaped = cluster.shape_pod(pod("a"), {})
    assert shaped["cpu_m"] is None and shaped["cpu_pct_of_request"] is None


def test_usage_sums_containers():
    usage = cluster.shape_usage(
        {
            "items": [
                {
                    "metadata": {"name": "a"},
                    "containers": [
                        {"usage": {"cpu": "50m", "memory": "10Mi"}},
                        {"usage": {"cpu": "25m", "memory": "5Mi"}},
                    ],
                }
            ]
        }
    )
    assert usage == {"a": {"cpu": 75.0, "mem": 15.0}}


def test_hpa_shape():
    hpa = {
        "metadata": {"name": "incidentlab-app"},
        "spec": {
            "minReplicas": 2,
            "maxReplicas": 6,
            "metrics": [
                {
                    "type": "Resource",
                    "resource": {"name": "cpu", "target": {"averageUtilization": 50}},
                }
            ],
            "behavior": {"scaleDown": {"stabilizationWindowSeconds": 60}},
        },
        "status": {
            "currentReplicas": 2,
            "desiredReplicas": 4,
            "currentMetrics": [
                {
                    "type": "Resource",
                    "resource": {"name": "cpu", "current": {"averageUtilization": 180}},
                }
            ],
            "conditions": [{"type": "AbleToScale", "status": "True", "reason": "SucceededRescale"}],
        },
    }
    s = cluster.shape_hpa(hpa)
    assert (s["target_cpu_pct"], s["current_cpu_pct"]) == (50, 180)
    assert (s["current_replicas"], s["desired_replicas"], s["max_replicas"]) == (2, 4, 6)
    assert s["scale_down_window_s"] == 60
    assert s["conditions"][0]["reason"] == "SucceededRescale"


def test_nodes_control_plane_first_and_taint():
    nodes = cluster.shape_nodes(
        {
            "items": [
                {
                    "metadata": {"name": "w", "labels": {}},
                    "spec": {},
                    "status": {"conditions": [{"type": "Ready", "status": "True"}]},
                },
                {
                    "metadata": {
                        "name": "cp",
                        "labels": {"node-role.kubernetes.io/control-plane": ""},
                    },
                    "spec": {"taints": [{"effect": "NoSchedule"}]},
                    "status": {"conditions": [{"type": "Ready", "status": "True"}]},
                },
            ]
        }
    )
    assert [n["name"] for n in nodes] == ["cp", "w"]
    assert nodes[0]["schedulable"] is False and nodes[1]["schedulable"] is True


def test_events_newest_first():
    def ev(t, r):
        return {
            "lastTimestamp": t,
            "type": "Normal",
            "reason": r,
            "message": "m",
            "metadata": {},
            "involvedObject": {"kind": "Pod", "name": "p"},
        }

    out = cluster.shape_events(
        {"items": [ev("2026-01-01T00:00:01Z", "old"), ev("2026-01-01T00:00:09Z", "new")]}
    )
    assert [e["reason"] for e in out] == ["new", "old"]
    assert out[0]["object"] == "Pod/p"


def test_load_levels_map_to_pods_and_rate():
    assert cluster.LEVEL_PODS == (0, 2, 3, 6)
    assert cluster.shape_load(6) == {
        "load_level": 3,
        "load_pods": 6,
        "load_rps": 12.0,
        "load_rate_per_pod": 2.0,
    }
    assert cluster.shape_load(0)["load_level"] == 0
    # A manual `kubectl scale` to 4 pods reads as Medium (the highest level reached).
    assert cluster.level_for_pods(4) == 2
