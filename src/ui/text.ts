/** `m:ss.s` — a precise position, as move timecodes are shown. */
export function formatTimestamp(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00.0";
  const total = Math.floor(seconds * 10) / 10;
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = (total % 60).toFixed(1).padStart(4, "0");
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}` : `${minutes}:${rest}`;
}

/** `m:ss`, or `h:mm:ss` past an hour. */
export function formatClock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const total = Math.floor(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = total % 60;
  const tail = `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
  return hours > 0 ? `${hours}:${tail}` : `${minutes}:${String(rest).padStart(2, "0")}`;
}

/** `+1.2`, `-0.5`, `0.0` — the eval label. */
export function formatEval(score: number): string {
  const rounded = Math.round(score * 10) / 10;
  if (rounded === 0) return "0.0";
  return rounded > 0 ? `+${rounded.toFixed(1)}` : rounded.toFixed(1);
}

const SIZE_UNITS = ["B", "KB", "MB", "GB"] as const;

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const power = Math.min(SIZE_UNITS.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / 1024 ** power;
  const digits = power === 0 || value >= 10 ? 0 : 1;
  return `${value.toFixed(digits)} ${SIZE_UNITS[power] ?? "B"}`;
}

/** Message for a caught value, for the status line. */
export function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
