import type { Move } from "./analysis/move.ts";
import { DEFAULT_SCHEDULE_OPTIONS } from "./analysis/schedule.ts";
import type { ScheduleOptions } from "./analysis/schedule.ts";
import type { TranscriptSegment } from "./analysis/transcript.ts";
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

export interface LlmChoice {
  provider: string;
  model: string;
}

let clip: Clip | null = null;
let moves: Move[] = [];
let movesBackup: Move[] | null = null;
let transcript: TranscriptSegment[] = [];
let scheduleOptions: ScheduleOptions = { ...DEFAULT_SCHEDULE_OPTIONS };
let names: Names = { ...DEFAULT_NAMES };
let llm: LlmChoice = { provider: "", model: "" };

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

export function getLlmChoice(): LlmChoice {
  return llm;
}

export function setLlmChoice(patch: Partial<LlmChoice>): void {
  llm = { ...llm, ...patch };
}

export function getTranscript(): TranscriptSegment[] {
  return transcript;
}

export function setTranscript(next: TranscriptSegment[]): void {
  transcript = next;
}

export function getClip(): Clip | null {
  return clip;
}

export function getMoves(): Move[] {
  return moves;
}

export function setMoves(next: Move[], options: { keepBackup?: boolean } = {}): void {
  moves = next;
  if (options.keepBackup === true) movesBackup = next.map((move) => ({ ...move }));
}

/** The analysis as it was before the first hand edit; `null` until a list arrives. */
export function getMovesBackup(): Move[] | null {
  return movesBackup === null ? null : movesBackup.map((move) => ({ ...move }));
}

export function restoreMovesBackup(): boolean {
  if (movesBackup === null) return false;
  moves = movesBackup.map((move) => ({ ...move }));
  return true;
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
  movesBackup = null;
  transcript = [];
}
