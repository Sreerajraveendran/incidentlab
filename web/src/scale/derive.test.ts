import { describe as group, expect, it } from "vitest";
import { describe, formula } from "./derive";
import type { Hpa, Pod, Snapshot } from "./types";

const hpa = (over: Partial<Hpa> = {}): Hpa => ({
  name: "minicloud-app",
  min_replicas: 2,
  max_replicas: 6,
  target_cpu_pct: 50,
  current_cpu_pct: 50,
  current_replicas: 2,
  desired_replicas: 2,
  last_scale_time: null,
  scale_down_window_s: 60,
  conditions: [],
  ...over,
});

const pod = (name: string, stage: string): Pod => ({
  name,
  stage,
  node: "minicloud-worker",
  ip: null,
  created: null,
  conditions: {},
  restarts: 0,
  cpu_m: null,
  cpu_request_m: 100,
  cpu_pct_of_request: null,
  mem_mib: null,
});

const snap = (h: Hpa, pods: Pod[] = [pod("a", "Ready"), pod("b", "Ready")]): Snapshot => ({
  as_of: "",
  namespace: "minicloud",
  metrics_available: true,
  nodes: [],
  pods,
  hpa: h,
  load_level: 0,
  load_pods: 0,
  load_rps: 0,
  load_rate_per_pod: 2,
  events: [],
});

group("formula", () => {
  it("computes the HPA's desired replicas", () => {
    expect(formula(hpa({ current_cpu_pct: 120 }))).toMatchObject({ raw: 5, result: 5, capped: null });
  });
  it("caps at maxReplicas", () => {
    expect(formula(hpa({ current_cpu_pct: 250 }))).toMatchObject({ raw: 10, result: 6, capped: "max" });
  });
  it("holds at minReplicas", () => {
    expect(formula(hpa({ current_cpu_pct: 5 }))).toMatchObject({ raw: 1, result: 2, capped: "min" });
  });
  it("ignores changes within the 10% tolerance", () => {
    expect(formula(hpa({ current_cpu_pct: 54 }))).toMatchObject({ withinTolerance: true, result: 2 });
  });
  it("is unknown without metrics", () => {
    expect(formula(hpa({ current_cpu_pct: null }))).toBeNull();
  });
});

group("describe", () => {
  it("scaling up when desired > current", () => {
    expect(describe(snap(hpa({ current_cpu_pct: 180, desired_replicas: 6 })), null, 0).phase).toBe("scaling-up");
  });
  it("new pods starting take priority", () => {
    const s = snap(hpa({ current_cpu_pct: 180 }), [pod("a", "Ready"), pod("b", "ContainerCreating")]);
    expect(describe(s, null, 0)).toMatchObject({ phase: "starting", loopStep: "start" });
  });
  it("running but not ready is warming", () => {
    expect(describe(snap(hpa(), [pod("a", "Ready"), pod("b", "Running")]), null, 0).phase).toBe("warming");
  });
  it("terminating pods mean scaling down", () => {
    expect(describe(snap(hpa(), [pod("a", "Ready"), pod("b", "Terminating")]), null, 0).phase).toBe("scaling-down");
  });
  it("cooling counts down the stabilization window", () => {
    const s = snap(hpa({ current_cpu_pct: 5, current_replicas: 4 }));
    expect(describe(s, 0, 20_000)).toMatchObject({ phase: "cooling", countdownS: 40 });
  });
  it("at max when still hot at maxReplicas", () => {
    expect(describe(snap(hpa({ current_cpu_pct: 200, current_replicas: 6, desired_replicas: 6 })), null, 0).phase).toBe("at-max");
  });
  it("no metrics is explicit, not zero", () => {
    expect(describe(snap(hpa({ current_cpu_pct: null })), null, 0).phase).toBe("no-metrics");
  });
  it("stable otherwise", () => {
    expect(describe(snap(hpa()), null, 0).phase).toBe("stable");
  });
});
