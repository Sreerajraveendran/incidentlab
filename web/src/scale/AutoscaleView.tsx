import { useEffect, useMemo, useRef, useState } from "react";
import Chart from "./Chart";
import { describe, formula, LOOP } from "./derive";
import type { Pod, Sample, Snapshot } from "./types";

const POLL_MS = 2000;
const HISTORY_S = 300;
const GONE_MS = 4000; // how long a removed pod stays on screen, fading out
const TOKEN_KEY = "minicloud.apiToken";
// Requests/s per level and the pod count to expect. Estimates from a measured
// ~32m CPU per req/s against the HPA's 50m-per-pod target.
const LEVELS = [
  { label: "Off", hint: "0 req/s · back to 2 pods" },
  { label: "Light", hint: "4 req/s · ≈ 3 pods" },
  { label: "Medium", hint: "6 req/s · ≈ 4 pods" },
  { label: "Heavy", hint: "12 req/s · 6 pods (max)" },
];

// "not-k8s": no management API at all (404, e.g. the Compose stage).
// "error": the API exists but the latest poll failed (it may recover).
type Status = "loading" | "ok" | "not-k8s" | "error";

const POLL_TIMEOUT_MS = 5000;

// mgmt-api answers 503 with {"error": ..., "detail": ...}; show the detail.
async function describeFailure(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (body?.detail) return `HTTP ${res.status}: ${body.detail}`;
    if (body?.error) return `HTTP ${res.status}: ${body.error}`;
  } catch {
    /* not JSON (e.g. an ingress-nginx error page) */
  }
  return `HTTP ${res.status}`;
}

function readToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

const ago = (iso: string | null, now: number) => {
  if (!iso) return "";
  const s = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  return s < 60 ? `${s}s ago` : `${Math.floor(s / 60)}m ${s % 60}s ago`;
};

const stageTone = (stage: string) =>
  stage === "Ready" ? "good" : stage === "Terminating" ? "bad" : stage === "gone" ? "gone" : "warn";

function PodCard({ pod, now }: { pod: Pod & { gone?: boolean }; now: number }) {
  const stage = pod.gone ? "gone" : pod.stage;
  const pct = pod.cpu_pct_of_request;
  return (
    <div className={`pod tone-${stageTone(stage)}`} title={pod.ip ?? ""}>
      <div className="pod-name">{pod.name.replace(/^minicloud-app-/, "")}</div>
      <div className="pod-stage">{pod.gone ? "removed" : pod.stage}</div>
      <div className="pod-cpu" aria-label="CPU against request">
        <span className="bar">
          <span className="fill" style={{ width: `${Math.min(100, (pct ?? 0) / 2.5)}%` }} />
        </span>
        <span className="num">{pct === null ? "CPU —" : `${pct}%`}</span>
      </div>
      <div className="pod-meta">
        {pod.restarts ? `${pod.restarts} restarts · ` : ""}
        {ago(pod.created, now)}
      </div>
    </div>
  );
}

export default function AutoscaleView() {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [status, setStatus] = useState<Status>("loading");
  // Kept apart so a successful poll doesn't wipe a load-button message.
  const [pollError, setPollError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [gone, setGone] = useState<(Pod & { gone: true; at: number })[]>([]);
  const [token, setToken] = useState(readToken);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(Date.now());
  const belowSince = useRef<number | null>(null);
  const prevPods = useRef<Pod[]>([]);

  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let inFlight: AbortController | null = null;

    // Each poll is scheduled only after the previous one finishes, and gives up
    // after POLL_TIMEOUT_MS, so slow answers never overlap or arrive out of order.
    async function poll() {
      inFlight = new AbortController();
      const abort = setTimeout(() => inFlight?.abort(), POLL_TIMEOUT_MS);
      try {
        const res = await fetch("/api/mgmt/cluster", { cache: "no-store", signal: inFlight.signal });
        if (res.status === 404) {
          if (!stop) setStatus("not-k8s");
          return; // nothing to poll on this stack
        }
        if (!res.ok) throw new Error(await describeFailure(res));
        const s: Snapshot = await res.json();
        if (stop) return;
        const t = Date.now();
        // Pods that vanished since the last poll fade out instead of blinking away.
        const names = new Set(s.pods.map((p) => p.name));
        const vanished = prevPods.current.filter((p) => !names.has(p.name));
        prevPods.current = s.pods;
        setGone((g) => [...g.filter((p) => t - p.at < GONE_MS), ...vanished.map((p) => ({ ...p, gone: true as const, at: t }))]);
        const h = s.hpa;
        if (h.current_cpu_pct !== null && h.target_cpu_pct !== null && h.current_cpu_pct < h.target_cpu_pct * 0.9) {
          belowSince.current ??= t;
        } else {
          belowSince.current = null;
        }
        setSamples((prev) => [
          ...prev.filter((x) => t - x.t <= HISTORY_S * 1000),
          {
            t,
            cpu: h.current_cpu_pct,
            replicas: s.pods.filter((p) => p.stage !== "Terminating").length,
            ready: s.pods.filter((p) => p.stage === "Ready").length,
          },
        ]);
        setSnap(s);
        setStatus("ok");
        setPollError(null);
      } catch (e) {
        if (stop) return;
        const msg =
          e instanceof DOMException && e.name === "AbortError"
            ? `no answer within ${POLL_TIMEOUT_MS / 1000} s`
            : e instanceof Error
              ? e.message
              : String(e);
        setStatus("error");
        setPollError(msg);
      } finally {
        clearTimeout(abort);
      }
      if (!stop) timer = setTimeout(poll, POLL_MS);
    }
    poll();
    const tick = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      stop = true;
      clearTimeout(timer);
      inFlight?.abort();
      clearInterval(tick);
    };
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(TOKEN_KEY, token);
    } catch {
      /* storage unavailable */
    }
  }, [token]);

  async function setLoad(level: number) {
    setBusy(true);
    try {
      const res = await fetch("/api/mgmt/load", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ level }),
      });
      if (res.status === 401) setLoadError("Load change refused: enter the API token");
      else if (!res.ok) setLoadError(`Load change failed: ${await describeFailure(res)}`);
      else setLoadError(null);
    } catch (e) {
      setLoadError(`Load change failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  const f = useMemo(() => (snap ? formula(snap.hpa) : null), [snap]);
  const info = snap ? describe(snap, belowSince.current, now) : null;

  if (status === "not-k8s")
    return (
      <section className="panel">
        <h2>Autoscaling needs the Kubernetes stage</h2>
        <p>
          Docker Compose has no autoscaler, so there are no new pods to show here. Start the kind cluster with{" "}
          <code>.\scripts\k8s-up.ps1</code> and open <code>http://127.0.0.1:8090</code>.
        </p>
      </section>
    );
  if (!snap)
    return status === "error" ? (
      <section className="panel">
        <h2>Cluster state unavailable</h2>
        <p>
          The management API didn't answer. Retrying every {POLL_MS / 1000} s. Check it with{" "}
          <code>kubectl -n minicloud get pods</code>.
        </p>
        <p className="failed">{pollError}</p>
      </section>
    ) : (
      <p className="empty">Loading cluster state…</p>
    );

  const workers = snap.nodes;
  const allPods: (Pod & { gone?: boolean })[] = [...snap.pods, ...gone];
  const h = snap.hpa;

  return (
    <div className="scale">
      <section className="panel load">
        <div className="load-row">
          <div>
            <h2>Load</h2>
            <p className="muted">
              {snap.load_rps} req/s: {snap.load_pods} load-generator pod{snap.load_pods === 1 ? "" : "s"} ×{" "}
              {snap.load_rate_per_pod} req/s to <code>/api/v1/cpu?ms=50</code> through the gateway
            </p>
          </div>
          <div className="levels">
            <div className="seg" role="group" aria-label="Load level">
              {LEVELS.map((l, i) => (
                <button
                  key={l.label}
                  className={snap.load_level === i ? "on" : ""}
                  disabled={busy}
                  title={l.hint}
                  onClick={() => setLoad(i)}
                >
                  {l.label}
                </button>
              ))}
            </div>
            <span className="muted">{LEVELS[snap.load_level]?.hint} (estimate)</span>
          </div>
          <label>
            API token
            <input type="password" value={token} placeholder="API_TOKEN" onChange={(e) => setToken(e.target.value)} />
          </label>
        </div>
        {loadError && <p className="failed">{loadError}</p>}
        {pollError && (
          <p className="failed">
            Showing the last good snapshot ({ago(snap.as_of, now)}); latest update failed: {pollError}
          </p>
        )}
      </section>

      <section className="panel topo" aria-label="Cluster">
        <div className="stage-head">
          <h2>Cluster · namespace {snap.namespace}</h2>
          <span className="muted">
            updated {ago(snap.as_of, now)}
            {!snap.metrics_available && " · CPU metrics not available yet"}
          </span>
        </div>
        <div className="flowline">
          <div className={`hop ${snap.load_level ? "busy" : ""}`}>
            <strong>Load generator</strong>
            <span>{snap.load_pods ? `${snap.load_rps} req/s` : "off"}</span>
          </div>
          <div className={`arrow ${snap.load_level ? "moving" : ""}`} />
          <div className="hop">
            <strong>API Gateway</strong>
            <span>NGINX · per request</span>
          </div>
          <div className={`arrow ${snap.load_level ? "moving" : ""}`} />
          <div className="hop">
            <strong>Ready pods</strong>
            <span>{snap.pods.filter((p) => p.stage === "Ready").length} in the EndpointSlice</span>
          </div>
        </div>
        <div className="nodes">
          {workers.map((n) => {
            const pods = allPods.filter((p) => p.node === n.name);
            const unscheduled = n.role === "worker" && n === workers.find((w) => w.role === "worker") ? allPods.filter((p) => !p.node) : [];
            return (
              <div key={n.name} className={`knode ${n.ready ? "" : "notready"}`}>
                <div className="knode-head">
                  <strong>{n.name}</strong>
                  <span className="muted">
                    {n.role}
                    {!n.schedulable && " · tainted: app pods don't run here"}
                    {!n.ready && " · NotReady"}
                  </span>
                </div>
                <div className="pods">
                  {[...pods, ...unscheduled].map((p) => (
                    <PodCard key={p.name + (p.gone ? "-gone" : "")} pod={p} now={now} />
                  ))}
                  {pods.length + unscheduled.length === 0 && <span className="muted">no app pods</span>}
                </div>
              </div>
            );
          })}
        </div>
        <p className="hint">
          Bars show each pod's CPU against its 100m request (full bar = the 250m limit). Terminal:{" "}
          <code>kubectl -n minicloud get pods -w</code> and <code>kubectl -n minicloud get hpa -w</code>
        </p>
      </section>

      <section className="panel hpa" aria-label="Autoscaler">
        <h2>HorizontalPodAutoscaler</h2>
        {info && (
          <div className={`phase phase-${info.phase}`}>
            <strong>{info.title}</strong>
            <span>{info.detail}</span>
            {info.countdownS !== null && <span className="countdown">≈ {info.countdownS}s until it may remove pods</span>}
          </div>
        )}
        <ol className="loop">
          {LOOP.map((s) => (
            <li key={s.id} className={info?.loopStep === s.id ? "current" : ""}>
              <strong>{s.label}</strong>
              <span>{s.hint}</span>
            </li>
          ))}
        </ol>
        <div className="formula">
          {f ? (
            <>
              <code>
                desired = ceil({f.current} × {f.cpu}% ÷ {f.target}%) = {f.raw}
              </code>
              <span className="muted">
                {f.withinTolerance
                  ? "within ±10% of target: no change"
                  : f.capped
                    ? `held at ${f.capped}Replicas = ${f.result}`
                    : `→ ${f.result} pods`}
              </span>
            </>
          ) : (
            <span className="muted">desired = ceil(current × CPU ÷ target): waiting for CPU metrics</span>
          )}
        </div>
        <dl className="hpa-facts">
          <div>
            <dt>CPU now / target</dt>
            <dd>
              {h.current_cpu_pct ?? "—"}% / {h.target_cpu_pct}%
            </dd>
          </div>
          <div>
            <dt>Replicas current / desired</dt>
            <dd>
              {h.current_replicas ?? "—"} / {h.desired_replicas ?? "—"}
            </dd>
          </div>
          <div>
            <dt>Min / max</dt>
            <dd>
              {h.min_replicas} / {h.max_replicas}
            </dd>
          </div>
          <div>
            <dt>Last scale</dt>
            <dd>{ago(h.last_scale_time, now) || "—"}</dd>
          </div>
        </dl>
      </section>

      <section className="panel chart-panel" aria-label="History">
        <h2>Last 5 minutes</h2>
        <div className="legend">
          <span className="key cpu" /> average CPU (% of request) <span className="key target" /> target{" "}
          <span className="key replicas" /> pods (right axis)
        </div>
        <Chart samples={samples} targetCpu={h.target_cpu_pct} maxReplicas={h.max_replicas} windowS={HISTORY_S} nowMs={now} />
      </section>

      <section className="panel events" aria-label="Kubernetes events">
        <h2>Kubernetes events</h2>
        <ol className="log">
          {snap.events.slice(0, 14).map((e, i) => (
            <li key={i} className={e.type === "Warning" ? "tone-bad" : e.reason === "SuccessfulRescale" ? "tone-warn" : ""}>
              <span className="muted">{ago(e.time, now).padEnd(8)}</span> {e.reason} · {e.object} · {e.message}
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
