import type { Move } from "./analysis/move.ts";
import { DEFAULT_SCHEDULE_OPTIONS } from "./analysis/schedule.ts";
import type { ScheduleOptions } from "./analysis/schedule.ts";
import { openMedia } from "./media/probe.ts";
import type { OpenedMedia } from "./media/probe.ts";
import { clampRange } from "./media/range.ts";
import type { TimeRange } from "./media/range.ts";
import { DEFAULT_NAMES } from "./names.ts";
import type { Names } from "./names.ts";

export interface Clip {
  file: File;
  media: OpenedMedia;
  /** Blob URL shared by every page that shows the clip. */
  objectUrl: string;
  range: TimeRange;
}

let clip: Clip | null = null;
let moves: Move[] = [];
let scheduleOptions: ScheduleOptions = { ...DEFAULT_SCHEDULE_OPTIONS };
let names: Names = { ...DEFAULT_NAMES };

export function getScheduleOptions(): ScheduleOptions {
  return scheduleOptions;
}

export function setScheduleOptions(patch: Partial<ScheduleOptions>): void {
  scheduleOptions = { ...scheduleOptions, ...patch };
}

export function getNames(): Names {
  return names;
}

export function setNames(patch: Partial<Names>): void {
  names = { ...names, ...patch };
}

export function getClip(): Clip | null {
  return clip;
}

export function getMoves(): Move[] {
  return moves;
}

export function setMoves(next: Move[]): void {
  moves = next;
}

/** Opens a file as the current clip, releasing the previous one only after the new one loads. */
export async function openClip(file: File): Promise<Clip> {
  const media = await openMedia(file);
  closeClip();
  clip = {
    file,
    media,
    objectUrl: URL.createObjectURL(file),
    range: { start: 0, end: media.info.duration },
  };
  return clip;
}

export function setClipRange(range: TimeRange): Clip | null {
  if (clip === null) return null;
  clip.range = clampRange(range, clip.media.info.duration);
  return clip;
}

export function closeClip(): void {
  if (clip === null) return;
  URL.revokeObjectURL(clip.objectUrl);
  clip.media.input.dispose();
  clip = null;
  moves = [];
}
