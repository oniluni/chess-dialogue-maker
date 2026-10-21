export interface TimeRange {
  start: number;
  end: number;
}

export const MIN_RANGE_SECONDS = 0.1;

/** Keeps a range inside `0…duration` and at least `MIN_RANGE_SECONDS` long. */
export function clampRange(range: TimeRange, duration: number): TimeRange {
  const maxStart = Math.max(0, duration - MIN_RANGE_SECONDS);
  const start = Math.min(Math.max(0, range.start), maxStart);
  const minEnd = Math.min(duration, start + MIN_RANGE_SECONDS);
  const end = Math.min(Math.max(minEnd, range.end), duration);
  return { start, end };
}
