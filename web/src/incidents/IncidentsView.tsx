import { useCallback, useEffect, useState } from "react";
import { useToken } from "../token";
import {
  clock,
  elapsed,
  EVENT_LABEL,
  eventTone,
  focusRun,
  needsAnswer,
  remaining,
  scoreBreakdown,
  stateLabel,
} from "./timeline";
import type { Run, Runs, ScenarioInfo } from "./types";

const POLL_MS = 3000;
const POLL_TIMEOUT_MS = 5000;

type Status = "loading" | "ok" | "not-k8s" | "error";

async function failure(res: Response): Promise<string> {
  try {
    const body = await res.json();
    return body?.detail ?? body?.error ?? `HTTP ${res.status}`;
  } catch {
    return `HTTP ${res.status}`;
  }
}

const time = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

export default function IncidentsView() {
  const [scenarios, setScenarios] = useState<ScenarioInfo[]>([]);
  const [runs, setRuns] = useState<Runs>({ active: null, history: [] });
  const [status, setStatus] = useState<Status>("loading");
  const [pollError, setPollError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [choice, setChoice] = useState<number | null>(null);
  const [token, setToken] = useToken();
  const [now, setNow] = useState(Date.now());

  // Same pattern as the Autoscaling view: one poll at a time, each with a timeout.
  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let inFlight: AbortController | null = null;

    async function poll() {
      inFlight = new AbortController();
      const abort = setTimeout(() => inFlight?.abort(), POLL_TIMEOUT_MS);
      try {
        const res = await fetch("/api/lab/runs", { cache: "no-store", signal: inFlight.signal });
        if (res.status === 404) {
          if (!stop) setStatus("not-k8s");
          return;
        }
        if (!res.ok) throw new Error(await failure(res));
        const data: Runs = await res.json();
        if (stop) return;
        setRuns(data);
        setStatus("ok");
        setPollError(null);
      } catch (e) {
        if (stop) return;
        setStatus((s) => (s === "loading" ? "error" : s));
        setPollError(
          e instanceof DOMException && e.name === "AbortError"
            ? `no answer within ${POLL_TIMEOUT_MS / 1000} s`
            : e instanceof Error
              ? e.message
              : String(e),
        );
      } finally {
        clearTimeout(abort);
      }
      if (!stop) timer = setTimeout(poll, POLL_MS);
    }

    fetch("/api/lab/scenarios", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : { scenarios: [] }))
      .then((d) => !stop && setScenarios(d.scenarios ?? []))
      .catch(() => {});
    poll();
    const tick = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      stop = true;
      clearTimeout(timer);
      inFlight?.abort();
      clearInterval(tick);
    };
  }, []);

  const command = useCallback(
    async (path: string, body?: unknown): Promise<Run | null> => {
      setBusy(true);
      setActionError(null);
      try {
        const res = await fetch(`/api/lab${path}`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
          body: body === undefined ? undefined : JSON.stringify(body),
        });
        if (res.status === 401) {
          setActionError("Refused: enter the API token (API_TOKEN in deploy/k8s/base/secrets.env).");
          return null;
        }
        if (!res.ok) {
          setActionError(await failure(res));
          return null;
        }
        const run: Run = await res.json();
        // Show the change at once instead of waiting for the next poll.
        setRuns((prev) => {
          const history = prev.history.map((r) => (r.id === run.id ? run : r));
          const live = ["injecting", "active", "cleanup"].includes(run.state);
          if (live) return { active: run, history };
          if (!history.some((r) => r.id === run.id)) history.unshift(run);
          return { active: prev.active?.id === run.id ? null : prev.active, history };
        });
        return run;
      } catch (e) {
        setActionError(e instanceof Error ? e.message : String(e));
        return null;
      } finally {
        setBusy(false);
      }
    },
    [token],
  );

  async function start(id: string) {
    const run = await command("/runs", { scenario_id: id });
    if (run) {
      setSelected(run.id);
      setChoice(null);
    }
  }

  if (status === "not-k8s")
    return (
      <section className="panel">
        <h2>Incidents need the Kubernetes stage</h2>
        <p>
          The scenario engine runs in the Lab API on the kind cluster. Start it with <code>.\scripts\k8s-up.ps1</code>{" "}
          and open <code>http://127.0.0.1:8090</code>.
        </p>
      </section>
    );
  if (status === "loading") return <p className="muted">Loading incidents…</p>;
  if (status === "error")
    return (
      <section className="panel">
        <h2>Scenario engine unavailable</h2>
        <p className="failed">{pollError}</p>
        <p>
          Check the Lab API: <code>kubectl -n incidentlab logs deploy/incidentlab-api --tail=30</code>
        </p>
      </section>
    );

  const active = runs.active;
  const run = focusRun(active, runs.history, selected);

  return (
    <div className="incidents">
      <section className="panel inc-intro">
        <div>
          <h2>Incident drills</h2>
          <p className="muted">
            Start a scenario: the lab breaks the system on purpose. Investigate and fix it with <code>kubectl</code> in
            a terminal. The lab checks the real cluster state and says when it has recovered.
          </p>
        </div>
        <label className="inc-token">
          API token
          <input type="password" value={token} placeholder="API_TOKEN" onChange={(e) => setToken(e.target.value)} />
        </label>
      </section>

      {actionError && <p className="failed">{actionError}</p>}
      {pollError && <p className="failed">Latest update failed: {pollError} (retrying)</p>}

      {!active && (
        <section className="inc-cards" aria-label="Scenarios">
          {scenarios.map((s) => (
            <article key={s.id} className="panel inc-card">
              <h3>{s.title}</h3>
              <p>{s.briefing}</p>
              <p className="muted">
                Time limit {s.time_limit_minutes} min · {s.hint_count} hints (−10 points each)
              </p>
              <button disabled={busy} onClick={() => start(s.id)}>
                Start incident
              </button>
            </article>
          ))}
        </section>
      )}

      {run && <RunPanel run={run} now={now} busy={busy} command={command} choice={choice} setChoice={setChoice} />}

      {runs.history.length > 0 && (
        <section className="panel">
          <h2>History</h2>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Started</th>
                  <th>Scenario</th>
                  <th>Outcome</th>
                  <th>Time to recover</th>
                  <th>Hints</th>
                  <th>Root cause</th>
                  <th>Score</th>
                </tr>
              </thead>
              <tbody>
                {runs.history.map((r) => (
                  <tr
                    key={r.id}
                    className={run?.id === r.id ? "inc-picked" : ""}
                    onClick={() => setSelected(r.id)}
                    style={{ cursor: "pointer" }}
                  >
                    <td>{new Date(r.started_at).toLocaleString()}</td>
                    <td>{r.scenario.title ?? r.scenario_id}</td>
                    <td>{r.outcome === "resolved" ? "Resolved" : `Ended: ${r.abort_reason ?? "—"}`}</td>
                    <td>{r.seconds_to_recover === null ? "—" : clock(r.seconds_to_recover)}</td>
                    <td>{r.hints_used}</td>
                    <td>{r.root_cause_correct === null ? "—" : r.root_cause_correct ? "correct" : "wrong"}</td>
                    <td>{r.score ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}

interface RunPanelProps {
  run: Run;
  now: number;
  busy: boolean;
  command: (path: string, body?: unknown) => Promise<Run | null>;
  choice: number | null;
  setChoice: (c: number | null) => void;
}

function RunPanel({ run, now, busy, command, choice, setChoice }: RunPanelProps) {
  const live = run.state === "active" || run.state === "injecting";
  const left = remaining(run, now);
  const hintCount = run.scenario.hint_count ?? 0;
  const breakdown = scoreBreakdown(run);

  return (
    <section className="panel inc-run" aria-label="Incident">
      <header className="inc-head">
        <div>
          <h2>{run.scenario.title ?? run.scenario_id}</h2>
          <span className={`badge tone-${run.state === "resolved" ? "good" : run.state === "aborted" ? "bad" : "warn"}`}>
            {stateLabel(run)}
          </span>
          {run.argo_paused && (
            <span className="badge tone-warn" title="Self-heal would undo the fault and your own fix">
              Argo CD auto-sync paused
            </span>
          )}
        </div>
        <div className="inc-clock" aria-label="Time since the fault">
          <strong>{clock(elapsed(run, now))}</strong>
          {left !== null && <span className="muted"> · {clock(left)} left</span>}
        </div>
      </header>

      {run.scenario.briefing && <p className="inc-brief">{run.scenario.briefing}</p>}
      {run.last_error && (
        <p className="failed">
          {run.last_error} If it persists, resume Argo CD by hand:{" "}
          <code>
            kubectl -n argocd patch application incidentlab --type merge -p
            {" '{\"spec\":{\"syncPolicy\":{\"automated\":{\"enabled\":true}}}}'"}
          </code>
        </p>
      )}

      <div className="inc-grid">
        <div>
          <h3>Recovery checks</h3>
          {run.checks.length === 0 ? (
            <p className="muted">Evaluated every 5 s once the fault is in place.</p>
          ) : (
            <ul className="inc-checks">
              {run.checks.map((c) => (
                <li key={c.label} className={c.ok ? "tone-good" : "tone-bad"}>
                  <span aria-hidden>{c.ok ? "✓" : "✗"}</span> {c.label}
                  <span className="muted"> · {c.detail}</span>
                </li>
              ))}
            </ul>
          )}
          {live && (
            <p className="muted">
              All checks must pass for 15 s in a row. Start with <code>kubectl -n incidentlab get pods</code>.
            </p>
          )}

          {(run.hints.length > 0 || live) && <h3>Hints</h3>}
          <ol className="inc-hints">
            {run.hints.map((h) => (
              <li key={h}>{h}</li>
            ))}
          </ol>
          {live && (
            <div className="row">
              <button disabled={busy || run.hints_used >= hintCount} onClick={() => command(`/runs/${run.id}/hint`)}>
                {run.hints_used >= hintCount ? "No more hints" : `Get a hint (−10) · ${hintCount - run.hints_used} left`}
              </button>
              <button
                className="danger"
                disabled={busy}
                onClick={() => window.confirm("Give up and restore the system from Git?") && command(`/runs/${run.id}/abort`)}
              >
                Give up
              </button>
            </div>
          )}

          {needsAnswer(run) && (
            <form
              className="inc-answer"
              onSubmit={(e) => {
                e.preventDefault();
                if (choice !== null) command(`/runs/${run.id}/root-cause`, { choice }).then(() => setChoice(null));
              }}
            >
              <h3>What was the root cause? (+20 if right)</h3>
              {run.root_cause_options!.map((o, i) => (
                <label key={o}>
                  <input type="radio" name="root-cause" checked={choice === i} onChange={() => setChoice(i)} /> {o}
                </label>
              ))}
              <button type="submit" disabled={busy || choice === null}>
                Submit answer
              </button>
            </form>
          )}

          {run.root_cause_answer !== undefined && run.root_cause_options && (
            <div className="inc-result">
              <h3>Root cause</h3>
              <p>
                {run.root_cause_correct ? "Correct: " : "Not quite. The answer: "}
                <strong>{run.root_cause_options[run.root_cause_answer]}</strong>
              </p>
              <p>{run.explanation}</p>
            </div>
          )}

          {breakdown.length > 0 && (
            <div className="inc-result">
              <h3>Score {run.score}</h3>
              <ul className="inc-score">
                {breakdown.map((l) => (
                  <li key={l.label}>
                    <span>{l.label}</span>
                    <span className={l.points < 0 ? "tone-bad" : ""}>{l.points > 0 ? `+${l.points}` : l.points}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        <div>
          <h3>Timeline</h3>
          <ol className="inc-timeline">
            {run.events.map((e, i) => (
              <li key={i} className={`tone-${eventTone(e)}`}>
                <time>{time(e.at)}</time>
                <span className="inc-kind">{EVENT_LABEL[e.kind] ?? e.kind}</span>
                <span>{e.message}</span>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </section>
  );
}
