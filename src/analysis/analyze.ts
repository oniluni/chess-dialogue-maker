import type { LlmChatMessage } from "../../shared/llm.ts";
import { chat } from "../llm/api.ts";
import type { Names } from "../names.ts";
import { MOVE_TYPES, sanitizeMoves } from "./move.ts";
import type { Move, MoveType } from "./move.ts";
import { buildMovePrompt } from "./prompt.ts";
import { moveFromCandidate, reviewCandidates, shapeEvals, targetMoves } from "./quality.ts";
import type { MoveCandidate } from "./quality.ts";
import type { TranscriptSegment } from "./transcript.ts";

export interface AnalysisAttempt {
  index: number;
  issues: string[];
  reply: string;
  candidates: MoveCandidate[] | null;
}

export type AnalysisResult =
  | { ok: true; moves: Move[]; attempts: AnalysisAttempt[] }
  | { ok: false; attempts: AnalysisAttempt[] };

export interface AnalyzeOptions {
  segments: TranscriptSegment[];
  names: Names;
  provider: string;
  model: string;
  duration: number | null;
  onAttempt?: (attempt: number, total: number) => void;
}

const MAX_ATTEMPTS = 3;

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

function toCandidates(value: unknown, total: number): MoveCandidate[] {
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
    const knownType: MoveType | undefined = MOVE_TYPES.find((type) => type === moveType);
    if (knownType === undefined) throw new Error(`${path}: unknown move_type "${String(moveType)}"`);

    const strength = raw["strength"];
    return {
      firstSegment,
      lastSegment,
      speaker,
      moveType: knownType,
      strength: typeof strength === "number" ? strength : null,
      gist: textField(raw["gist"], path, "gist"),
      title: textField(raw["title"], path, "title"),
      explanation: textField(raw["explanation"], path, "explanation"),
      evalScore: evalField(raw["eval_score"], path),
    };
  });
}

/** Asks the model for moves, checks them and retries with the found problems. */
export async function analyzeDialogue(options: AnalyzeOptions): Promise<AnalysisResult> {
  const targets = targetMoves(options.segments);
  const prompt = buildMovePrompt(options.segments, options.names, targets);
  const attempts: AnalysisAttempt[] = [];
  let messages: LlmChatMessage[] = prompt.messages;

  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt += 1) {
    options.onAttempt?.(attempt, MAX_ATTEMPTS);
    let reply = "";
    let candidates: MoveCandidate[] | null = null;
    let issues: string[] = [];

    try {
      reply = await chat({
        provider: options.provider,
        model: options.model,
        messages,
        schema: prompt.schema,
        temperature: 0.4,
      });
      candidates = toCandidates(parseJson(reply), options.segments.length);
      issues = reviewCandidates(candidates, options.segments, targets.min);
    } catch (error) {
      issues = [error instanceof Error ? error.message : String(error)];
    }

    attempts.push({ index: attempt, issues, reply, candidates });
    if (candidates !== null && issues.length === 0) {
      const moves = shapeEvals(
        sanitizeMoves(
          candidates.map((candidate) => moveFromCandidate(candidate, options.segments, options.names)),
          options.duration,
        ),
      );
      return { ok: true, moves, attempts };
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

  return { ok: false, attempts };
}
