/** Canvas text helpers: the renderer measures every line itself. */

export const FONT_STACK = '"Google Sans", Inter, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';

export function setFont(
  ctx: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D,
  weight: 400 | 600 | 700,
  size: number,
): void {
  ctx.font = `${weight} ${Math.max(8, Math.round(size))}px ${FONT_STACK}`;
}

/** Greedy word wrap; a single word longer than the line is left as is. */
export function wrapLines(
  ctx: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D,
  text: string,
  maxWidth: number,
): string[] {
  const words = text.split(/\s+/).filter((word) => word !== "");
  if (words.length === 0) return [];
  const lines: string[] = [];
  let current = "";
  for (const word of words) {
    const candidate = current === "" ? word : `${current} ${word}`;
    if (current === "" || ctx.measureText(candidate).width <= maxWidth) {
      current = candidate;
      continue;
    }
    lines.push(current);
    current = word;
  }
  if (current !== "") lines.push(current);
  return lines;
}

/** Trims a line until it fits, with an ellipsis. */
export function ellipsize(
  ctx: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D,
  line: string,
  maxWidth: number,
): string {
  if (ctx.measureText(line).width <= maxWidth) return line;
  let text = line;
  while (text.length > 0 && ctx.measureText(`${text}…`).width > maxWidth) {
    text = text.slice(0, -1);
  }
  return `${text.trimEnd()}…`;
}
