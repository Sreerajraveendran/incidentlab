import type { Hpa, Snapshot } from "./types";

// The HPA does nothing while the ratio is within 10% of 1 (its default tolerance).
const TOLERANCE = 0.1;

export interface Formula {
  current: number;
  cpu: number;
  target: number;
  raw: number;
  withinTolerance: boolean;
  result: number; // after tolerance and min/max
  capped: "min" | "max" | null;
}

/** desired = ceil(current × currentCPU / targetCPU), as the HPA computes it. */
export function formula(h: Hpa): Formula | null {
  const { current_replicas: current, current_cpu_pct: cpu, target_cpu_pct: target } = h;
  if (current == null || cpu == null || target == null || current === 0) return null;
  const ratio = cpu / target;
  const withinTolerance = Math.abs(ratio - 1) <= TOLERANCE;
  const raw = Math.ceil(current * ratio);
  const wanted = withinTolerance ? current : raw;
  const result = Math.min(h.max_replicas, Math.max(h.min_replicas, wanted));
  const capped = wanted > h.max_replicas ? "max" : wanted < h.min_replicas ? "min" : null;
  return { current, cpu, target, raw, withinTolerance, result, capped };
}

export type Phase =
  | "no-metrics"
  | "scaling-down"
  | "starting"
  | "warming"
  | "scaling-up"
  | "at-max"
  | "cooling"
  | "stable";

// The loop the diagram highlights, in order.
export const LOOP = [
  { id: "measure", label: "Measure CPU", hint: "kubelet → metrics-server, every ~15 s" },
  { id: "decide", label: "HPA decides", hint: "compares average CPU with the target" },
  { id: "scale", label: "Change replicas", hint: "Deployment → ReplicaSet" },
  { id: "start", label: "Pods start", hint: "Pending → ContainerCreating → Running" },
  { id: "ready", label: "Ready → traffic", hint: "readiness passes, gateway routes to it" },
] as const;

export interface PhaseInfo {
  phase: Phase;
  title: string;
  detail: string;
  loopStep: (typeof LOOP)[number]["id"] | null;
  countdownS: number | null;
}

/** What the autoscaler is doing right now, in words, from one snapshot. */
export function describe(s: Snapshot, belowTargetSinceMs: number | null, nowMs: number): PhaseInfo {
  const h = s.hpa;
  const pods = s.pods;
  const terminating = pods.filter((p) => p.stage === "Terminating").length;
  const starting = pods.filter((p) => p.stage === "Pending" || p.stage === "ContainerCreating").length;
  const warming = pods.filter((p) => p.stage === "Running").length;
  const current = h.current_replicas ?? pods.length;
  const desired = h.desired_replicas ?? current;
  const cpu = h.current_cpu_pct;
  const target = h.target_cpu_pct;

  if (terminating)
    return {
      phase: "scaling-down",
      title: `Scaling down: removing ${terminating} pod${terminating > 1 ? "s" : ""}`,
      detail: "Removed from the endpoints first, then SIGTERM, a 5 s preStop pause and a graceful exit.",
      loopStep: "scale",
      countdownS: null,
    };
  if (starting)
    return {
      phase: "starting",
      title: `${starting} new pod${starting > 1 ? "s" : ""} starting`,
      detail: "Scheduled onto a node; the container is being created. No traffic yet.",
      loopStep: "start",
      countdownS: null,
    };
  if (warming)
    return {
      phase: "warming",
      title: `${warming} pod${warming > 1 ? "s" : ""} waiting for readiness`,
      detail: "Running, but the readiness probe hasn't passed: not in the EndpointSlice yet.",
      loopStep: "ready",
      countdownS: null,
    };
  if (cpu == null || target == null)
    return {
      phase: "no-metrics",
      title: "Waiting for CPU metrics",
      detail: "metrics-server needs ~30–60 s after start before the HPA can see CPU. Shown as unknown, not 0.",
      loopStep: "measure",
      countdownS: null,
    };
  if (desired > current)
    return {
      phase: "scaling-up",
      title: `Scaling up: ${current} → ${desired} pods`,
      detail: `Average CPU ${cpu}% is above the ${target}% target.`,
      loopStep: "scale",
      countdownS: null,
    };
  if (cpu > target * (1 + TOLERANCE) && current >= h.max_replicas)
    return {
      phase: "at-max",
      title: `At the maximum of ${h.max_replicas} pods`,
      detail: `CPU ${cpu}% is still above target, but maxReplicas stops further scaling.`,
      loopStep: "decide",
      countdownS: null,
    };
  if (cpu < target * (1 - TOLERANCE) && current > h.min_replicas) {
    const elapsed = belowTargetSinceMs === null ? 0 : (nowMs - belowTargetSinceMs) / 1000;
    return {
      phase: "cooling",
      title: "CPU below target: waiting before removing pods",
      detail: `The HPA waits a ${h.scale_down_window_s} s stabilization window so a short dip doesn't remove pods.`,
      loopStep: "decide",
      countdownS: Math.max(0, Math.round(h.scale_down_window_s - elapsed)),
    };
  }
  return {
    phase: "stable",
    title: "Stable",
    detail: `CPU ${cpu}% vs target ${target}% (±10% tolerance): no change needed.`,
    loopStep: "measure",
    countdownS: null,
  };
}
