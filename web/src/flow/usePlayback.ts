import { useCallback, useEffect, useRef, useState } from "react";
import type { FlowStep } from "./types";

// Animation time per frame at 1x. These are display times, not measured ones:
// the real durations are shown in the inspector.
const MOVE_MS = 1100;
const STAY_MS = 800;

export interface Playback {
  index: number;
  progress: number; // 0..1 within the current step
  playing: boolean;
  speed: number;
  play: () => void;
  pause: () => void;
  stepForward: () => void;
  stepBack: () => void;
  restart: () => void;
  setSpeed: (s: number) => void;
}

export function usePlayback(steps: FlowStep[]): Playback {
  const [index, setIndex] = useState(0);
  const [progress, setProgress] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const last = useRef<number | null>(null);

  // New run: start from the beginning and play.
  useEffect(() => {
    setIndex(0);
    setProgress(0);
    setPlaying(steps.length > 0);
  }, [steps]);

  useEffect(() => {
    if (!playing || steps.length === 0) {
      last.current = null;
      return;
    }
    let frame = 0;
    const tick = (now: number) => {
      const dt = last.current === null ? 0 : now - last.current;
      last.current = now;
      setProgress((p) => {
        const step = steps[index];
        const span = step && step.from !== step.to ? MOVE_MS : STAY_MS;
        const next = p + (dt * speed) / span;
        if (next < 1) return next;
        if (index < steps.length - 1) {
          setIndex(index + 1);
          return 0;
        }
        setPlaying(false);
        return 1;
      });
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing, index, speed, steps]);

  const stepForward = useCallback(() => {
    setPlaying(false);
    if (progress < 1) setProgress(1);
    else if (index < steps.length - 1) {
      setIndex(index + 1);
      setProgress(1);
    }
  }, [index, progress, steps.length]);

  const stepBack = useCallback(() => {
    setPlaying(false);
    setIndex(Math.max(0, index - 1));
    setProgress(1);
  }, [index]);

  const restart = useCallback(() => {
    setIndex(0);
    setProgress(0);
    setPlaying(true);
  }, []);

  const play = useCallback(() => {
    // At the very end, play means replay.
    if (index === steps.length - 1 && progress >= 1) {
      setIndex(0);
      setProgress(0);
    }
    setPlaying(true);
  }, [index, progress, steps.length]);

  return {
    index,
    progress,
    playing,
    speed,
    play,
    pause: () => setPlaying(false),
    stepForward,
    stepBack,
    restart,
    setSpeed,
  };
}
