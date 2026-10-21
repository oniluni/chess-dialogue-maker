import { openMedia } from "./probe.ts";
import type { OpenedMedia } from "./probe.ts";
import { clampRange } from "./range.ts";
import type { TimeRange } from "./range.ts";

export interface Clip {
  file: File;
  media: OpenedMedia;
  /** Blob URL shared by every page that shows the clip. */
  objectUrl: string;
  range: TimeRange;
}

let current: Clip | null = null;

export function getClip(): Clip | null {
  return current;
}

/** Opens a file as the current clip, releasing the previous one only after the new one loads. */
export async function openClip(file: File): Promise<Clip> {
  const media = await openMedia(file);
  closeClip();
  current = {
    file,
    media,
    objectUrl: URL.createObjectURL(file),
    range: { start: 0, end: media.info.duration },
  };
  return current;
}

export function setClipRange(range: TimeRange): Clip | null {
  if (current === null) return null;
  current.range = clampRange(range, current.media.info.duration);
  return current;
}

export function closeClip(): void {
  if (current === null) return;
  URL.revokeObjectURL(current.objectUrl);
  current.media.input.dispose();
  current = null;
}
