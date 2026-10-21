import type { SoundDetection } from "../../shared/sounds.ts";
import { extractWav } from "../media/wav.ts";
import { errorMessage, isRecord } from "../net/http.ts";
import { getSpeakerHints, getTranscript } from "../session.ts";

function toBase64(bytes: Uint8Array): string {
  let binary = "";
  for (let index = 0; index < bytes.length; index += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000));
  }
  return btoa(binary);
}

function detectionOf(raw: unknown): SoundDetection | null {
  if (!isRecord(raw)) return null;
  const kind = raw["kind"];
  const label = raw["label"];
  const start = raw["start"];
  const end = raw["end"];
  const peak = raw["peak"];
  if (typeof kind !== "string" || typeof label !== "string") return null;
  if ([start, end, peak].some((value) => typeof value !== "number")) return null;
  return {
    kind,
    label,
    start: start as number,
    end: end as number,
    peak: peak as number,
    reliable: raw["reliable"] === true,
  };
}

/** Compares each line with the voice's usual manner: louder, quieter, higher, faster, slower, pauses. */
export async function detectDelivery(file: File): Promise<SoundDetection[]> {
  const wav = await extractWav(file);
  const base64 = toBase64(new Uint8Array(await wav.arrayBuffer()));
  const hints = getSpeakerHints();
  const segments = getTranscript().map((segment, index) => {
    const hint = hints?.hints[index];
    const label =
      hint === undefined || hint.confidence < 0.6 ? null : hint.label === "A" || hint.label === "B" ? hint.label : null;
    return { start: segment.start, end: segment.end, label };
  });

  const response = await fetch("/api/prosody", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ wav: base64, segments }),
  });
  if (!response.ok) throw new Error(await errorMessage(response));

  const data: unknown = await response.json();
  if (!isRecord(data) || !Array.isArray(data["events"])) throw new Error("unexpected delivery answer");
  return data["events"].flatMap((raw) => detectionOf(raw) ?? []);
}
