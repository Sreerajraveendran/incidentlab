import { describe, expect, it } from "vitest";
import { buildSteps } from "./buildSteps";
import type { FlowRun, TraceStep } from "./types";

const step = (node: TraceStep["node"], name: string, status: TraceStep["status"], detail = {}): TraceStep => ({
  node,
  name,
  status,
  started_ms: 0,
  duration_ms: 1.5,
  detail,
});

const run = (over: Partial<FlowRun> = {}, steps: TraceStep[] = [], status = 200): FlowRun => ({
  id: "r1",
  at: 0,
  request: { method: "GET", url: "/api/v1/items/1", headers: {}, body: null },
  response: {
    status,
    headers: {
      "x-pod-name": "app-2",
      "x-upstream": "172.24.0.5:8000",
      "x-gateway-connect-time": "0.001",
      "x-gateway-header-time": "0.004",
    },
    body: { result: { item: { id: 1 } } },
  },
  networkError: null,
  trace: {
    instance: "app-2",
    version: "dev",
    method: "GET",
    path: "/api/v1/items/1",
    status_code: status,
    total_ms: 3,
    scenario: {},
    steps,
  },
  browserMs: 10,
  ...over,
});

const path = (r: FlowRun) => buildSteps(r).map((s) => `${s.from}>${s.to}`);

describe("buildSteps", () => {
  it("cache hit: goes to the cache and back, never to the database", () => {
    const r = run({}, [step("app", "Request received", "ok"), step("cache", "Cache lookup", "hit")]);
    expect(path(r)).toEqual([
      "browser>gateway",
      "gateway>app",
      "app>app",
      "app>cache",
      "cache>app",
      "app>gateway",
      "gateway>browser",
      "browser>browser",
    ]);
    expect(buildSteps(r)[4].title).toBe("Cache returns the value (hit)");
  });

  it("cache miss: lookup, database query, cache fill", () => {
    const r = run({}, [
      step("cache", "Cache lookup", "miss"),
      step("db", "SELECT item by id", "ok", { rows: 1, sql: "SELECT …" }),
      step("cache", "Cache fill (after miss)", "ok"),
    ]);
    const s = buildSteps(r);
    expect(s.map((x) => x.to)).toContain("db");
    expect(s.find((x) => x.to === "db")!.detail.sql).toBe("SELECT …");
    expect(s.find((x) => x.from === "db")!.title).toBe("Database returns 1 row(s)");
  });

  it("database error marks the step and the response as errors", () => {
    const r = run({}, [step("db", "SELECT item by id", "error", { error: "connection refused (simulated)", simulated: true })], 503);
    const s = buildSteps(r);
    const dbStep = s.find((x) => x.to === "db")!;
    expect(dbStep.status).toBe("error");
    expect(dbStep.simulated).toBe(true);
    expect(s.at(-1)!.status).toBe("error");
  });

  it("measured gateway timings come from NGINX headers in ms", () => {
    const s = buildSteps(run());
    expect(s[1].durationMs).toBeCloseTo(1);
    expect(s.at(-2)!.durationMs).toBeCloseTo(4);
  });

  it("a gateway retry is shown as such", () => {
    const r = run();
    r.response!.headers["x-upstream"] = "172.24.0.3:8000, 172.24.0.5:8000";
    expect(buildSteps(r)[1].title).toMatch(/retried/);
  });

  it("no backend: the gateway answers by itself", () => {
    const r = run({ trace: null });
    delete r.response!.headers["x-pod-name"];
    r.response!.status = 503;
    expect(path(r)).toEqual(["browser>gateway", "gateway>browser"]);
  });

  it("network error: nothing came back", () => {
    const s = buildSteps(run({ response: null, trace: null, networkError: "Failed to fetch" }));
    expect(s).toHaveLength(2);
    expect(s[1].status).toBe("error");
  });
});
