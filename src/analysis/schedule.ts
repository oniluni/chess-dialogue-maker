import type { TimeRange } from "../media/range.ts";
import type { Move } from "./move.ts";

/** Output frame rate of the export and of the pause holds. */
export const FRAME_RATE = 30;

/** A card is fully typed, then the picture holds a moment longer. */
const CARD_FADE = 0.18;
const MIN_PAUSE = 0.8;
const PAUSE_TAIL = 0.12;

export interface ScheduleOptions {
  pauseOnMove: boolean;
  typingCps: number;
  readHold: number;
  sfxEnabled: boolean;
  sfxVolume: number;
  sfxPause: boolean;
  sfxUnpause: boolean;
  sfxTyping: boolean;
  sfxTypingSet: string;
  sfxPauseSet: string;
}

export const DEFAULT_SCHEDULE_OPTIONS: ScheduleOptions = {
  pauseOnMove: true,
  typingCps: 28,
  readHold: 2,
  sfxEnabled: true,
  sfxVolume: 0.8,
  sfxPause: true,
  sfxUnpause: true,
  sfxTyping: true,
  sfxTypingSet: "soft",
  sfxPauseSet: "wood",
};

export interface PlaySegment {
  kind: "play";
  start: number;
  end: number;
}

/** The picture stops at `at` for `duration` output seconds while the card is shown. */
export interface PauseSegment {
  kind: "pause";
  at: number;
  duration: number;
}

export type ScheduleSegment = PlaySegment | PauseSegment;

export interface Schedule {
  segments: ScheduleSegment[];
  /** Total length of the finished video. */
  duration: number;
}

export function typingTime(chars: number, cps: number): number {
  return chars / Math.max(1, cps);
}

/** Card pop-in, the explanation typed out, then the reading time. */
export function pauseDuration(chars: number, options: ScheduleOptions): number {
  return Math.max(
    MIN_PAUSE,
    CARD_FADE + typingTime(chars, options.typingCps) + Math.max(0, options.readHold),
  );
}

/**
 * Turns the kept range and the moves into the output timeline: play, freeze on a move,
 * resume from the same frame. With pauses off it is one uninterrupted play segment.
 */
export function buildSchedule(range: TimeRange, moves: Move[], options: ScheduleOptions): Schedule {
  const wholeRange = (): Schedule => ({
    segments: [{ kind: "play", start: range.start, end: range.end }],
    duration: range.end - range.start,
  });

  if (!options.pauseOnMove || moves.length === 0) return wholeRange();

  const inside = moves.filter((move) => move.end > range.start && move.start < range.end);
  const segments: ScheduleSegment[] = [];
  let cursor = range.start;

  for (let index = 0; index < inside.length; index += 1) {
    const move = inside[index];
    if (move === undefined) continue;
    const end = Math.min(move.end, range.end);
    if (end <= cursor) continue;

    const next = inside[index + 1];
    const nextStart = next === undefined ? range.end : Math.min(next.start, range.end);
    const freezeAt = end + Math.min(PAUSE_TAIL, Math.max(0, (nextStart - end) / 2));

    if (freezeAt > cursor) {
      segments.push({ kind: "play", start: cursor, end: freezeAt });
      cursor = freezeAt;
    }
    segments.push({ kind: "pause", at: cursor, duration: pauseDuration(move.explanation.length, options) });
  }

  if (cursor < range.end) segments.push({ kind: "play", start: cursor, end: range.end });
  if (segments.length === 0) return wholeRange();

  const duration = segments.reduce(
    (total, segment) => total + (segment.kind === "play" ? segment.end - segment.start : segment.duration),
    0,
  );
  return { segments, duration };
}
