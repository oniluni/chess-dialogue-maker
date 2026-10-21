import type { Names } from "../names.ts";
import { formatTimestamp, parseTimestamp } from "../ui/text.ts";
import { MOVE_TYPES, sanitizeMoves } from "./move.ts";
import type { Move, MoveType, Side } from "./move.ts";

/** What the moves editor shows: timecodes stay text until they are checked. */
export interface MoveDraft {
  start: string;
  end: string;
  speaker: Side;
  moveType: MoveType;
  evalScore: number;
  gist: string;
  title: string;
  explanation: string;
}

export interface DraftCheck {
  move: Move | null;
  errors: string[];
  warnings: string[];
}

const MIN_LENGTH = 0.3;
/** Timecodes are shown with tenths, so touching moves are allowed. */
const TOLERANCE = 0.05;
const TITLE_SOFT_MAX = 60;
const EXPLANATION_SOFT_MAX = 190;

function gistOf(moveNumber: string): string {
  const text = moveNumber.replace(/^\s*\d+[.)]?\s*/, "").trim();
  const marker = text.indexOf(" · ");
  return marker < 0 ? "" : text.slice(marker + 3).trim();
}

export function moveNumber(names: Names, speaker: Side, gist: string): string {
  const name = names[speaker].trim() || (speaker === "white" ? "White" : "Black");
  const clean = gist.replace(/\s+/g, " ").trim();
  return clean === "" ? name : `${name} · ${clean}`;
}

export function draftOf(move: Move): MoveDraft {
  return {
    start: formatTimestamp(move.start),
    end: formatTimestamp(move.end),
    speaker: move.speaker,
    moveType: move.moveType,
    evalScore: move.evalScore,
    gist: gistOf(move.moveNumber),
    title: move.title,
    explanation: move.explanation,
  };
}

function overlapping(moves: Move[], start: number, end: number, skip: number | null): number[] {
  return moves.flatMap((move, index) =>
    index !== skip && start < move.end - TOLERANCE && end > move.start + TOLERANCE ? [index] : [],
  );
}

/** Checks the draft and turns it into a move; `index` is the edited move (`null` = new). */
export function checkDraft(
  draft: MoveDraft,
  moves: Move[],
  index: number | null,
  names: Names,
  duration: number | null,
): DraftCheck {
  const errors: string[] = [];
  const warnings: string[] = [];

  const start = parseTimestamp(draft.start);
  const end = parseTimestamp(draft.end);
  if (start === null || end === null) {
    errors.push("Set the start and the end of the move.");
  } else if (Number.isNaN(start)) {
    errors.push(`"${draft.start}" is not a time; write 1:02 or 62.`);
  } else if (Number.isNaN(end)) {
    errors.push(`"${draft.end}" is not a time; write 1:02 or 62.`);
  } else {
    let endSeconds = end;
    if (endSeconds - start < MIN_LENGTH) {
      errors.push(`A move lasts at least ${MIN_LENGTH} s: the end goes after the start.`);
    }
    if (duration !== null && start >= duration) {
      errors.push(`The start (${formatTimestamp(start)}) is past the end of the clip (${formatTimestamp(duration)}).`);
    } else if (duration !== null && endSeconds > duration + TOLERANCE) {
      warnings.push(`The end is cut to the end of the clip (${formatTimestamp(duration)}).`);
      endSeconds = duration;
    }

    const clash = overlapping(moves, start, endSeconds, index)[0];
    const other = clash === undefined ? undefined : moves[clash];
    if (clash !== undefined && other !== undefined) {
      errors.push(
        `Overlaps move ${clash + 1} (${formatTimestamp(other.start)}–${formatTimestamp(other.end)}): moves must not overlap.`,
      );
    }
  }

  const title = draft.title.replace(/\s+/g, " ").trim();
  if (title === "") {
    errors.push("The card title is empty.");
  } else if (title.length > TITLE_SOFT_MAX) {
    warnings.push(`The title is longer than ${TITLE_SOFT_MAX} characters: the card font shrinks.`);
  }

  const explanation = draft.explanation.replace(/\s+/g, " ").trim();
  if (explanation === "") {
    errors.push("The explanation is empty.");
  } else if (explanation.length > EXPLANATION_SOFT_MAX) {
    warnings.push(`The explanation is longer than ${EXPLANATION_SOFT_MAX} characters: the pause grows and the text shrinks.`);
  }

  if (!MOVE_TYPES.includes(draft.moveType)) errors.push("Pick a move type.");
  if (draft.speaker !== "white" && draft.speaker !== "black") errors.push("Pick who makes the move.");

  if (errors.length > 0 || start === null || end === null || Number.isNaN(start) || Number.isNaN(end)) {
    return { move: null, errors, warnings };
  }

  const score = Number.isFinite(draft.evalScore) ? draft.evalScore : 0;
  return {
    move: {
      start,
      end: Math.max(start + MIN_LENGTH, Math.min(end, duration ?? end)),
      speaker: draft.speaker,
      moveNumber: moveNumber(names, draft.speaker, draft.gist),
      moveType: draft.moveType,
      title,
      explanation,
      evalScore: Math.round(Math.max(-10, Math.min(10, score)) * 10) / 10,
    },
    errors,
    warnings,
  };
}

function nearestIndex(moves: Move[], move: Move): number {
  let best = 0;
  let distance = Number.POSITIVE_INFINITY;
  moves.forEach((item, index) => {
    const next = Math.abs(item.start - move.start);
    if (next < distance) {
      distance = next;
      best = index;
    }
  });
  return best;
}

export function upsertMoves(
  moves: Move[],
  index: number | null,
  move: Move,
  duration: number | null,
): { moves: Move[]; index: number } {
  const items = [...moves];
  if (index === null || index < 0 || index >= items.length) items.push(move);
  else items[index] = move;
  const ordered = sanitizeMoves(items, duration);
  return { moves: ordered, index: nearestIndex(ordered, move) };
}

export function removeMove(moves: Move[], index: number, duration: number | null): Move[] {
  return sanitizeMoves(moves.filter((_, item) => item !== index), duration);
}

/** Swaps a move with its neighbour: they exchange time slots on the timeline. */
export function swapMoves(
  moves: Move[],
  index: number,
  step: number,
  duration: number | null,
): { moves: Move[]; index: number } {
  const other = index + step;
  const first = moves[index];
  const second = moves[other];
  if (first === undefined || second === undefined) return { moves: [...moves], index };

  const items = [...moves];
  items[index] = { ...second, start: first.start, end: first.end };
  items[other] = { ...first, start: second.start, end: second.end };
  const ordered = sanitizeMoves(items, duration);
  return { moves: ordered, index: other };
}

/** A sensible new move at the playhead: up to 3 s and never past the next move. */
export function defaultDraft(moves: Move[], at: number, duration: number | null): MoveDraft {
  const limit = duration ?? at + 3;
  let start = Math.max(0, Math.min(at, limit - MIN_LENGTH));
  for (const move of moves) {
    if (move.start <= start && start < move.end) start = move.end;
  }

  const next = moves
    .filter((move) => move.start > start)
    .map((move) => move.start)
    .sort((left, right) => left - right)[0];
  const end = Math.min(start + 3, next ?? limit, limit);
  const roundedStart = Math.ceil(start * 10 - 1e-6) / 10;
  const roundedEnd = Math.max(Math.floor(end * 10) / 10, roundedStart + MIN_LENGTH);

  const previous = moves
    .filter((move) => move.end <= roundedStart + 1e-3)
    .sort((left, right) => left.end - right.end)
    .at(-1);
  const speaker: Side = previous === undefined ? "white" : previous.speaker === "white" ? "black" : "white";

  return {
    start: formatTimestamp(roundedStart),
    end: formatTimestamp(roundedEnd),
    speaker,
    moveType: "Best",
    evalScore: previous?.evalScore ?? 0,
    gist: "",
    title: "",
    explanation: "",
  };
}
