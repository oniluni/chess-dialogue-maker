import type { FacesResult, FrameFaces, PersonInfo } from "../../shared/people.ts";
import { CanvasSink } from "mediabunny";
import { errorMessage, isRecord } from "../net/http.ts";

const SIZE = 320;
const MAX_FRAMES = 24;

function toBase64(bytes: Uint8Array): string {
  let binary = "";
  for (let index = 0; index < bytes.length; index += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000));
  }
  return btoa(binary);
}

function rgbBytes(pixels: Uint8ClampedArray): Uint8Array {
  const out = new Uint8Array((pixels.length / 4) * 3);
  for (let source = 0, target = 0; source < pixels.length; source += 4, target += 3) {
    out[target] = pixels[source] ?? 0;
    out[target + 1] = pixels[source + 1] ?? 0;
    out[target + 2] = pixels[source + 2] ?? 0;
  }
  return out;
}

function personOf(raw: unknown): PersonInfo | null {
  if (!isRecord(raw)) return null;
  const id = raw["id"];
  const frames = raw["frames"];
  const share = raw["share"];
  const preview = raw["preview"];
  if (typeof id !== "number" || typeof frames !== "number" || typeof share !== "number" || !isRecord(preview)) {
    return null;
  }
  const time = preview["time"];
  const x = preview["x"];
  const y = preview["y"];
  const width = preview["width"];
  const height = preview["height"];
  if ([time, x, y, width, height].some((value) => typeof value !== "number")) return null;
  return {
    id,
    frames,
    share,
    preview: { time: time as number, x: x as number, y: y as number, width: width as number, height: height as number },
  };
}

function frameOf(raw: unknown): FrameFaces | null {
  if (!isRecord(raw) || typeof raw["time"] !== "number" || !Array.isArray(raw["boxes"])) return null;
  const boxes = raw["boxes"].flatMap((box): FrameFaces["boxes"] => {
    if (!isRecord(box)) return [];
    const x = box["x"];
    const y = box["y"];
    const width = box["width"];
    const height = box["height"];
    const person = box["person"];
    if ([x, y, width, height, person].some((value) => typeof value !== "number")) return [];
    return [{ x: x as number, y: y as number, width: width as number, height: height as number, person: person as number }];
  });
  return { time: raw["time"], boxes };
}

/** Samples about one frame per second, letterboxed to 320×320, and asks the local models. */
export async function detectPeople(file: File, start: number, end: number): Promise<FacesResult> {
  const { ALL_FORMATS, BlobSource, Input } = await import("mediabunny");
  const input = new Input({ formats: ALL_FORMATS, source: new BlobSource(file) });
  try {
    const track = await input.getPrimaryVideoTrack();
    if (track === null) throw new Error("the clip has no video track");
    const frameWidth = track.displayWidth;
    const frameHeight = track.displayHeight;

    const sink = new CanvasSink(track, { width: SIZE, height: SIZE, fit: "contain" });
    const span = Math.max(0.1, end - start);
    const count = Math.max(1, Math.min(MAX_FRAMES, Math.round(span)));
    const frames: Array<{ time: number; rgb: string }> = [];

    for (let index = 0; index < count; index += 1) {
      const time = start + (span * (index + 0.5)) / count;
      const canvas = await sink.getCanvas(time);
      if (canvas === null) continue;
      const context = canvas.canvas.getContext("2d");
      if (context === null) continue;
      frames.push({
        time,
        rgb: toBase64(rgbBytes(context.getImageData(0, 0, canvas.canvas.width, canvas.canvas.height).data)),
      });
    }
    if (frames.length === 0) throw new Error("no frames were decoded");

    const response = await fetch("/api/faces", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ frames: frames.map((frame) => ({ ...frame, width: SIZE, height: SIZE })) }),
    });
    if (!response.ok) throw new Error(await errorMessage(response));

    const data: unknown = await response.json();
    if (!isRecord(data)) throw new Error("unexpected faces answer");
    const persons = Array.isArray(data["persons"]) ? data["persons"].flatMap((raw) => personOf(raw) ?? []) : [];
    const mapped = Array.isArray(data["frames"]) ? data["frames"].flatMap((raw) => frameOf(raw) ?? []) : [];

    const scale = Math.min(SIZE / frameWidth, SIZE / frameHeight);
    const offsetX = (SIZE - frameWidth * scale) / 2;
    const offsetY = (SIZE - frameHeight * scale) / 2;
    const toX = (value: number): number => (value * SIZE - offsetX) / scale / frameWidth;
    const toY = (value: number): number => (value * SIZE - offsetY) / scale / frameHeight;

    for (const frame of mapped) {
      for (const box of frame.boxes) {
        box.x = toX(box.x);
        box.y = toY(box.y);
        box.width = (box.width * SIZE) / scale / frameWidth;
        box.height = (box.height * SIZE) / scale / frameHeight;
      }
    }
    for (const person of persons) {
      person.preview.x = toX(person.preview.x);
      person.preview.y = toY(person.preview.y);
      person.preview.width = (person.preview.width * SIZE) / scale / frameWidth;
      person.preview.height = (person.preview.height * SIZE) / scale / frameHeight;
    }
    return { persons, frames: mapped };
  } finally {
    input.dispose();
  }
}
