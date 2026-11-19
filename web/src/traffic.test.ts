import { describe, expect, it } from "vitest";
import { type Sample, tally } from "./traffic";

const sample = (instance: string | null, status: number, ms = 10): Sample => ({
  requestId: "r",
  instance,
  status,
  ms,
  serverTiming: null,
  at: 0,
});

describe("tally", () => {
  it("counts requests per instance with shares and average latency", () => {
    const t = tally([sample("app-1", 200, 10), sample("app-2", 200, 30), sample("app-1", 200, 20)]);
    expect(t.total).toBe(3);
    expect(t.failed).toBe(0);
    expect(t.instances).toEqual([
      { instance: "app-1", count: 2, share: 2 / 3, avgMs: 15 },
      { instance: "app-2", count: 1, share: 1 / 3, avgMs: 30 },
    ]);
  });

  it("counts gateway errors and network failures as failed, not as an instance", () => {
    const t = tally([sample(null, 502), sample(null, 0), sample("app-3", 200)]);
    expect(t.failed).toBe(2);
    expect(t.instances.map((i) => i.instance)).toEqual(["app-3"]);
    expect(t.instances[0].share).toBe(1);
  });

  it("reports an instance's 5xx as failed", () => {
    expect(tally([sample("app-1", 503)]).failed).toBe(1);
  });

  it("handles no samples", () => {
    expect(tally([])).toEqual({ total: 0, failed: 0, instances: [] });
  });
});
