import type { SoundDetection } from "../../shared/sounds.ts";
import { extractWav } from "../media/wav.ts";
import { errorMessage, isRecord } from "../net/http.ts";

function detectionOf(raw: unknown): SoundDetection | null {
  if (!isRecord(raw)) return null;
  const kind = raw["kind"];
  const label = raw["label"];
  const start = raw["start"];
  const end = raw["end"];
  const peak = raw["peak"];
  const reliable = raw["reliable"];
  if (typeof kind !== "string" || typeof label !== "string") return null;
  if ([start, end, peak].some((value) => typeof value !== "number")) return null;
  return {
    kind,
    label,
    start: start as number,
    end: end as number,
    peak: peak as number,
    reliable: reliable === true,
  };
}

/** Sends the clip's audio to the backend and returns what YAMNet heard. */
export async function detectSounds(file: File): Promise<SoundDetection[]> {
  const wav = await extractWav(file);
  const response = await fetch("/api/sounds", {
    method: "POST",
    headers: { "content-type": "audio/wav" },
    body: wav,
  });
  if (!response.ok) throw new Error(await errorMessage(response));

  const data: unknown = await response.json();
  if (!isRecord(data) || !Array.isArray(data["events"])) throw new Error("unexpected sounds answer");
  return data["events"].flatMap((raw) => detectionOf(raw) ?? []);
}
