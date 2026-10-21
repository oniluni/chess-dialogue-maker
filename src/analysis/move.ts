/** One chess.com-style move of the review: the unit stored in `moves.json`. */

export const MOVE_TYPES = ["Brilliant", "Great", "Best", "Inaccuracy", "Mistake", "Blunder"] as const;

export type MoveType = (typeof MOVE_TYPES)[number];

export type Side = "white" | "black";

export const MOVE_GLYPHS: Record<MoveType, string> = {
  Brilliant: "!!",
  Great: "!",
  Best: "★",
  Inaccuracy: "?!",
  Mistake: "?",
  Blunder: "??",
};

export const SIDE_LABELS: Record<Side, string> = { white: "White", black: "Black" };

/** The chess.com palette the old renderer used for cards and the move badge. */
export const MOVE_COLORS: Record<MoveType, string> = {
  Brilliant: "#1baca6",
  Great: "#5c8bb0",
  Best: "#96bc4b",
  Inaccuracy: "#f0c15c",
  Mistake: "#e6912c",
  Blunder: "#ca3431",
};

/** `+1.2`, `-0.5`, `0.0` — the eval bar label. */
export function formatEval(score: number): string {
  const rounded = Math.round(score * 10) / 10;
  if (rounded === 0) return "0.0";
  return rounded > 0 ? `+${rounded.toFixed(1)}` : rounded.toFixed(1);
}

/** Index of the move that owns `time`, or -1 before the first one. */
export function activeMoveIndex(moves: Move[], time: number): number {
  let index = -1;
  for (let position = 0; position < moves.length; position += 1) {
    const move = moves[position];
    if (move === undefined || move.start > time) break;
    index = position;
  }
  return index;
}

export interface Move {
  start: number;
  end: number;
  speaker: Side;
  moveNumber: string;
  moveType: MoveType;
  title: string;
  explanation: string;
  evalScore: number;
}

/** Aliases old `moves.json` files may contain: Russian labels, glyphs, English synonyms. */
const TYPE_ALIASES: ReadonlyMap<string, MoveType> = new Map([
  ...MOVE_TYPES.map((type) => [type.toLowerCase(), type] as const),
  ["!!", "Brilliant"],
  ["!", "Great"],
  ["★", "Best"],
  ["*", "Best"],
  ["?!", "Inaccuracy"],
  ["??", "Blunder"],
  ["блестящий ход", "Brilliant"],
  ["блестящий", "Brilliant"],
  ["отличный ход", "Great"],
  ["отличный", "Great"],
  ["excellent", "Great"],
  ["лучший ход", "Best"],
  ["лучший", "Best"],
  ["good", "Best"],
  ["неточность", "Inaccuracy"],
  ["ошибка", "Mistake"],
  ["зевок", "Blunder"],
]);

const SIDE_ALIASES: ReadonlyMap<string, Side> = new Map([
  ["white", "white"],
  ["белые", "white"],
  ["белый", "white"],
  ["1", "white"],
  ["w", "white"],
  ["black", "black"],
  ["чёрные", "black"],
  ["черные", "black"],
  ["чёрный", "black"],
  ["черный", "black"],
  ["2", "black"],
  ["b", "black"],
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function textField(value: unknown, path: string, field: string): string {
  if (typeof value !== "string" || value.trim() === "") {
    throw new Error(`${path}: "${field}" must be a non-empty string`);
  }
  return value.replace(/\s+/g, " ").trim();
}

/** Seconds from a number or `mm:ss` / `hh:mm:ss`, as the old editor accepted them. */
function timeField(value: unknown, path: string, field: string): number {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parts = value.trim().split(":");
    if (parts.length > 0 && parts.length <= 3 && parts.every((part) => part.trim() !== "" && Number.isFinite(Number(part)))) {
      return parts.map(Number).reduce((total, part) => total * 60 + part, 0);
    }
  }
  throw new Error(`${path}: "${field}" must be seconds or mm:ss`);
}

function typeField(value: unknown, path: string): MoveType {
  if (typeof value !== "string") throw new Error(`${path}: "move_type" must be a string`);
  const key = value
    .replace(/^["'«»]+|["'«»]+$/g, "")
    .replace(/\s*\(.*\)$/, "")
    .trim()
    .toLowerCase();
  const exact = TYPE_ALIASES.get(key);
  if (exact !== undefined) return exact;
  for (const [alias, type] of TYPE_ALIASES) {
    if (alias.length > 2 && key.includes(alias)) return type;
  }
  throw new Error(`${path}: unknown move type "${value}"`);
}

function sideField(value: unknown, path: string): Side {
  const key = String(value).trim().toLowerCase();
  const exact = SIDE_ALIASES.get(key);
  if (exact !== undefined) return exact;
  if (key.includes("бел") || key.includes("white")) return "white";
  if (key.includes("чёр") || key.includes("чер") || key.includes("black")) return "black";
  throw new Error(`${path}: unknown speaker "${String(value)}"`);
}

function evalField(value: unknown, path: string): number {
  let score: number;
  if (typeof value === "number") {
    score = value;
  } else if (typeof value === "string") {
    const match = /[-+−]?\d+(?:[.,]\d+)?/.exec(value);
    if (match === null) throw new Error(`${path}: "eval_score" must be a number`);
    score = Number.parseFloat(match[0].replace(",", ".").replace("−", "-"));
  } else {
    throw new Error(`${path}: "eval_score" must be a number`);
  }
  if (!Number.isFinite(score)) throw new Error(`${path}: "eval_score" must be a finite number`);
  return Math.round(Math.min(10, Math.max(-10, score)) * 10) / 10;
}

function toMove(value: unknown, index: number): Move {
  const path = `moves[${index}]`;
  if (!isRecord(value)) throw new Error(`${path}: expected an object`);
  const start = timeField(value["start"], path, "start");
  const end = timeField(value["end"], path, "end");
  if (start < 0 || end < 0) throw new Error(`${path}: times cannot be negative`);
  return {
    start,
    end,
    speaker: sideField(value["speaker"], path),
    moveNumber: textField(value["move_number"], path, "move_number"),
    moveType: typeField(value["move_type"], path),
    title: textField(value["title"], path, "title"),
    explanation: textField(value["explanation"], path, "explanation"),
    evalScore: evalField(value["eval_score"], path),
  };
}

function roundMilliseconds(value: number): number {
  return Math.round(value * 1000) / 1000;
}

/**
 * Sorts, keeps the moves inside the clip, resolves overlaps and numbers the moves.
 * Same normalisation the old `sanitize_moves` applied to every analysis and every edit.
 */
export function sanitizeMoves(moves: Move[], duration: number | null): Move[] {
  const sorted = [...moves].sort((left, right) => left.start - right.start);
  const kept: Move[] = [];
  for (const move of sorted) {
    const next = { ...move };
    if (duration !== null) {
      next.start = Math.min(next.start, duration);
      next.end = Math.min(next.end, duration);
    }
    if (next.end <= next.start) next.end = next.start + 1;
    next.start = roundMilliseconds(next.start);
    next.end = roundMilliseconds(next.end);
    const previous = kept[kept.length - 1];
    if (previous !== undefined && next.start < previous.end) {
      if (next.start <= previous.start) continue;
      previous.end = next.start;
    }
    kept.push(next);
  }
  return kept.map((move, index) => ({
    ...move,
    moveNumber: `${index + 1} ${move.moveNumber.replace(/^\s*\d+[.)]?\s*/, "").trim()}`,
  }));
}

/** Parses a `moves.json` file; `duration` clamps the moves when the clip is known. */
export function parseMoves(text: string, duration: number | null = null): Move[] {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch (error) {
    throw new Error(`not valid JSON: ${error instanceof Error ? error.message : String(error)}`);
  }
  const list = Array.isArray(data) ? data : isRecord(data) && Array.isArray(data["moves"]) ? data["moves"] : null;
  if (list === null) throw new Error('expected {"moves": [...]}');
  if (list.length === 0) throw new Error("the file has no moves");
  return sanitizeMoves(
    list.map((item, index) => toMove(item, index)),
    duration,
  );
}

/** Serialises the moves back into the documented `moves.json` shape. */
export function movesToJson(moves: Move[]): string {
  return `${JSON.stringify(
    {
      moves: moves.map((move) => ({
        start: move.start,
        end: move.end,
        speaker: move.speaker,
        move_number: move.moveNumber,
        move_type: move.moveType,
        title: move.title,
        explanation: move.explanation,
        eval_score: move.evalScore,
      })),
    },
    null,
    2,
  )}\n`;
}
