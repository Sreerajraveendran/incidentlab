import type { Sample } from "./types";

interface Props {
  samples: Sample[];
  targetCpu: number | null;
  maxReplicas: number;
  windowS: number;
  nowMs: number;
}

const W = 720;
const H = 200;
const PAD = { l: 44, r: 44, t: 14, b: 28 };

/** CPU % (left axis) and replica count (right axis) over the last `windowS` seconds. */
export default function Chart({ samples, targetCpu, maxReplicas, windowS, nowMs }: Props) {
  const innerW = W - PAD.l - PAD.r;
  const innerH = H - PAD.t - PAD.b;
  const cpuMax = Math.max(100, (targetCpu ?? 50) * 2, ...samples.map((s) => s.cpu ?? 0)) * 1.1;
  const repMax = maxReplicas + 1;
  const x = (t: number) => PAD.l + innerW * (1 - (nowMs - t) / (windowS * 1000));
  const yCpu = (v: number) => PAD.t + innerH * (1 - v / cpuMax);
  const yRep = (v: number) => PAD.t + innerH * (1 - v / repMax);

  const visible = samples.filter((s) => nowMs - s.t <= windowS * 1000);

  // CPU line, broken where metrics were unavailable (never drawn as 0).
  const cpuPaths: string[] = [];
  let current = "";
  for (const s of visible) {
    if (s.cpu === null) {
      if (current) cpuPaths.push(current);
      current = "";
      continue;
    }
    current += `${current ? "L" : "M"}${x(s.t).toFixed(1)} ${yCpu(s.cpu).toFixed(1)}`;
  }
  if (current) cpuPaths.push(current);

  // Replicas as a step line: the count changes in jumps.
  let rep = "";
  visible.forEach((s, i) => {
    const px = x(s.t).toFixed(1);
    const py = yRep(s.replicas).toFixed(1);
    rep += i === 0 ? `M${px} ${py}` : `H${px}V${py}`;
  });
  if (visible.length) rep += `H${x(nowMs).toFixed(1)}`;

  const cpuTicks = [0, 50, 100, 150, 200, 250].filter((v) => v <= cpuMax);
  const repTicks = Array.from({ length: repMax + 1 }, (_, i) => i).filter((v) => v % 2 === 0);

  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Average CPU and replica count over time">
      {cpuTicks.map((v) => (
        <g key={`c${v}`}>
          <line className="grid" x1={PAD.l} x2={W - PAD.r} y1={yCpu(v)} y2={yCpu(v)} />
          <text className="axis" x={PAD.l - 6} y={yCpu(v) + 4} textAnchor="end">
            {v}%
          </text>
        </g>
      ))}
      {repTicks.map((v) => (
        <text key={`r${v}`} className="axis rep" x={W - PAD.r + 6} y={yRep(v) + 4}>
          {v}
        </text>
      ))}
      {[0, 60, 120, 180, 240, 300].filter((s) => s <= windowS).map((s) => (
        <text key={`t${s}`} className="axis" x={x(nowMs - s * 1000)} y={H - 8} textAnchor="middle">
          {s === 0 ? "now" : `-${s / 60}m`}
        </text>
      ))}
      {targetCpu !== null && (
        <>
          <line className="target" x1={PAD.l} x2={W - PAD.r} y1={yCpu(targetCpu)} y2={yCpu(targetCpu)} />
          <text className="axis target-label" x={PAD.l + 4} y={yCpu(targetCpu) - 5}>
            target {targetCpu}%
          </text>
        </>
      )}
      {cpuPaths.map((d, i) => (
        <path key={i} className="cpu" d={d} />
      ))}
      {rep && <path className="replicas" d={rep} />}
    </svg>
  );
}
