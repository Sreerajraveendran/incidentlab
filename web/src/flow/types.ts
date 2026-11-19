// Shapes shared by the request-flow view.

export type NodeId = "browser" | "gateway" | "app" | "cache" | "db";

export type StepStatus = "ok" | "hit" | "miss" | "error" | "skipped" | "simulated" | "running";

// One step recorded by the app (see app/app/trace.py).
export interface TraceStep {
  node: "app" | "cache" | "db" | "network";
  name: string;
  status: StepStatus;
  started_ms: number;
  duration_ms: number | null;
  detail: Record<string, unknown>;
}

export interface AppTrace {
  instance: string;
  version: string;
  method: string;
  path: string;
  status_code: number;
  total_ms: number;
  scenario: Record<string, unknown>;
  steps: TraceStep[];
}

export type Method = "GET" | "POST" | "PUT" | "DELETE";

export interface Scenario {
  auth: "valid" | "wrong" | "none";
  cache: "auto" | "force-hit" | "force-miss";
  dbError: boolean;
  networkMs: number;
}

export interface FlowRun {
  id: string;
  at: number;
  request: { method: Method; url: string; headers: Record<string, string>; body: string | null };
  response: { status: number; headers: Record<string, string>; body: unknown } | null;
  networkError: string | null;
  trace: AppTrace | null;
  browserMs: number;
}

// One frame of the animation: the packet travels from -> to (or stays put when equal).
export interface FlowStep {
  from: NodeId;
  to: NodeId;
  focus: NodeId; // node the inspector describes and the diagram highlights
  title: string;
  status: StepStatus;
  durationMs: number | null; // measured; null when the step has no own timing
  simulated: boolean;
  detail: Record<string, unknown>;
  log: string;
  phase: "request" | "response";
}
