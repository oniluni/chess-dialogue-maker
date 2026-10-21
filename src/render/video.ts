import type { FrameFaces } from "../../shared/people.ts";
import { getScheduleOptions } from "../session.ts";
import { CANVAS_H, CANVAS_W } from "./canvas.ts";

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export function sourceSize(source: CanvasImageSource): { width: number; height: number } {
  if (source instanceof HTMLVideoElement) return { width: source.videoWidth, height: source.videoHeight };
  if (source instanceof HTMLCanvasElement || source instanceof OffscreenCanvas || source instanceof ImageBitmap) {
    return { width: source.width, height: source.height };
  }
  return { width: CANVAS_W, height: CANVAS_H };
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

  const aspect = CANVAS_W / boxHeight;
  const sourceAspect = sourceWidth / sourceHeight;
  const w = sourceAspect > aspect ? sourceHeight * aspect : sourceWidth;
  const h = sourceAspect > aspect ? sourceHeight : sourceWidth / aspect;
  const x = Math.max(0, Math.min(sourceWidth - w, cx * sourceWidth - w / 2));
  const y = Math.max(0, Math.min(sourceHeight - h, cy * sourceHeight - h / 2));
  return { x, y, w, h };
}

/** Draws the video into an arbitrary rectangle: fitted for `original`, cropped to the faces otherwise. */
export function drawVideoIn(
  ctx: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D,
  source: CanvasImageSource,
  rect: Rect,
  time: number,
  frames: FrameFaces[] | null,
): void {
  const size = sourceSize(source);
  if (size.width <= 0 || size.height <= 0) return;

  if (getScheduleOptions().format === "original") {
    const scale = Math.min(rect.w / size.width, rect.h / size.height);
    const width = size.width * scale;
    const height = size.height * scale;
    ctx.drawImage(source, rect.x + (rect.w - width) / 2, rect.y + (rect.h - height) / 2, width, height);
    return;
  }

  const crop = cropRect(frames, time, size.width, size.height, rect.h);
  if (crop !== null) {
    ctx.drawImage(source, crop.x, crop.y, crop.w, crop.h, rect.x, rect.y, rect.w, rect.h);
    return;
  }

  const scale = Math.max(rect.w / size.width, rect.h / size.height);
  const width = size.width * scale;
  const height = size.height * scale;
  ctx.drawImage(source, rect.x + (rect.w - width) / 2, rect.y + (rect.h - height) / 2, width, height);
}

export function drawVideo(
  ctx: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D,
  source: CanvasImageSource,
  uiHeight: number,
  time: number,
  frames: FrameFaces[] | null,
): void {
  drawVideoIn(ctx, source, { x: 0, y: uiHeight, w: CANVAS_W, h: CANVAS_H - uiHeight }, time, frames);
}
