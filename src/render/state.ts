import type { Move } from "../analysis/move.ts";
import { activeMoveIndex } from "../analysis/move.ts";
import type { ScheduleOptions } from "../analysis/schedule.ts";
import { typingTime } from "../analysis/schedule.ts";

/** What the card looks like at one moment of the output timeline. */
export interface FrameState {
  /** -1 = the intro card shown before the first move. */
  moveIndex: number;
  /** How much of the explanation is typed, 0…1. */
  typed: number;
  evalScore: number;
  /** 0…1 crossfade from the previous card. */
  cardMix: number;
  iconScale: number;
}

const CARD_FADE = 0.18;
const EVAL_ANIM = 0.5;
const ICON_ANIM = 0.35;

function easeInOut(t: number): number {
  return t < 0.5 ? 4 * t ** 3 : 1 - (-2 * t + 2) ** 3 / 2;
}

function easeOutBack(t: number): number {
  const c = 1.9;
  const x = Math.min(1, Math.max(0, t)) - 1;
  return 1 + (c + 1) * x ** 3 + c * x ** 2;
}

/** During playback the last revealed move stays on screen, fully typed. */
export function playState(moves: Move[], time: number): FrameState {
  const index = activeMoveIndex(moves, time);
  return {
    moveIndex: index,
    typed: 1,
    evalScore: index < 0 ? 0 : (moves[index]?.evalScore ?? 0),
    cardMix: 1,
    iconScale: 1,
  };
}

/** During a freeze the new card pops in, its text is typed and the bar moves. */
export function pauseState(moves: Move[], index: number, elapsed: number, options: ScheduleOptions): FrameState {
  const move = moves[index];
  const previous = index > 0 ? (moves[index - 1]?.evalScore ?? 0) : 0;
  const chars = move?.explanation.length ?? 0;
  const typing = Math.max(0.001, typingTime(chars, options.typingCps));
  const typed = elapsed <= CARD_FADE ? 0 : Math.min(1, (elapsed - CARD_FADE) / typing);
  const target = move?.evalScore ?? 0;
  return {
    moveIndex: index,
    typed,
    evalScore: previous + (target - previous) * easeInOut(Math.min(1, elapsed / EVAL_ANIM)),
    cardMix: easeInOut(Math.min(1, elapsed / CARD_FADE)),
    iconScale: easeOutBack(elapsed / ICON_ANIM),
  };
}
