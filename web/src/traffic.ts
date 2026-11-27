import { newRequestId } from "./ids";

// One measured request as seen by the browser, through the load balancer.
export interface Sample {
  requestId: string;
  instance: string | null; // null when no instance answered (network error, 502/503 from NGINX)
  status: number; // 0 for a network error
  ms: number;
  serverTiming: string | null;
  at: number;
}

export interface InstanceStats {
  instance: string;
  count: number;
  share: number;
  avgMs: number;
}

export interface Tally {
  total: number;
  failed: number;
  instances: InstanceStats[];
}

const ok = (s: Sample) => s.status >= 200 && s.status < 400 && s.instance !== null;

export function tally(samples: Sample[]): Tally {
  const byInstance = new Map<string, { count: number; ms: number }>();
  let failed = 0;
  for (const s of samples) {
    if (!ok(s)) {
      failed++;
      continue;
    }
    const entry = byInstance.get(s.instance!) ?? { count: 0, ms: 0 };
    entry.count++;
    entry.ms += s.ms;
    byInstance.set(s.instance!, entry);
  }
  const served = samples.length - failed;
  const instances = [...byInstance.entries()]
    .map(([instance, { count, ms }]) => ({
      instance,
      count,
      share: served ? count / served : 0,
      avgMs: ms / count,
    }))
    .sort((a, b) => a.instance.localeCompare(b.instance));
  return { total: samples.length, failed, instances };
}

export async function measure(path: string): Promise<Sample> {
  const requestId = newRequestId();
  const start = performance.now();
  try {
    const res = await fetch(path, { headers: { "X-Request-ID": requestId }, cache: "no-store" });
    await res.arrayBuffer();
    return {
      requestId,
      instance: res.headers.get("X-Pod-Name"),
      status: res.status,
      ms: performance.now() - start,
      serverTiming: res.headers.get("Server-Timing"),
      at: Date.now(),
    };
  } catch {
    return {
      requestId,
      instance: null,
      status: 0,
      ms: performance.now() - start,
      serverTiming: null,
      at: Date.now(),
    };
  }
}

// Send `total` requests with at most `concurrency` in flight, reporting each as it lands.
export async function run(
  path: string,
  total: number,
  concurrency: number,
  onSample: (s: Sample) => void,
): Promise<void> {
  let next = 0;
  const worker = async () => {
    while (next < total) {
      next++;
      onSample(await measure(path));
    }
  };
  await Promise.all(Array.from({ length: Math.min(concurrency, total) }, worker));
}
