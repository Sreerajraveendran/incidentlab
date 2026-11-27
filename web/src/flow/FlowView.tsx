import { useEffect, useMemo, useState } from "react";
import { buildSteps, NODE_LABEL, statusTone } from "./buildSteps";
import Diagram from "./Diagram";
import { sendFlow } from "./send";
import type { FlowRun, Method, Scenario } from "./types";
import { usePlayback } from "./usePlayback";

const METHODS: Method[] = ["GET", "POST", "PUT", "DELETE"];
const SPEEDS = [0.25, 0.5, 1, 2, 4];
const NETWORK_DELAYS = [0, 300, 1000, 2000];
const KNOWN_INSTANCES = ["app-1", "app-2", "app-3"];
const TOKEN_KEY = "incidentlab.apiToken";

function readToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

function Value({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span className="muted">—</span>;
  if (typeof value === "object") return <pre>{JSON.stringify(value, null, 2)}</pre>;
  return <code>{String(value)}</code>;
}

export default function FlowView() {
  const [method, setMethod] = useState<Method>("GET");
  const [itemId, setItemId] = useState(1);
  const [body, setBody] = useState('{"name": "hello from the visualizer"}');
  const [token, setToken] = useState(readToken);
  const [scenario, setScenario] = useState<Scenario>({ auth: "valid", cache: "auto", dbError: false, networkMs: 0 });
  const [runs, setRuns] = useState<FlowRun[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [tab, setTab] = useState<"step" | "request" | "response">("step");

  useEffect(() => {
    try {
      localStorage.setItem(TOKEN_KEY, token);
    } catch {
      /* storage unavailable: token lives for this page only */
    }
  }, [token]);

  const run = runs.find((r) => r.id === selected) ?? null;
  const steps = useMemo(() => (run ? buildSteps(run) : []), [run]);
  const pb = usePlayback(steps);
  const step = steps[pb.index];

  // On Kubernetes, draw the real app pods (refreshed every 5 s); on Compose, the fixed containers.
  const [clusterPods, setClusterPods] = useState<string[] | null>(null);
  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let inFlight: AbortController | null = null;
    // Scheduled after each answer (never overlapping), with a timeout; stops for
    // good on 404, which means there is no Lab API (Compose stage).
    const load = async () => {
      inFlight = new AbortController();
      const abort = setTimeout(() => inFlight?.abort(), 5000);
      try {
        const res = await fetch("/api/lab/cluster", { cache: "no-store", signal: inFlight.signal });
        if (res.status === 404) return;
        if (res.ok) {
          const s = (await res.json()) as { pods: { name: string; stage: string }[] };
          if (!stop) setClusterPods(s.pods.filter((p) => p.stage !== "Terminating").map((p) => p.name));
        }
      } catch {
        /* transient failure: keep the last pod list and try again */
      } finally {
        clearTimeout(abort);
      }
      if (!stop) timer = setTimeout(load, 5000);
    };
    load();
    return () => {
      stop = true;
      clearTimeout(timer);
      inFlight?.abort();
    };
  }, []);

  const activeInstance = run?.trace?.instance ?? run?.response?.headers["x-pod-name"] ?? null;
  const instances = useMemo(() => {
    const seen = new Set(clusterPods ?? KNOWN_INSTANCES);
    if (activeInstance) seen.add(activeInstance);
    return [...seen].sort();
  }, [clusterPods, activeInstance]);

  async function send() {
    setSending(true);
    let r;
    try {
      r = await sendFlow(method, itemId, body, scenario, token);
    } finally {
      // Never leave the Send button stuck disabled.
      setSending(false);
    }
    setRuns((prev) => [r, ...prev].slice(0, 12));
    setSelected(r.id);
    setTab("step");
    // A successful create gives us an id to read back next.
    const created = (r.response?.body as { result?: { id?: number } } | null)?.result?.id;
    if (method === "POST" && r.response?.status === 201 && created) {
      setItemId(created);
      setMethod("GET");
    }
  }

  const needsId = method !== "POST";
  const needsBody = method === "POST" || method === "PUT";
  const set = <K extends keyof Scenario>(k: K, v: Scenario[K]) => setScenario((s) => ({ ...s, [k]: v }));

  return (
    <div className="flow">
      <section className="panel builder" aria-label="Request builder">
        <h2>1 · Build a request</h2>
        <div className="row">
          <div className="seg" role="group" aria-label="HTTP method">
            {METHODS.map((m) => (
              <button key={m} className={m === method ? "on" : ""} onClick={() => setMethod(m)}>
                {m}
              </button>
            ))}
          </div>
          <code className="url">
            {needsId ? `/api/v1/items/` : "/api/v1/items"}
            {needsId && (
              <input
                aria-label="Item id"
                type="number"
                min={1}
                value={itemId}
                onChange={(e) => setItemId(Math.max(1, Number(e.target.value)))}
              />
            )}
          </code>
        </div>
        {needsBody && (
          <label>
            JSON body
            <textarea rows={2} value={body} onChange={(e) => setBody(e.target.value)} spellCheck={false} />
          </label>
        )}

        <h3>Scenario</h3>
        <div className="grid">
          <label>
            Auth (writes need a token)
            <select value={scenario.auth} onChange={(e) => set("auth", e.target.value as Scenario["auth"])}>
              <option value="valid">Valid token</option>
              <option value="wrong">Wrong token</option>
              <option value="none">No token</option>
            </select>
          </label>
          <label>
            API token (from your .env)
            <input type="password" value={token} placeholder="API_TOKEN" onChange={(e) => setToken(e.target.value)} />
          </label>
          <label>
            Cache (GET)
            <select value={scenario.cache} onChange={(e) => set("cache", e.target.value as Scenario["cache"])}>
              <option value="auto">Normal</option>
              <option value="force-hit">Force cache hit</option>
              <option value="force-miss">Force cache miss</option>
            </select>
          </label>
          <label>
            Slow network
            <select value={scenario.networkMs} onChange={(e) => set("networkMs", Number(e.target.value))}>
              {NETWORK_DELAYS.map((d) => (
                <option key={d} value={d}>
                  {d ? `+${d} ms` : "Off"}
                </option>
              ))}
            </select>
          </label>
          <label className="check">
            <input type="checkbox" checked={scenario.dbError} onChange={(e) => set("dbError", e.target.checked)} />
            Simulate database error
          </label>
        </div>
        <button className="primary" onClick={send} disabled={sending}>
          {sending ? "Sending…" : `Send ${method}`}
        </button>
        <p className="hint">
          A real request goes through NGINX to one app container. The app records each step with its real timing,
          and the animation replays them. Scenario switches only work while <code>SIMULATION_ENABLED=true</code>.
        </p>
      </section>

      <section className="panel stage" aria-label="Request flow">
        <div className="stage-head">
          <h2>2 · Watch it travel</h2>
          <div className="controls" role="group" aria-label="Playback">
            <button onClick={pb.restart} disabled={!steps.length} title="Restart">⏮</button>
            <button onClick={pb.stepBack} disabled={!steps.length || pb.index === 0} title="Step back">◀</button>
            {pb.playing ? (
              <button onClick={pb.pause} title="Pause">⏸</button>
            ) : (
              <button onClick={pb.play} disabled={!steps.length} title="Play">▶</button>
            )}
            <button onClick={pb.stepForward} disabled={!steps.length} title="Step forward">▶|</button>
            <label className="speed">
              Speed
              <select value={pb.speed} onChange={(e) => pb.setSpeed(Number(e.target.value))}>
                {SPEEDS.map((s) => (
                  <option key={s} value={s}>
                    {s}×
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>
        <Diagram
          steps={steps}
          index={pb.index}
          progress={pb.progress}
          instances={instances}
          activeInstance={activeInstance}
          method={run?.request.method ?? method}
          responseStatus={run?.response?.status ?? null}
        />
        {steps.length > 0 ? (
          <ol className="timeline">
            {steps.map((s, i) => (
              <li
                key={i}
                className={`${i === pb.index ? "current" : ""} ${i > pb.index ? "future" : ""} tone-${statusTone(s.status)}`}
              >
                <span className="dot" />
                {s.title}
              </li>
            ))}
          </ol>
        ) : (
          <p className="empty">Send a request to see its path.</p>
        )}
      </section>

      <section className="panel inspector" aria-label="Inspector">
        <div className="tabs" role="tablist">
          {(["step", "request", "response"] as const).map((t) => (
            <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
              {t === "step" ? "Step details" : t === "request" ? "Request" : "Response"}
            </button>
          ))}
        </div>
        {!run && <p className="empty">Nothing to inspect yet.</p>}
        {run && tab === "step" && step && (
          <div>
            <h3>
              Step {pb.index + 1} of {steps.length}: {step.title}
            </h3>
            <p>
              <span className="badge">{step.focus === "app" ? activeInstance : NODE_LABEL[step.focus]}</span>{" "}
              <span className={`badge tone-${statusTone(step.status)}`}>{step.status}</span>{" "}
              {step.simulated && <span className="badge tone-warn">simulated</span>}{" "}
              {step.durationMs !== null && <span className="muted">{step.durationMs.toFixed(1)} ms measured</span>}
            </p>
            <dl>
              {Object.entries(step.detail).map(([k, v]) => (
                <div key={k}>
                  <dt>{k}</dt>
                  <dd>
                    <Value value={v} />
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        )}
        {run && tab === "request" && (
          <dl>
            <div>
              <dt>line</dt>
              <dd>
                <code>
                  {run.request.method} {run.request.url}
                </code>
              </dd>
            </div>
            <div>
              <dt>headers</dt>
              <dd>
                <Value value={run.request.headers} />
              </dd>
            </div>
            <div>
              <dt>body</dt>
              <dd>
                <Value value={run.request.body} />
              </dd>
            </div>
          </dl>
        )}
        {run && tab === "response" && (
          <dl>
            <div>
              <dt>status</dt>
              <dd>
                <Value value={run.response?.status ?? run.networkError} />
              </dd>
            </div>
            <div>
              <dt>headers</dt>
              <dd>
                <Value value={run.response?.headers} />
              </dd>
            </div>
            <div>
              <dt>body</dt>
              <dd>
                <Value value={run.response?.body} />
              </dd>
            </div>
          </dl>
        )}
      </section>

      <section className="panel logs" aria-label="Processing log">
        <h2>Processing log</h2>
        <ol className="log">
          {steps.slice(0, pb.index + 1).map((s, i) => (
            <li key={i} className={`tone-${statusTone(s.status)}`}>
              <span className="muted">{String(i + 1).padStart(2, "0")}</span> {s.log}
            </li>
          ))}
        </ol>
      </section>

      <section className="panel history" aria-label="History">
        <h2>History</h2>
        {runs.length === 0 ? (
          <p className="empty">No requests yet.</p>
        ) : (
          <ul>
            {runs.map((r) => {
              const status = r.response?.status ?? 0;
              return (
                <li key={r.id}>
                  <button className={r.id === selected ? "on" : ""} onClick={() => setSelected(r.id)}>
                    <span className={`badge tone-${status && status < 400 ? "good" : "bad"}`}>{status || "ERR"}</span>{" "}
                    {r.request.method} {r.request.url}
                    <span className="muted">
                      {" "}
                      · {r.trace?.instance ?? "—"} · {r.browserMs.toFixed(0)} ms
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
