import { statusTone } from "./buildSteps";
import type { FlowStep, NodeId } from "./types";

interface Props {
  steps: FlowStep[];
  index: number;
  progress: number;
  instances: string[]; // all known app instances, in display order
  activeInstance: string | null; // the one that served this request
  method: string;
  responseStatus: number | null;
}

interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

const W = 960;
const H = 400;
const FIXED: Record<Exclude<NodeId, "app">, Box> = {
  browser: { x: 90, y: 200, w: 140, h: 64 },
  gateway: { x: 290, y: 200, w: 170, h: 64 },
  cache: { x: 820, y: 110, w: 160, h: 64 },
  db: { x: 820, y: 290, w: 160, h: 64 },
};
const APP_X = 560;

const ease = (t: number) => (t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2);

export default function Diagram({ steps, index, progress, instances, activeInstance, method, responseStatus }: Props) {
  // Up to ~6 instances fit; spacing shrinks as the autoscaler adds pods.
  const gap = Math.min(76, 330 / Math.max(1, instances.length));
  const slot = (i: number) => H / 2 + (i - (instances.length - 1) / 2) * gap;
  // "incidentlab-app-66465d759c-dmjlj" -> "pod dmjlj"; Compose names stay as they are.
  const label = (name: string) => name.replace(/^incidentlab-app-[a-z0-9]+-/, "pod ");
  const appBox = (name: string): Box => ({
    x: APP_X,
    y: slot(Math.max(0, instances.indexOf(name))),
    w: 150,
    h: Math.min(52, gap - 6),
  });
  const box = (n: NodeId): Box => (n === "app" ? appBox(activeInstance ?? instances[0]) : FIXED[n]);

  const step = steps[index];
  const tone = step ? statusTone(step.status) : "neutral";
  const focusKey = step ? (step.focus === "app" ? `app:${activeInstance}` : step.focus) : null;
  const edgeKey = step && step.from !== step.to ? [step.from, step.to].sort().join("-") : null;

  // Packet position.
  let px = FIXED.browser.x;
  let py = FIXED.browser.y;
  if (step) {
    const a = box(step.from);
    const b = box(step.to);
    const t = ease(Math.min(1, progress));
    px = a.x + (b.x - a.x) * t;
    py = a.y + (b.y - a.y) * t;
  }
  const isResponse = step?.phase === "response";
  const packetLabel = isResponse && responseStatus ? String(responseStatus) : method;
  const packetTone = isResponse ? (responseStatus && responseStatus < 400 ? "good" : "bad") : "accent";

  const edgeClass = (k: string) => `edge${edgeKey === k ? " edge-active" : ""}`;
  const nodeClass = (k: string) => `node${focusKey === k ? ` node-focus tone-${tone}` : ""}`;

  const rect = (b: Box) => <rect x={b.x - b.w / 2} y={b.y - b.h / 2} width={b.w} height={b.h} rx={10} />;

  return (
    <svg className="diagram" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Request path through the system">
      {/* edges */}
      <line className={edgeClass("browser-gateway")} x1={FIXED.browser.x} y1={FIXED.browser.y} x2={FIXED.gateway.x} y2={FIXED.gateway.y} />
      {instances.map((name) => {
        const b = appBox(name);
        const mine = name === activeInstance;
        return (
          <g key={`e-${name}`} className={mine ? "" : "dim"}>
            <line className={mine ? edgeClass("app-gateway") : "edge"} x1={FIXED.gateway.x} y1={FIXED.gateway.y} x2={b.x} y2={b.y} />
            {/* Only the instance serving this request is drawn to the data stores. */}
            {mine && (
              <>
                <line className={edgeClass("app-cache")} x1={b.x} y1={b.y} x2={FIXED.cache.x} y2={FIXED.cache.y} />
                <line className={edgeClass("app-db")} x1={b.x} y1={b.y} x2={FIXED.db.x} y2={FIXED.db.y} />
              </>
            )}
          </g>
        );
      })}

      {/* nodes */}
      <g className={nodeClass("browser")}>
        {rect(FIXED.browser)}
        <text x={FIXED.browser.x} y={FIXED.browser.y - 4} className="node-name">Browser</text>
        <text x={FIXED.browser.x} y={FIXED.browser.y + 16} className="node-sub">this page</text>
      </g>
      <g className={nodeClass("gateway")}>
        {rect(FIXED.gateway)}
        <text x={FIXED.gateway.x} y={FIXED.gateway.y - 4} className="node-name">API Gateway</text>
        <text x={FIXED.gateway.x} y={FIXED.gateway.y + 16} className="node-sub">NGINX · round-robin</text>
      </g>
      {instances.map((name) => {
        const b = appBox(name);
        const mine = name === activeInstance;
        return (
          <g key={name} className={`${nodeClass(`app:${name}`)}${mine ? " node-chosen" : " dim"}`}>
            {rect(b)}
            <text x={b.x} y={b.h < 48 ? b.y + 5 : b.y - 2} className="node-name">
              {label(name)}
            </text>
            {b.h >= 48 && (
              <text x={b.x} y={b.y + 15} className="node-sub">
                {mine ? "served this request" : "app server"}
              </text>
            )}
          </g>
        );
      })}
      <g className={nodeClass("cache")}>
        {rect(FIXED.cache)}
        <text x={FIXED.cache.x} y={FIXED.cache.y - 4} className="node-name">Cache</text>
        <text x={FIXED.cache.x} y={FIXED.cache.y + 16} className="node-sub">Redis · TTL 60 s</text>
      </g>
      <g className={nodeClass("db")}>
        {rect(FIXED.db)}
        <text x={FIXED.db.x} y={FIXED.db.y - 4} className="node-name">Database</text>
        <text x={FIXED.db.x} y={FIXED.db.y + 16} className="node-sub">Postgres · app_db</text>
      </g>

      {/* packet */}
      {step && (
        <g className={`packet tone-${packetTone}`} transform={`translate(${px} ${py - 44})`}>
          {step.from === step.to && <circle className="pulse" cx={0} cy={44} r={40 + 8 * Math.sin(progress * Math.PI)} />}
          <rect x={-30} y={-13} width={60} height={26} rx={13} />
          <text y={5}>{packetLabel}</text>
        </g>
      )}
    </svg>
  );
}
