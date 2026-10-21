import { activeMoveIndex } from "../analysis/move.ts";
import type { Move } from "../analysis/move.ts";
import type { Schedule, ScheduleOptions } from "../analysis/schedule.ts";
import { typingTime } from "../analysis/schedule.ts";
import type { SfxName } from "./sfx.ts";

export interface SfxEvent {
  /** Output time in seconds. */
  at: number;
  name: SfxName;
}

const CARD_FADE = 0.18;
const TICK_STEP = 0.06;
const UNPAUSE_LEAD = 0.08;
const TICK_KINDS: readonly SfxName[] = ["tick1", "tick2", "tick3", "tick4"];

/** Deterministic tick rotation, as the old renderer's seeded RNG produced. */
function seededRandom(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4_294_967_296;
  };
}

/** Sounds of one freeze, as offsets from its start. */
export function pauseSfxEvents(
  chars: number,
  duration: number,
  options: ScheduleOptions,
): Array<{ name: SfxName; offset: number }> {
  const events: Array<{ name: SfxName; offset: number }> = [];
  if (options.sfxPause) events.push({ name: "pause", offset: 0 });

  if (options.sfxTyping) {
    const typing = typingTime(chars, options.typingCps);
    const random = seededRandom(7);
    for (let offset = CARD_FADE; offset < CARD_FADE + typing; offset += TICK_STEP) {
      const kind = TICK_KINDS[Math.floor(random() * TICK_KINDS.length)] ?? "tick1";
      events.push({ name: kind, offset });
    }
  }

  if (options.sfxUnpause) {
    events.push({ name: "unpause", offset: Math.max(0, duration - UNPAUSE_LEAD) });
  }
  return events;
}

/** Every sound of the finished video, on the output timeline. */
export function buildSfxEvents(schedule: Schedule, moves: Move[], options: ScheduleOptions): SfxEvent[] {
  if (!options.sfxEnabled || options.sfxVolume <= 0) return [];

  const events: SfxEvent[] = [];
  let output = 0;
  for (const segment of schedule.segments) {
    if (segment.kind === "play") {
      if (!options.pauseOnMove) {
        for (const move of moves) {
          if (move.start >= segment.start && move.start < segment.end) {
            events.push({ at: output + (move.start - segment.start), name: "move" });
          }
        }
      }
      output += segment.end - segment.start;
      continue;
    }

    const move = moves[Math.max(0, activeMoveIndex(moves, segment.at))];
    for (const event of pauseSfxEvents(move?.explanation.length ?? 0, segment.duration, options)) {
      events.push({ at: output + event.offset, name: event.name });
    }
    output += segment.duration;
  }
  return events;
}
