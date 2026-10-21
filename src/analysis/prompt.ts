import type { LlmChatMessage } from "../../shared/llm.ts";
import type { Names } from "../names.ts";
import type { TranscriptSegment } from "./transcript.ts";

export interface MovePrompt {
  messages: LlmChatMessage[];
  schema: Record<string, unknown>;
}

const SYSTEM = `You are a chess coach reviewing a dialogue as if it were a chess game.

Turn the dialogue into "moves" of a Chess.com-style game review.

Rules:
- Every move covers a consecutive range of the numbered dialogue lines. Together the moves must cover the whole clip, in order, without overlaps.
- move_type is one of: Brilliant, Great, Best, Inaccuracy, Mistake, Blunder.
- strength is an integer from -3 to +3: how good the exchange was for the player making it.
- speaker is "white" or "black" — the player who makes this move.
- gist is a 2 to 5 word summary of the exchange.
- title is a short hook, at most 8 words. Never quote the dialogue and never use clichés like "a brilliant move".
- explanation is 1 or 2 sentences about what actually happened: which words changed the balance and why. Write it in the language of the dialogue.
- eval_score is the position after the move, from -10 (Black is winning) to +10 (White is winning).
- first_segment and last_segment are the indices of the numbered lines this move covers.
- Never invent facts that are not in the dialogue.

Answer with JSON only, exactly this shape: {"moves": [...]}`;

export const MOVE_SCHEMA: Record<string, unknown> = {
  type: "object",
  properties: {
    moves: {
      type: "array",
      items: {
        type: "object",
        properties: {
          first_segment: { type: "integer" },
          last_segment: { type: "integer" },
          speaker: { type: "string", enum: ["white", "black"] },
          move_type: {
            type: "string",
            enum: ["Brilliant", "Great", "Best", "Inaccuracy", "Mistake", "Blunder"],
          },
          strength: { type: "integer", minimum: -3, maximum: 3 },
          gist: { type: "string" },
          title: { type: "string" },
          explanation: { type: "string" },
          eval_score: { type: "number", minimum: -10, maximum: 10 },
        },
        required: [
          "first_segment",
          "last_segment",
          "speaker",
          "move_type",
          "strength",
          "gist",
          "title",
          "explanation",
          "eval_score",
        ],
      },
    },
  },
  required: ["moves"],
};

function clock(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const rest = (seconds % 60).toFixed(1).padStart(4, "0");
  return `${minutes}:${rest}`;
}

/** The dialogue lines as the model sees them. */
export function transcriptLines(segments: TranscriptSegment[]): string {
  return segments
    .map((segment) => `${segment.id} [${clock(segment.start)}–${clock(segment.end)}] ${segment.text}`)
    .join("\n");
}

export function buildMovePrompt(segments: TranscriptSegment[], names: Names): MovePrompt {
  return {
    messages: [
      { role: "system", content: SYSTEM },
      {
        role: "user",
        content:
          `Players: White is "${names.white}", Black is "${names.black}".\n\n` +
          `Dialogue lines:\n${transcriptLines(segments)}`,
      },
    ],
    schema: MOVE_SCHEMA,
  };
}
