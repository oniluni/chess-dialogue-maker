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

/**
 * `62`, `1:02` or `1:02.5` → seconds; empty → `null` (no limit), malformed → `NaN`.
 * The time inputs accept what people type for timecodes, not just numbers.
 */
export function parseTimestamp(value: string): number | null {
  const text = value.trim();
  if (text === "") return null;

  const parts = text.split(":");
  if (parts.length > 2) return Number.NaN;
  const numbers = parts.map((part) => Number(part.replace(",", ".")));
  if (numbers.some((number) => !Number.isFinite(number) || number < 0)) return Number.NaN;
  return numbers.reduce((total, number) => total * 60 + number, 0);
}
