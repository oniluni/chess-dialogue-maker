/** The UI is designed at 1080×1920; the canvas may be wider (landscape) around it. */
export const WIDTH = 1080;
export const HEIGHT = 1920;

export let CANVAS_W = 1080;
export let CANVAS_H = 1920;

export function setCanvasSize(format: "vertical" | "original" | "landscape"): void {
  CANVAS_W = format === "landscape" ? 1920 : 1080;
  CANVAS_H = format === "landscape" ? 1080 : 1920;
}
