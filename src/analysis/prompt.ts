import type { LlmChatMessage } from "../../shared/llm.ts";
import type { SpeakerHint } from "../../shared/speakers.ts";
import type { Names } from "../names.ts";
import type { Side } from "./move.ts";
import type { TranscriptSegment } from "./transcript.ts";

export interface SpeakerContext {
  hints: SpeakerHint[];
  sides: Record<"A" | "B", Side>;
}

export interface MovePrompt {
  messages: LlmChatMessage[];
  schema: Record<string, unknown>;
}

const SYSTEM = `You are a chess coach reviewing a dialogue as if it were a chess game.

Turn the dialogue into "moves" of a Chess.com-style game review.

Rules:
- Every move covers a consecutive range of the numbered dialogue lines. Together the moves must cover the whole clip, in order, without overlaps.
- Use the number of moves the request asks for: fewer and the review drags, more and the cards blur together.
- effect: one sentence about what actually changed in the exchange. Think about it before you classify.
- move_type is one of: Brilliant, Great, Best, Inaccuracy, Mistake, Blunder.
- strength is an integer from -3 to +3: how good the exchange was for the player making it. It must agree with move_type.
- speaker is "white" or "black" — the player who makes this move.
- gist is a 2 to 5 word summary of the exchange.
- title is a short hook: what happened first, then a dash and the verdict, at most 8 words. Never quote the dialogue, never start with the verdict.
- explanation is 1 or 2 sentences about what actually happened: which words changed the balance and why. Write it in the language of the dialogue.
- eval_score is the position after the move, from -10 (Black is winning) to +10 (White is winning).
- Never invent facts that are not in the dialogue. No clichés, no "it is worth noting".

Answer with JSON only, exactly this shape: {"moves": [...]}`;

const FIELD_ORDER = [
  "first_segment",
  "last_segment",
  "speaker",
  "effect",
  "strength",
  "move_type",
  "eval_score",
  "gist",
  "title",
  "explanation",
] as const;

function itemSchema(): Record<string, unknown> {
  const properties: Record<string, unknown> = {
    first_segment: { type: "integer" },
    last_segment: { type: "integer" },
    speaker: { type: "string", enum: ["white", "black"] },
    effect: { type: "string" },
    strength: { type: "integer", minimum: -3, maximum: 3 },
    move_type: {
      type: "string",
      enum: ["Brilliant", "Great", "Best", "Inaccuracy", "Mistake", "Blunder"],
    },
    eval_score: { type: "number", minimum: -10, maximum: 10 },
    gist: { type: "string" },
    title: { type: "string" },
    explanation: { type: "string" },
  };
  return {
    type: "object",
    properties: Object.fromEntries(FIELD_ORDER.map((field) => [field, properties[field]])),
    required: [...FIELD_ORDER],
  };
}

export function moveSchema(targets: { min: number; max: number }): Record<string, unknown> {
  return {
    type: "object",
    properties: {
      moves: {
        type: "array",
        minItems: targets.min,
        maxItems: targets.max,
        items: itemSchema(),
      },
    },
    required: ["moves"],
  };
}

function clock(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const rest = (seconds % 60).toFixed(1).padStart(4, "0");
  return `${minutes}:${rest}`;
}

function hintTag(hint: SpeakerHint | undefined): string {
  if (hint === undefined || hint.label === null || hint.confidence < 0.8) return "";
  return `voice ${hint.label} (${Math.round(hint.confidence * 100)}%) | `;
}

/** The dialogue lines as the model sees them, with the voice tags when they are known. */
export function transcriptLines(segments: TranscriptSegment[], speakers: SpeakerContext | null): string {
  return segments
    .map(
      (segment) =>
        `${segment.id} [${clock(segment.start)}–${clock(segment.end)}] ${hintTag(speakers?.hints[segment.id])}${segment.text}`,
    )
    .join("\n");
}

export function buildMovePrompt(
  segments: TranscriptSegment[],
  names: Names,
  targets: { min: number; max: number },
  speakers: SpeakerContext | null = null,
  sounds: Array<{ start: number; label: string }> = [],
): MovePrompt {
  const mapping =
    speakers === null
      ? ""
      : `\nLines marked voice A or voice B are spoken by different people. One move never mixes labels, ` +
        `and speaker must follow the mapping: voice A is ${names[speakers.sides.A]}, voice B is ${names[speakers.sides.B]}.`;

  const soundLines =
    sounds.length === 0
      ? ""
      : `\n\nSound events (context only, never invent them as dialogue):\n${sounds
          .map((sound) => `${clock(sound.start)} — ${sound.label}`)
          .join("\n")}`;

  return {
    messages: [
      { role: "system", content: SYSTEM },
      {
        role: "user",
        content:
          `Players: White is "${names.white}", Black is "${names.black}".\n` +
          `Write between ${targets.min} and ${targets.max} moves.${mapping}\n\n` +
          `Dialogue lines:\n${transcriptLines(segments, speakers)}${soundLines}`,
      },
    ],
    schema: moveSchema(targets),
  };
}
