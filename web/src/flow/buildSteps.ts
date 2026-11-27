import type { FlowRun, FlowStep, NodeId, StepStatus, TraceStep } from "./types";

const ms = (seconds: string | undefined): number | null => {
  // NGINX reports seconds ("0.002"), or "-" / a comma list when it retried.
  if (!seconds) return null;
  const last = seconds.split(",").pop()!.trim();
  const n = Number(last);
  return Number.isFinite(n) ? n * 1000 : null;
};

const fmt = (n: number | null) => (n === null ? "" : ` (${n.toFixed(1)} ms)`);

function returnStep(s: TraceStep, node: "cache" | "db"): FlowStep {
  const d = s.detail;
  let title: string;
  if (s.status === "error") title = `${node === "db" ? "Database" : "Cache"} error goes back to the app`;
  else if (node === "cache" && s.status === "hit") title = "Cache returns the value (hit)";
  else if (node === "cache" && s.status === "miss") title = "Cache: key not found (miss)";
  else if (node === "db") title = `Database returns ${d.rows ?? 0} row(s)`;
  else title = "Cache acknowledges";
  return {
    from: node,
    to: "app",
    focus: "app",
    title,
    status: s.status,
    durationMs: null,
    simulated: Boolean(d.simulated),
    detail: { result: s.status, ...(d.error ? { error: d.error } : {}), ...(d.rows !== undefined ? { rows: d.rows } : {}) },
    log: `${node} → app · ${s.status}`,
    phase: "request",
  };
}

function traceSteps(steps: TraceStep[]): FlowStep[] {
  const out: FlowStep[] = [];
  for (const s of steps) {
    const simulated = Boolean(s.detail.simulated) || s.status === "simulated";
    const line = `${s.node} · ${s.name} · ${s.status}${fmt(s.duration_ms)}`;
    if (s.node === "cache" || s.node === "db") {
      out.push({
        from: "app",
        to: s.node,
        focus: s.node,
        title: s.name,
        status: s.status,
        durationMs: s.duration_ms,
        simulated,
        detail: s.detail,
        log: line,
        phase: "request",
      });
      out.push(returnStep(s, s.node));
    } else {
      out.push({
        from: "app",
        to: "app",
        focus: "app",
        title: s.node === "network" ? `${s.name} — simulated delay` : s.name,
        status: s.status,
        durationMs: s.duration_ms,
        simulated,
        detail: s.detail,
        log: line,
        phase: "request",
      });
    }
  }
  return out;
}

const header = (run: FlowRun, name: string) => run.response?.headers[name.toLowerCase()];

/** Turn one measured request into the frames of its animation. */
export function buildSteps(run: FlowRun): FlowStep[] {
  const { request, response, trace } = run;
  const steps: FlowStep[] = [
    {
      from: "browser",
      to: "gateway",
      focus: "browser",
      title: `Browser sends ${request.method} ${request.url}`,
      status: "ok",
      durationMs: null,
      simulated: false,
      detail: { method: request.method, url: request.url, headers: request.headers, body: request.body },
      log: `browser → gateway · ${request.method} ${request.url}`,
      phase: "request",
    },
  ];

  if (!response) {
    steps.push({
      from: "gateway",
      to: "browser",
      focus: "browser",
      title: "No response: the request never reached the gateway",
      status: "error",
      durationMs: run.browserMs,
      simulated: false,
      detail: { error: run.networkError },
      log: `browser · network error · ${run.networkError}`,
      phase: "response",
    });
    return steps;
  }

  const instance = trace?.instance ?? header(run, "x-pod-name") ?? null;
  const upstream = header(run, "x-upstream");
  const upstreamStatus = header(run, "x-upstream-status");
  const retried = Boolean(upstream?.includes(","));
  const firstStatus = upstreamStatus?.split(",")[0].trim();

  if (!instance) {
    // NGINX answered by itself: no app instance was reachable.
    steps.push({
      from: "gateway",
      to: "browser",
      focus: "gateway",
      title: `Gateway has no healthy backend: ${response.status}`,
      status: "error",
      durationMs: run.browserMs,
      simulated: false,
      detail: { status: response.status, upstream, body: response.body },
      log: `gateway · no backend available · ${response.status}`,
      phase: "response",
    });
    return steps;
  }

  steps.push({
    from: "gateway",
    to: "app",
    focus: "gateway",
    title: retried
      ? `Gateway retried: first instance ${firstStatus === "504" ? "timed out" : `failed (${firstStatus})`}, ${instance} answered`
      : `Gateway routes to ${instance} (round-robin)`,
    status: retried ? "error" : "ok",
    durationMs: ms(header(run, "x-gateway-connect-time")),
    simulated: false,
    // The Compose gateway reports what it did in X-Upstream / X-Gateway-* headers.
    // ingress-nginx (Kubernetes) sends none of them, so those fields are left out
    // instead of being shown as empty or "undefined".
    detail: upstream
      ? {
          algorithm: "round-robin",
          chosen_instance: instance,
          upstream_address: upstream,
          upstream_status: upstreamStatus,
          ...(retried ? { why_retried: "the first instance could not be reached, so NGINX tried the next one" } : {}),
          connect_ms: ms(header(run, "x-gateway-connect-time")),
          request_id: header(run, "x-gateway-request-id"),
        }
      : {
          chosen_instance: instance,
          note: "ingress-nginx doesn't report its upstream or timings in response headers",
        },
    log: `gateway → ${instance}${upstream ? ` · upstream ${upstream}` : ""}${retried ? " (retry)" : ""}`,
    phase: "request",
  });

  if (trace) steps.push(...traceSteps(trace.steps));

  const ok = response.status < 400;
  const body = response.body as { result?: unknown } | null;
  steps.push({
    from: "app",
    to: "gateway",
    focus: "app",
    title: `${instance} sends response ${response.status}`,
    status: ok ? "ok" : "error",
    durationMs: trace?.total_ms ?? null,
    simulated: false,
    detail: {
      status: response.status,
      app_total_ms: trace?.total_ms,
      server_timing: header(run, "server-timing"),
      body: body && typeof body === "object" && "result" in body ? body.result : body,
    },
    log: `${instance} → gateway · ${response.status}`,
    phase: "response",
  });

  const headerMs = ms(header(run, "x-gateway-header-time"));
  steps.push({
    from: "gateway",
    to: "browser",
    focus: "gateway",
    title: "Gateway forwards the response",
    status: ok ? "ok" : "error",
    durationMs: headerMs,
    simulated: false,
    detail: { upstream_response_ms: headerMs, response_headers: response.headers },
    log: `gateway → browser · ${response.status}`,
    phase: "response",
  });

  const appMs = trace?.total_ms ?? null;
  steps.push({
    from: "browser",
    to: "browser",
    focus: "browser",
    title: `Browser receives ${response.status} in ${run.browserMs.toFixed(1)} ms`,
    status: ok ? "ok" : "error",
    durationMs: run.browserMs,
    simulated: false,
    detail: {
      total_ms: Number(run.browserMs.toFixed(1)),
      inside_app_ms: appMs,
      network_and_gateway_ms: appMs === null ? null : Number((run.browserMs - appMs).toFixed(1)),
    },
    log: `browser · done · ${response.status} · ${run.browserMs.toFixed(1)} ms`,
    phase: "response",
  });

  return steps;
}

export const NODE_LABEL: Record<NodeId, string> = {
  browser: "Browser",
  gateway: "API Gateway (NGINX)",
  app: "App server",
  cache: "Cache (Redis)",
  db: "Database (Postgres)",
};

export function statusTone(s: StepStatus): "good" | "warn" | "bad" | "neutral" {
  if (s === "ok" || s === "hit") return "good";
  if (s === "miss" || s === "simulated") return "warn";
  if (s === "error") return "bad";
  return "neutral";
}
