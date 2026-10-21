import { setFont } from "./text.ts";

/** `Hank` → `H`, `Walter White` → `WW`. */
export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter((part) => part !== "");
  const first = parts[0]?.slice(0, 1) ?? "?";
  const second = parts.length > 1 ? (parts[1]?.slice(0, 1) ?? "") : "";
  return `${first}${second}`.toUpperCase();
}

/** Shrinks a name until it fits the line, never below 14 px. */
export function fittedSize(
  ctx: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D,
  text: string,
  weight: 400 | 600 | 700,
  size: number,
  maxWidth: number,
): number {
  for (let current = size; current > 14; current -= 2) {
    setFont(ctx, weight, current);
    if (ctx.measureText(text).width <= maxWidth) return current;
  }
  return 14;
}
