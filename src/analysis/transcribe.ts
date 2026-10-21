import type { TranscriptionInfo, TranscriptionResponse } from "../../shared/transcribe.ts";
import { parseTranscript } from "./transcript.ts";
import type { TranscriptSegment } from "./transcript.ts";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (isRecord(body) && typeof body["title"] === "string") {
      const hint = typeof body["hint"] === "string" ? body["hint"] : "";
      return hint === "" ? body["title"] : `${body["title"]} — ${hint}`;
    }
  } catch {
    // not JSON: fall through to the status line
  }
  return `transcription failed: HTTP ${response.status}`;
}

/** Which model and language the backend defaults to. */
export async function transcriptionInfo(): Promise<TranscriptionInfo> {
  const response = await fetch("/api/transcribe");
  if (!response.ok) throw new Error(await errorMessage(response));
  const data: unknown = await response.json();
  const model = isRecord(data) ? data["model"] : undefined;
  const language = isRecord(data) ? data["language"] : undefined;
  if (typeof model !== "string" || typeof language !== "string") {
    throw new Error("unexpected transcription settings");
  }
  return { model, language };
}

/** Sends the WAV to the local model and returns the renumbered lines. */
export async function transcribeWav(wav: Blob, language: string): Promise<TranscriptSegment[]> {
  const response = await fetch(`/api/transcribe?language=${encodeURIComponent(language)}`, {
    method: "POST",
    headers: { "content-type": "audio/wav" },
    body: wav,
  });
  if (!response.ok) throw new Error(await errorMessage(response));

  const data: unknown = await response.json();
  if (!isRecord(data) || !Array.isArray(data["segments"])) throw new Error("unexpected transcription answer");
  const body: TranscriptionResponse = {
    segments: data["segments"] as TranscriptionResponse["segments"],
    language: typeof data["language"] === "string" ? data["language"] : language,
    model: typeof data["model"] === "string" ? data["model"] : "",
  };
  // parseTranscript validates the lines and renumbers them.
  return parseTranscript(JSON.stringify({ segments: body.segments }));
}
