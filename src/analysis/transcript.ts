/** The dialogue lines the analysis works on, as `transcript.json` stores them. */

export interface TranscriptSegment {
  id: number;
  start: number;
  end: number;
  text: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function seconds(value: unknown, path: string, field: string): number {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  throw new Error(`${path}: "${field}" must be a number of seconds`);
}

/** Reads `{"segments": [...]}` or a bare array; renumbers and sorts by time. */
export function parseTranscript(text: string): TranscriptSegment[] {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch (error) {
    throw new Error(`not valid JSON: ${error instanceof Error ? error.message : String(error)}`);
  }

  const list = Array.isArray(data) ? data : isRecord(data) && Array.isArray(data["segments"]) ? data["segments"] : null;
  if (list === null) throw new Error('expected {"segments": [...]}');

  const parsed = list.map((raw, index): TranscriptSegment => {
    const path = `segments[${index}]`;
    if (!isRecord(raw)) throw new Error(`${path}: expected an object`);
    const start = seconds(raw["start"], path, "start");
    const end = seconds(raw["end"], path, "end");
    const line = raw["text"];
    if (typeof line !== "string" || line.trim() === "") throw new Error(`${path}: "text" is empty`);
    if (end <= start) throw new Error(`${path}: "end" must be after "start"`);
    return { id: index, start, end, text: line.replace(/\s+/g, " ").trim() };
  });

  if (parsed.length === 0) throw new Error("the transcript has no lines");
  return parsed
    .sort((left, right) => left.start - right.start)
    .map((segment, index) => ({ ...segment, id: index }));
}
