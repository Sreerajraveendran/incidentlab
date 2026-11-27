import { useMemo, useState } from "react";
import { run, type Sample, tally } from "./traffic";

const ENDPOINTS = [
  { label: "/api/v1/info (instant)", path: "/api/v1/info" },
  { label: "/api/v1/slow?ms=200 (waits, no CPU)", path: "/api/v1/slow?ms=200" },
  { label: "/api/v1/cpu?ms=50 (burns CPU)", path: "/api/v1/cpu?ms=50" },
];
const COUNTS = [10, 30, 100];
const LOG_ROWS = 15;

export default function TrafficView() {
  const [endpoint, setEndpoint] = useState(ENDPOINTS[0].path);
  const [count, setCount] = useState(30);
  const [concurrency, setConcurrency] = useState(3);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [running, setRunning] = useState(false);
  const [finishedAt, setFinishedAt] = useState<number | null>(null);

  const stats = useMemo(() => tally(samples), [samples]);

  async function send() {
    setRunning(true);
    setSamples([]);
    try {
      await run(endpoint, count, concurrency, (s) => setSamples((prev) => [...prev, s]));
    } finally {
      // Never leave the Send button stuck disabled.
      setRunning(false);
      setFinishedAt(Date.now());
    }
  }

  return (
    <>
      <p className="note">
        Live measurements: each request goes from this browser through NGINX to one app container,
        which names itself in the <code>X-Pod-Name</code> header. Nothing here is sample data.
      </p>

      <section className="controls" aria-label="Load controls">
        <label>
          Endpoint
          <select value={endpoint} onChange={(e) => setEndpoint(e.target.value)} disabled={running}>
            {ENDPOINTS.map((e) => (
              <option key={e.path} value={e.path}>
                {e.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Requests
          <select value={count} onChange={(e) => setCount(Number(e.target.value))} disabled={running}>
            {COUNTS.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </label>
        <label>
          In flight
          <input
            type="number"
            min={1}
            max={10}
            value={concurrency}
            onChange={(e) => setConcurrency(Math.min(10, Math.max(1, Number(e.target.value))))}
            disabled={running}
          />
        </label>
        <button onClick={send} disabled={running}>
          {running ? `Sending… ${samples.length}/${count}` : "Send requests"}
        </button>
      </section>

      <section aria-label="Distribution">
        <h2>Requests per instance</h2>
        {stats.total === 0 ? (
          <p className="empty">No requests sent yet.</p>
        ) : (
          <>
            <ul className="bars">
              {stats.instances.map((i) => (
                <li key={i.instance}>
                  <span className="name">{i.instance}</span>
                  <span className="track">
                    <span className="fill" style={{ width: `${i.share * 100}%` }} />
                  </span>
                  <span className="num">
                    {i.count} · {(i.share * 100).toFixed(0)}% · {i.avgMs.toFixed(0)} ms avg
                  </span>
                </li>
              ))}
            </ul>
            <p className={stats.failed ? "failed" : "muted"}>
              {stats.failed} of {stats.total} failed (no instance answered or 5xx)
              {finishedAt && !running ? ` · finished ${new Date(finishedAt).toLocaleTimeString()}` : ""}
            </p>
          </>
        )}
        <p className="hint">
          Same view from the terminal: <code>docker compose logs nginx</code> shows{" "}
          <code>upstream=</code> for every request.
        </p>
      </section>

      <section aria-label="Request log">
        <h2>Last {LOG_ROWS} requests</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Request ID</th>
                <th>Instance</th>
                <th>Status</th>
                <th>Browser ms</th>
                <th>Server-Timing</th>
              </tr>
            </thead>
            <tbody>
              {samples
                .slice(-LOG_ROWS)
                .reverse()
                .map((s) => (
                  <tr key={s.requestId}>
                    <td>
                      <code>{s.requestId.slice(0, 8)}</code>
                    </td>
                    <td>{s.instance ?? "—"}</td>
                    <td className={s.status >= 200 && s.status < 400 ? "" : "failed"}>
                      {s.status || "network error"}
                    </td>
                    <td>{s.ms.toFixed(0)}</td>
                    <td>
                      <code>{s.serverTiming ?? "—"}</code>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
