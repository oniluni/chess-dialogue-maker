import type { FrameFaces } from "../../shared/people.ts";
import { getScheduleOptions } from "../session.ts";
import { HEIGHT, WIDTH } from "./canvas.ts";

export function sourceSize(source: CanvasImageSource): { width: number; height: number } {
  if (source instanceof HTMLVideoElement) return { width: source.videoWidth, height: source.videoHeight };
  if (source instanceof HTMLCanvasElement || source instanceof OffscreenCanvas || source instanceof ImageBitmap) {
    return { width: source.width, height: source.height };
  }
  return { width: WIDTH, height: HEIGHT };
}

/** The crop that keeps the faces in view, in source pixels; `null` means a plain centre crop. */
export function cropRect(
  frames: FrameFaces[] | null,
  time: number,
  sourceWidth: number,
  sourceHeight: number,
  boxHeight: number,
): { x: number; y: number; w: number; h: number } | null {
  if (frames === null || frames.length === 0) return null;

  let nearest: FrameFaces | undefined;
  let distance = Number.POSITIVE_INFINITY;
  for (const frame of frames) {
    const next = Math.abs(frame.time - time);
    if (next < distance) {
      distance = next;
      nearest = frame;
    }
  }
  if (nearest === undefined || nearest.boxes.length === 0) return null;

  let cx = 0;
  let cy = 0;
  for (const box of nearest.boxes) {
    cx += box.x + box.width / 2;
    cy += box.y + box.height / 2;
  }
  cx /= nearest.boxes.length;
  cy /= nearest.boxes.length;

  const aspect = WIDTH / boxHeight;
  const sourceAspect = sourceWidth / sourceHeight;
  const w = sourceAspect > aspect ? sourceHeight * aspect : sourceWidth;
  const h = sourceAspect > aspect ? sourceHeight : sourceWidth / aspect;
  const x = Math.max(0, Math.min(sourceWidth - w, cx * sourceWidth - w / 2));
  const y = Math.max(0, Math.min(sourceHeight - h, cy * sourceHeight - h / 2));
  return { x, y, w, h };
}

export function drawVideo(
  ctx: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D,
  source: CanvasImageSource,
  uiHeight: number,
  time: number,
  frames: FrameFaces[] | null,
): void {
  const boxHeight = HEIGHT - uiHeight;
  const size = sourceSize(source);
  if (size.width <= 0 || size.height <= 0) return;

  if (getScheduleOptions().format === "original") {
    const scale = Math.min(WIDTH / size.width, boxHeight / size.height);
    const width = size.width * scale;
    const height = size.height * scale;
    ctx.drawImage(source, (WIDTH - width) / 2, uiHeight + (boxHeight - height) / 2, width, height);
    return;
  }

  const crop = cropRect(frames, time, size.width, size.height, boxHeight);
  if (crop !== null) {
    ctx.drawImage(source, crop.x, crop.y, crop.w, crop.h, 0, uiHeight, WIDTH, boxHeight);
    return;
  }

  const scale = Math.max(WIDTH / size.width, boxHeight / size.height);
  const width = size.width * scale;
  const height = size.height * scale;
  ctx.drawImage(source, (WIDTH - width) / 2, uiHeight + (boxHeight - height) / 2, width, height);
}
