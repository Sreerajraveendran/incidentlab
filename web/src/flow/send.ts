import { newRequestId } from "../ids";
import type { AppTrace, FlowRun, Method, Scenario } from "./types";

const WRONG_TOKEN = "not-the-right-token";

/** Send one real request through NGINX with tracing and scenario headers. */
export async function sendFlow(
  method: Method,
  itemId: number,
  body: string,
  scenario: Scenario,
  token: string,
): Promise<FlowRun> {
  const id = newRequestId();
  const url = method === "POST" ? "/api/v1/items" : `/api/v1/items/${itemId}`;
  const headers: Record<string, string> = { "X-Trace": "1", "X-Request-ID": id };
  const hasBody = method === "POST" || method === "PUT";
  if (hasBody) headers["Content-Type"] = "application/json";
  if (scenario.auth === "valid" && token) headers.Authorization = `Bearer ${token}`;
  if (scenario.auth === "wrong") headers.Authorization = `Bearer ${WRONG_TOKEN}`;
  if (scenario.cache !== "auto") headers["X-Sim-Cache"] = scenario.cache;
  if (scenario.dbError) headers["X-Sim-DB-Error"] = "1";
  if (scenario.networkMs) headers["X-Sim-Network-Ms"] = String(scenario.networkMs);

  // What the inspector shows: the real token is masked.
  const shownHeaders = { ...headers };
  if (shownHeaders.Authorization && scenario.auth === "valid") shownHeaders.Authorization = "Bearer ••••••";

  const run: FlowRun = {
    id,
    at: Date.now(),
    request: { method, url, headers: shownHeaders, body: hasBody ? body : null },
    response: null,
    networkError: null,
    trace: null,
    browserMs: 0,
  };

  const start = performance.now();
  try {
    const res = await fetch(url, {
      method,
      headers,
      body: hasBody ? body : undefined,
      cache: "no-store",
    });
    const text = await res.text();
    run.browserMs = performance.now() - start;
    const responseHeaders: Record<string, string> = {};
    res.headers.forEach((v, k) => (responseHeaders[k] = v));
    let parsed: unknown = text;
    try {
      parsed = JSON.parse(text);
    } catch {
      /* not JSON: keep the text */
    }
    if (parsed && typeof parsed === "object" && "trace" in parsed) {
      run.trace = (parsed as { trace: AppTrace }).trace;
    }
    run.response = { status: res.status, headers: responseHeaders, body: parsed };
  } catch (err) {
    run.browserMs = performance.now() - start;
    run.networkError = err instanceof Error ? err.message : String(err);
  }
  return run;
}
