// Shapes of the Lab API's incident endpoints (lab-api/app/engine.py: Engine.view).

export interface ScenarioInfo {
  id: string;
  title: string;
  briefing: string;
  time_limit_minutes: number;
  hint_count: number;
}

export interface RunEvent {
  at: string;
  kind: "info" | "inject" | "symptom" | "hint" | "resolved" | "aborted" | "answer" | "error" | string;
  message: string;
}

export interface Check {
  label: string;
  ok: boolean;
  detail: string;
}

export type RunState = "injecting" | "active" | "cleanup" | "resolved" | "aborted";

export interface Run {
  id: string;
  scenario_id: string;
  scenario: Partial<ScenarioInfo> & { id: string };
  state: RunState;
  outcome: "resolved" | "aborted" | null;
  started_at: string;
  active_at: string | null;
  resolved_at: string | null;
  ended_at: string | null;
  seconds_to_recover: number | null;
  hints_used: number;
  hints: string[];
  checks: Check[];
  argo_paused: boolean;
  abort_reason: string | null;
  last_error: string | null;
  score_base: number | null;
  score: number | null;
  root_cause_choice: number | null;
  root_cause_correct: boolean | null;
  root_cause_options?: string[];
  root_cause_answer?: number;
  explanation?: string;
  events: RunEvent[];
}

export interface Runs {
  active: Run | null;
  history: Run[]; // newest first
}
