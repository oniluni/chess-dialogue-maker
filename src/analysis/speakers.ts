import type { DiarizationRequest, DiarizationResponse, SpeakerHint } from "../../shared/speakers.ts";
import { errorMessage, isRecord } from "../net/http.ts";

async function toBase64(blob: Blob): Promise<string> {
  const buffer = await blob.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let index = 0; index < bytes.length; index += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000));
  }
  return btoa(binary);
}

/** Asks the backend which of the two voices speaks each line. */
export async function detectSpeakers(
  wav: Blob,
  segments: Array<{ start: number; end: number }>,
): Promise<DiarizationResponse> {
  const request: DiarizationRequest = { wav: await toBase64(wav), segments };
  const response = await fetch("/api/speakers", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!response.ok) throw new Error(await errorMessage(response));

  const data: unknown = await response.json();
  if (!isRecord(data) || !Array.isArray(data["hints"])) throw new Error("unexpected speaker answer");
  const hints = data["hints"].flatMap((raw): SpeakerHint[] => {
    if (!isRecord(raw)) return [];
    const label = raw["label"];
    return [
      {
        label: label === "A" || label === "B" ? label : null,
        confidence: typeof raw["confidence"] === "number" ? raw["confidence"] : 0,
      },
    ];
  });
  return { hints, reliability: typeof data["reliability"] === "number" ? data["reliability"] : 0 };
}
