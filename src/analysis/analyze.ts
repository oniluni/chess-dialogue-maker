import type { LlmChatMessage } from "../../shared/llm.ts";
import { chat } from "../llm/api.ts";
import type { Names } from "../names.ts";
import { MOVE_TYPES, sanitizeMoves } from "./move.ts";
import type { Move, MoveType, Side } from "./move.ts";
import { buildMovePrompt } from "./prompt.ts";
import type { TranscriptSegment } from "./transcript.ts";

export interface AnalyzeOptions {
  segments: TranscriptSegment[];
  names: Names;
  provider: string;
  model: string;
  duration: number | null;
  onAttempt?: (attempt: number, total: number) => void;
}

interface Draft {
  firstSegment: number;
  lastSegment: number;
  speaker: Side;
  moveType: MoveType;
  strength: number;
  gist: string;
  title: string;
  explanation: string;
  evalScore: number;
}

const MAX_ATTEMPTS = 3;
const MIN_COVERAGE = 0.8;
const MIN_MOVES = 2;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseJson(text: string): unknown {
  const trimmed = text.trim().replace(/^```(?:json)?\s*/i, "").replace(/```$/i, "").trim();
  try {
    return JSON.parse(trimmed) as unknown;
  } catch (error) {
    throw new Error(`not valid JSON: ${error instanceof Error ? error.message : String(error)}`);
  }
}

function integer(value: unknown, path: string, field: string, min: number, max: number): number {
  if (typeof value === "number" && Number.isInteger(value) && value >= min && value <= max) return value;
  throw new Error(`${path}: "${field}" must be an integer from ${min} to ${max}`);
}

function textField(value: unknown, path: string, field: string): string {
  if (typeof value !== "string" || value.trim() === "") {
    throw new Error(`${path}: "${field}" must be a non-empty string`);
  }
  return value.replace(/\s+/g, " ").trim();
}

function evalField(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${path}: "eval_score" must be a number`);
  }
  return Math.round(Math.min(10, Math.max(-10, value)) * 10) / 10;
}

function toDrafts(value: unknown, total: number): Draft[] {
  if (!isRecord(value)) throw new Error('expected {"moves": [...]}');
  const list = value["moves"];
  if (!Array.isArray(list) || list.length === 0) throw new Error('"moves" must be a non-empty array');

  return list.map((raw, index) => {
    const path = `moves[${index}]`;
    if (!isRecord(raw)) throw new Error(`${path}: expected an object`);
    const firstSegment = integer(raw["first_segment"], path, "first_segment", 0, total - 1);
    const lastSegment = integer(raw["last_segment"], path, "last_segment", 0, total - 1);
    if (lastSegment < firstSegment) throw new Error(`${path}: last_segment is before first_segment`);

    const speaker = raw["speaker"];
    if (speaker !== "white" && speaker !== "black") throw new Error(`${path}: "speaker" must be white or black`);

    const moveType = raw["move_type"];
    const knownType = MOVE_TYPES.find((type) => type === moveType);
    if (knownType === undefined) throw new Error(`${path}: unknown move_type "${String(moveType)}"`);

    return {
      firstSegment,
      lastSegment,
      speaker,
      moveType: knownType,
      strength: integer(raw["strength"], path, "strength", -3, 3),
      gist: textField(raw["gist"], path, "gist"),
      title: textField(raw["title"], path, "title"),
      explanation: textField(raw["explanation"], path, "explanation"),
      evalScore: evalField(raw["eval_score"], path),
    };
  });
}

function review(drafts: Draft[], total: number): string[] {
  const issues: string[] = [];
  if (drafts.length < MIN_MOVES) {
    issues.push(`only ${drafts.length} moves; the review needs at least ${MIN_MOVES}`);
  }

  let covered = 0;
  let previousEnd = -1;
  for (const [index, draft] of drafts.entries()) {
    if (draft.firstSegment <= previousEnd) issues.push(`move ${index + 1} overlaps the previous one`);
    previousEnd = Math.max(previousEnd, draft.lastSegment);
    covered += draft.lastSegment - draft.firstSegment + 1;
    if (draft.title.length > 80) issues.push(`move ${index + 1}: the title is longer than 80 characters`);
    if (draft.explanation.length < 30) issues.push(`move ${index + 1}: the explanation is too short`);
  }

  if (total > 0 && covered / total < MIN_COVERAGE) {
    issues.push(`the moves cover only ${Math.round((covered / total) * 100)}% of the dialogue lines`);
  }
  return issues;
}

function toMoves(drafts: Draft[], segments: TranscriptSegment[], names: Names, duration: number | null): Move[] {
  const moves = drafts.map((draft): Move => {
    const first = segments[draft.firstSegment];
    const last = segments[draft.lastSegment];
    if (first === undefined || last === undefined) throw new Error("a move points outside the dialogue");
    return {
      start: first.start,
      end: last.end,
      speaker: draft.speaker,
      moveNumber: `${names[draft.speaker]} · ${draft.gist}`,
      moveType: draft.moveType,
      title: draft.title,
      explanation: draft.explanation,
      evalScore: draft.evalScore,
    };
  });
  return sanitizeMoves(moves, duration);
}

/** Asks the model for moves, validates them and retries with the found problems. */
export async function analyzeDialogue(options: AnalyzeOptions): Promise<Move[]> {
  const prompt = buildMovePrompt(options.segments, options.names);
  let messages: LlmChatMessage[] = prompt.messages;
  let issues: string[] = [];

  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt += 1) {
    options.onAttempt?.(attempt, MAX_ATTEMPTS);
    let reply = "";
    try {
      reply = await chat({
        provider: options.provider,
        model: options.model,
        messages,
        schema: prompt.schema,
        temperature: 0.4,
      });
      const drafts = toDrafts(parseJson(reply), options.segments.length);
      issues = review(drafts, options.segments.length);
      if (issues.length === 0) {
        return toMoves(drafts, options.segments, options.names, options.duration);
      }
    } catch (error) {
      issues = [error instanceof Error ? error.message : String(error)];
    }
    messages = [
      ...prompt.messages,
      { role: "assistant", content: reply.slice(0, 6000) },
      {
        role: "user",
        content:
          `Your answer failed the checks:\n- ${issues.join("\n- ")}\n` +
          'Return only corrected JSON in the shape {"moves": [...]}.',
      },
    ];
  }

  throw new Error(`the model did not produce a valid analysis: ${issues.join("; ")}`);
}
