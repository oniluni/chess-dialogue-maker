import type { Names } from "../names.ts";
import { sanitizeMoves } from "./move.ts";
import type { Move, MoveType, Side } from "./move.ts";
import { EVAL_SWING } from "./quality.ts";
import type { TranscriptSegment } from "./transcript.ts";

/** A dialogue line: what was said, when, and by whom (if known). */
export interface DialogueLine {
  start: number;
  end: number;
  text: string;
  speaker: Side | null;
  /** −3…+3, how strong the line was for its speaker; drives the automatic move type. */
  strength: number | null;
  /** A short motif of the line, used as the gist when the moment has none. */
  motif: string;
}

/** A moment groups consecutive lines into one card. */
export interface DialogueMoment {
  from: number;
  to: number;
  title: string;
  gist: string;
  explanation: string;
  /** `null` means "Best". */
  moveType: MoveType | null;
  /** `null` means "the side that speaks most of the lines". */
  speaker: Side | null;
  /** `null` means "continue the eval curve automatically". */
  evalScore: number | null;
}

export interface Dialogue {
  lines: DialogueLine[];
  moments: DialogueMoment[];
}

export interface DialogueBuild {
  moves: Move[];
  errors: string[];
  warnings: string[];
}

function gistOf(moveNumber: string): string {
  const text = moveNumber.replace(/^\s*\d+[.)]?\s*/, "").trim();
  const marker = text.indexOf(" · ");
  return marker < 0 ? "" : text.slice(marker + 3).trim();
}

export function fromTranscript(segments: TranscriptSegment[]): Dialogue {
  return {
    lines: segments.map((segment) => ({
      start: segment.start,
      end: segment.end,
      text: segment.text,
      speaker: null,
      strength: null,
      motif: "",
    })),
    moments: [],
  };
}

export function fromMoves(moves: Move[]): Dialogue {
  return {
    lines: moves.map((move) => ({ start: move.start, end: move.end, text: "", speaker: move.speaker, strength: null, motif: "" })),
    moments: moves.map((move, index) => ({
      from: index,
      to: index,
      title: move.title,
      gist: gistOf(move.moveNumber),
      explanation: move.explanation,
      moveType: move.moveType,
      speaker: move.speaker,
      evalScore: move.evalScore,
    })),
  };
}

/** Keeps the existing analysis as moments when the lines come from a transcript. */
export function attachMoves(dialogue: Dialogue, moves: Move[]): Dialogue {
  const moments = moves.flatMap((move): DialogueMoment[] => {
    const from = dialogue.lines.findIndex((line) => line.start >= move.start - 0.05);
    if (from < 0) return [];

    let to = -1;
    for (let index = dialogue.lines.length - 1; index >= from; index -= 1) {
      const line = dialogue.lines[index];
      if (line !== undefined && line.end <= move.end + 0.05) {
        to = index;
        break;
      }
    }
    if (to < from) return [];

    return [
      {
        from,
        to,
        title: move.title,
        gist: gistOf(move.moveNumber),
        explanation: move.explanation,
        moveType: move.moveType,
        speaker: move.speaker,
        evalScore: move.evalScore,
      },
    ];
  });
  return { ...dialogue, moments };
}

function majority(sides: Side[]): Side | null {
  if (sides.length === 0) return null;
  const white = sides.filter((side) => side === "white").length;
  const black = sides.length - white;
  if (white === black) return null;
  return white > black ? "white" : "black";
}

/** −3…+3 strength to the verdict, as the old manual editor did. */
export function typeForStrength(strength: number | null): MoveType {
  if (strength === null) return "Best";
  if (strength >= 2.5) return "Brilliant";
  if (strength >= 1.5) return "Great";
  if (strength >= 0.5) return "Best";
  if (strength <= -2.5) return "Blunder";
  if (strength <= -1.5) return "Mistake";
  if (strength <= -0.5) return "Inaccuracy";
  return "Best";
}

/** The card subtitle: the moment's gist, else the first motif among its lines. */
function moveNumberOf(name: string, gist: string, lines: DialogueLine[]): string {
  const clean = gist.trim() !== "" ? gist.trim() : (lines.find((line) => line.motif.trim() !== "")?.motif.trim() ?? "");
  return clean === "" ? name : `${name} · ${clean}`;
}

/** Turns the lines and moments into moves; no model is involved. */
export function buildDialogue(dialogue: Dialogue, names: Names, duration: number | null): DialogueBuild {
  const errors: string[] = [];
  const warnings: string[] = [];
  if (dialogue.moments.length === 0) errors.push("Add at least one moment.");

  const moves: Move[] = [];
  let previousEval = 0;
  const ordered = [...dialogue.moments].sort((left, right) => left.from - right.from);

  for (const [index, moment] of ordered.entries()) {
    const number = index + 1;
    const from = Math.max(0, Math.min(moment.from, moment.to));
    const to = Math.min(dialogue.lines.length - 1, Math.max(moment.from, moment.to));
    const lines = dialogue.lines.slice(from, to + 1);
    const first = lines[0];
    const last = lines[lines.length - 1];
    if (first === undefined || last === undefined) {
      errors.push(`Moment ${number} covers no lines.`);
      continue;
    }

    const speaker = moment.speaker ?? majority(lines.map((line) => line.speaker).filter((side): side is Side => side !== null));
    if (speaker === null) {
      errors.push(`Moment ${number}: nobody speaks in its lines — pick a side.`);
      continue;
    }

    const title = moment.title.replace(/\s+/g, " ").trim();
    if (title === "") errors.push(`Moment ${number}: the card title is empty.`);
    const explanation = moment.explanation.replace(/\s+/g, " ").trim();
    if (explanation === "") errors.push(`Moment ${number}: the explanation is empty.`);

    const strengths = lines.map((line) => line.strength).filter((value): value is number => value !== null);
    const average = strengths.length === 0 ? null : strengths.reduce((sum, value) => sum + value, 0) / strengths.length;
    const moveType: MoveType = moment.moveType ?? typeForStrength(average);
    const sign = speaker === "white" ? 1 : -1;
    const evalScore =
      moment.evalScore ?? Math.max(-10, Math.min(10, Math.round((previousEval + sign * EVAL_SWING[moveType]) * 10) / 10));
    previousEval = evalScore;

    moves.push({
      start: first.start,
      end: last.end,
      speaker,
      moveNumber: moveNumberOf(names[speaker], moment.gist, lines),
      moveType,
      title,
      explanation,
      evalScore,
    });
  }

  const sorted = [...moves].sort((left, right) => left.start - right.start);
  for (let index = 1; index < sorted.length; index += 1) {
    const previous = sorted[index - 1];
    const current = sorted[index];
    if (previous !== undefined && current !== undefined && current.start < previous.end - 0.001) {
      errors.push(`Moves ${index} and ${index + 1} overlap in time.`);
    }
  }

  if (errors.length > 0) return { moves: [], errors, warnings };
  if (dialogue.lines.some((line) => line.text.trim() === "")) {
    warnings.push("Some lines have no text: they still give the timing, but nobody will see the words.");
  }
  return { moves: sanitizeMoves(moves, duration), errors, warnings };
}

