import { describe, expect, it } from "vitest";
import { clock, elapsed, eventTone, focusRun, needsAnswer, remaining, scoreBreakdown, stateLabel } from "./timeline";
import type { Run } from "./types";

const base: Run = {
  id: "r1",
  scenario_id: "db-down",
  scenario: { id: "db-down", title: "Items won't load", time_limit_minutes: 15, hint_count: 3 },
  state: "active",
  outcome: null,
  started_at: "2026-11-28T10:00:00+00:00",
  active_at: "2026-11-28T10:00:02+00:00",
  resolved_at: null,
  ended_at: null,
  seconds_to_recover: null,
  hints_used: 0,
  hints: [],
  checks: [],
  argo_paused: true,
  abort_reason: null,
  last_error: null,
  score_base: null,
  score: null,
  root_cause_choice: null,
  root_cause_correct: null,
  events: [],
};
const at = (iso: string) => Date.parse(iso);

describe("clock and timers", () => {
  it("formats minutes and seconds", () => {
    expect(clock(0)).toBe("0:00");
    expect(clock(187)).toBe("3:07");
    expect(clock(-5)).toBe("0:00");
  });

  it("counts from injection, and stops at resolution", () => {
    expect(elapsed(base, at("2026-11-28T10:01:02+00:00"))).toBe(60);
    const done = { ...base, state: "resolved" as const, resolved_at: "2026-11-28T10:03:02+00:00" };
    expect(elapsed(done, at("2026-11-28T11:00:00+00:00"))).toBe(180);
  });

  it("shows the time left only while active", () => {
    expect(remaining(base, at("2026-11-28T10:05:02+00:00"))).toBe(600);
    expect(remaining(base, at("2026-11-28T11:00:00+00:00"))).toBe(0);
    expect(remaining({ ...base, state: "cleanup" }, Date.now())).toBeNull();
  });
});

describe("labels and tones", () => {
  it("colours events by meaning", () => {
    expect(eventTone({ at: "", kind: "resolved", message: "" })).toBe("good");
    expect(eventTone({ at: "", kind: "symptom", message: "" })).toBe("warn");
    expect(eventTone({ at: "", kind: "error", message: "" })).toBe("bad");
    expect(eventTone({ at: "", kind: "answer", message: "x (correct, +20)" })).toBe("good");
    expect(eventTone({ at: "", kind: "answer", message: "x (not quite)" })).toBe("warn");
  });

  it("describes cleanup by outcome", () => {
    expect(stateLabel({ ...base, state: "cleanup", outcome: "resolved" })).toMatch(/Resolved/);
    expect(stateLabel({ ...base, state: "cleanup", outcome: "aborted" })).toMatch(/Ending/);
  });
});

describe("score breakdown", () => {
  it("adds up to the stored score", () => {
    const run = {
      ...base,
      state: "resolved" as const,
      outcome: "resolved" as const,
      seconds_to_recover: 204,
      hints_used: 1,
      score_base: 88,
      score: 108,
      root_cause_choice: 1,
      root_cause_correct: true,
    };
    const lines = scoreBreakdown(run);
    expect(lines.reduce((s, l) => s + l.points, 0)).toBe(108);
    expect(lines.map((l) => l.points)).toEqual([100, -2, -10, 20]);
  });

  it("shows the minimum-score top-up", () => {
    const run = { ...base, seconds_to_recover: 5000, hints_used: 3, score_base: 10, score: 10 };
    expect(scoreBreakdown(run).reduce((s, l) => s + l.points, 0)).toBe(10);
  });

  it("is empty before resolution", () => {
    expect(scoreBreakdown(base)).toEqual([]);
  });
});

describe("which run to show", () => {
  const old = { ...base, id: "old", state: "resolved" as const };
  it("prefers the selected run, then the active one, then the newest", () => {
    expect(focusRun(base, [old], "old")?.id).toBe("old");
    expect(focusRun(base, [old], null)?.id).toBe("r1");
    expect(focusRun(null, [old], "missing")?.id).toBe("old");
    expect(focusRun(null, [], null)).toBeNull();
  });

  it("asks for the root cause only once, after resolution", () => {
    const resolved = { ...base, outcome: "resolved" as const, root_cause_options: ["a", "b"] };
    expect(needsAnswer(resolved)).toBe(true);
    expect(needsAnswer({ ...resolved, root_cause_choice: 0 })).toBe(false);
    expect(needsAnswer({ ...resolved, outcome: "aborted" as const })).toBe(false);
  });
});
