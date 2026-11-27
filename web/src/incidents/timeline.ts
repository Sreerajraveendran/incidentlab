import type { Run, RunEvent } from "./types";

/** "3:07" for 187 seconds; "0:00" for negatives. */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** Seconds since the fault was injected (the clock the score uses), up to `now`. */
export function elapsed(run: Run, now: number): number {
  const from = run.active_at ?? run.started_at;
  const to = run.resolved_at ?? run.ended_at;
  return ((to ? Date.parse(to) : now) - Date.parse(from)) / 1000;
}

/** Seconds left before the time limit aborts the run; null if unknown. */
export function remaining(run: Run, now: number): number | null {
  const limit = run.scenario.time_limit_minutes;
  if (!limit || run.state !== "active") return null;
  return Math.max(0, limit * 60 - elapsed(run, now));
}

export type Tone = "good" | "warn" | "bad" | "neutral";

export function eventTone(e: RunEvent): Tone {
  switch (e.kind) {
    case "resolved":
      return "good";
    case "symptom":
    case "inject":
    case "hint":
      return "warn";
    case "error":
    case "aborted":
      return "bad";
    case "answer":
      return e.message.includes("(correct") ? "good" : "warn";
    default:
      return "neutral";
  }
}

export const EVENT_LABEL: Record<string, string> = {
  info: "info",
  inject: "fault",
  symptom: "symptom",
  hint: "hint",
  resolved: "resolved",
  aborted: "ended",
  answer: "root cause",
  error: "error",
};

export function stateLabel(run: Run): string {
  switch (run.state) {
    case "injecting":
      return "Injecting the fault";
    case "active":
      return "Incident in progress";
    case "cleanup":
      return run.outcome === "resolved" ? "Resolved · cleaning up" : "Ending · cleaning up";
    case "resolved":
      return "Resolved";
    case "aborted":
      return "Ended without a fix";
  }
}

export interface ScoreLine {
  label: string;
  points: number;
}

/** How the score was made up, line by line, matching base_score() in lab-api/app/engine.py. */
export function scoreBreakdown(run: Run): ScoreLine[] {
  if (run.score_base === null || run.seconds_to_recover === null) return [];
  const slow = Math.max(0, Math.floor((run.seconds_to_recover - 120) / 30));
  const lines: ScoreLine[] = [{ label: "Start", points: 100 }];
  if (slow) lines.push({ label: `Time: ${clock(run.seconds_to_recover)} (1 point per 30 s after 2:00)`, points: -slow });
  if (run.hints_used) lines.push({ label: `${run.hints_used} hint${run.hints_used > 1 ? "s" : ""}`, points: -10 * run.hints_used });
  const raw = lines.reduce((sum, l) => sum + l.points, 0);
  if (run.score_base > raw) lines.push({ label: "Minimum score", points: run.score_base - raw });
  if (run.root_cause_correct) lines.push({ label: "Correct root cause", points: 20 });
  return lines;
}

/** The run that should show its result: the active one, else the newest finished one. */
export function focusRun(active: Run | null, history: Run[], selectedId: string | null): Run | null {
  if (selectedId) {
    const picked = active?.id === selectedId ? active : history.find((r) => r.id === selectedId);
    if (picked) return picked;
  }
  return active ?? history[0] ?? null;
}

/** A resolved run still waiting for its root-cause answer. */
export const needsAnswer = (run: Run) =>
  run.outcome === "resolved" && run.root_cause_choice === null && !!run.root_cause_options?.length;
