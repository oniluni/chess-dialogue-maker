import { ALL_FORMATS, BlobSource, CanvasSink, Input } from "mediabunny";

/** Crops one face from the clip at `time` and returns a square PNG data URL. */
export async function cropAvatar(
  file: File,
  time: number,
  box: { x: number; y: number; width: number; height: number },
): Promise<string> {
  const input = new Input({ formats: ALL_FORMATS, source: new BlobSource(file) });
  try {
    const track = await input.getPrimaryVideoTrack();
    if (track === null) throw new Error("the clip has no video track");
    const width = track.displayWidth;
    const height = track.displayHeight;
    const sink = new CanvasSink(track, { width, height });
    const canvas = await sink.getCanvas(time);
    if (canvas === null) throw new Error("the frame is not available");

    const size = Math.max(48, Math.max(box.width * width, box.height * height) * 1.4);
    const cx = (box.x + box.width / 2) * width;
    const cy = (box.y + box.height / 2) * height;
    const sx = Math.max(0, Math.min(width - size, cx - size / 2));
    const sy = Math.max(0, Math.min(height - size, cy - size / 2));

    const out = document.createElement("canvas");
    out.width = 128;
    out.height = 128;
    const target = out.getContext("2d");
    if (target === null) throw new Error("no 2d context");
    target.drawImage(canvas.canvas, sx, sy, size, size, 0, 0, 128, 128);
    return out.toDataURL("image/png");
  } finally {
    input.dispose();
  }
}
