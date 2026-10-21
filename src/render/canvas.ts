/** The canvas the preview and the export share; the format decides its size. */
export let WIDTH = 1080;
export let HEIGHT = 1920;

export function setCanvasSize(format: "vertical" | "original" | "landscape"): void {
  WIDTH = format === "landscape" ? 1920 : 1080;
  HEIGHT = format === "landscape" ? 1080 : 1920;
}
