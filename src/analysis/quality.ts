import type { Names } from "../names.ts";
import type { Move, MoveType, Side } from "./move.ts";
import type { SpeakerContext } from "./prompt.ts";
import type { TranscriptSegment } from "./transcript.ts";

/** What each verdict stands for on the −3…+3 strength scale. */
export const TYPE_STRENGTH: Record<MoveType, number> = {
  Brilliant: 3,
  Great: 2,
  Best: 1,
  Inaccuracy: -1,
  Mistake: -2,
  Blunder: -3,
};

/** Smallest eval swing a verdict must show, from the mover's point of view. */
export const EVAL_SWING: Record<MoveType, number> = {
  Brilliant: 3,
  Great: 2,
  Best: 1.2,
  Inaccuracy: -1.2,
  Mistake: -2.5,
  Blunder: -4,
};

const EVAL_GAIN = 1.5;

export interface MoveCandidate {
  firstSegment: number;
  lastSegment: number;
  speaker: Side;
  moveType: MoveType;
  strength: number | null;
  gist: string;
  title: string;
  explanation: string;
  evalScore: number;
}

/** (min, max) number of moves for a dialogue of this length. */
export function targetMoves(segments: TranscriptSegment[]): { min: number; max: number } {
  const first = segments[0];
  const last = segments[segments.length - 1];
  if (first === undefined || last === undefined) return { min: 1, max: 1 };

  const span = last.end - first.start;
  const max = Math.max(1, Math.min(12, segments.length, Math.round(span / 4)));
  const min = Math.min(max, Math.max(segments.length >= 2 ? 2 : 1, Math.round(span / 12)));
  return { min, max };
}

const PLACEHOLDER = /^(\.{3}|…|имя|суть|заголовок|title|name|gist|<.*>)$/i;
const CLICHES =
  /(it is worth noting|in the framework|casts doubt on|strong statement|game[- ]changer|thereby|является|данный|осуществля|в рамках|следует отметить|демонстриру|тем самым|сильное заявление|очень сильный ход|ставит под сомнение|жиза|рофл)/gi;

/** Checks the JSON schema cannot express; the found problems go back to the model. */
export function reviewCandidates(
  candidates: MoveCandidate[],
  segments: TranscriptSegment[],
  minCount: number,
  speakers: SpeakerContext | null = null,
): string[] {
  const issues: string[] = [];
  const total = segments.length;

  if (candidates.length < minCount) {
    issues.push(`Only ${candidates.length} moves: the dialogue needs at least ${minCount}. Split it further.`);
  }

  const covered = new Set<number>();
  for (const [index, candidate] of candidates.entries()) {
    const from = Math.max(0, Math.min(candidate.firstSegment, candidate.lastSegment));
    const to = Math.min(total - 1, Math.max(candidate.firstSegment, candidate.lastSegment));
    for (let segment = from; segment <= to; segment += 1) covered.add(segment);

    for (const [field, value] of [
      ["title", candidate.title],
      ["explanation", candidate.explanation],
      ["gist", candidate.gist],
    ] as const) {
      if (PLACEHOLDER.test(value.trim())) {
        issues.push(`Move ${index + 1}: the ${field} is a placeholder ("${value}") — write the real text.`);
      }
    }

    const head = candidate.title.split(/\s+[—–-]\s+/)[0]?.trim() ?? "";
    if (/^[—–-]/.test(candidate.title.trim()) || head.split(/\s+/).length < 2) {
      issues.push(
        `Move ${index + 1}: the title "${candidate.title}" is only a verdict — lead with what happened, then the verdict.`,
      );
    }

    if (candidate.strength !== null && Math.abs(candidate.strength - TYPE_STRENGTH[candidate.moveType]) >= 2) {
      issues.push(
        `Move ${index + 1}: ${candidate.moveType} does not match strength ${candidate.strength > 0 ? "+" : ""}${candidate.strength}.`,
      );
    }

    if (speakers !== null) {
      const labels = new Set<"A" | "B">();
      for (let segment = from; segment <= to; segment += 1) {
        const hint = speakers.hints[segment];
        if (hint !== undefined && hint.label !== null && hint.confidence >= 0.8) labels.add(hint.label);
      }
      const label = [...labels][0];
      if (labels.size > 1) {
        issues.push(`Move ${index + 1} mixes voices A and B: one move must be one speaker.`);
      } else if (label !== undefined && speakers.sides[label] !== candidate.speaker) {
        issues.push(
          `Move ${index + 1}: voice ${label} is ${speakers.sides[label]}, but the move is given to ${candidate.speaker}.`,
        );
      }
    }
  }

  const brilliant = candidates.filter((candidate) => candidate.moveType === "Brilliant").length;
  if (brilliant > Math.max(1, Math.floor(candidates.length / 5))) {
    issues.push(`${brilliant} moves are Brilliant — keep it for one or two key moments.`);
  }

  if (candidates.length >= 5) {
    const counts = new Map<MoveType, number>();
    for (const candidate of candidates) counts.set(candidate.moveType, (counts.get(candidate.moveType) ?? 0) + 1);
    const top = [...counts.entries()].sort((left, right) => right[1] - left[1])[0];
    if (top !== undefined && top[1] / candidates.length > 0.7) {
      issues.push(`Almost every move is ${top[0]}: judge each move by its real effect.`);
    }
  }

  const cliches = new Set<string>();
  for (const candidate of candidates) {
    for (const match of `${candidate.title} ${candidate.explanation}`.matchAll(CLICHES)) {
      cliches.add(match[0].toLowerCase());
    }
  }
  if (cliches.size > 0) {
    issues.push(`Rewrite the texts that use these clichés: ${[...cliches].slice(0, 6).map((word) => `"${word}"`).join(", ")}.`);
  }

  if (total > 0 && covered.size / total < 0.8) {
    const missing = [...Array(total).keys()].filter((index) => !covered.has(index));
    issues.push(`The moves skip segments ${missing.slice(0, 20).join(", ")} — the whole dialogue must be covered.`);
  }
  return issues;
}

/** Turns a checked candidate into a move on the clip's timeline. */
export function moveFromCandidate(
  candidate: MoveCandidate,
  segments: TranscriptSegment[],
  names: Names,
): Move {
  const first = segments[candidate.firstSegment];
  const last = segments[candidate.lastSegment];
  if (first === undefined || last === undefined) throw new Error("a move points outside the dialogue");
  return {
    start: first.start,
    end: last.end,
    speaker: candidate.speaker,
    moveNumber: `${names[candidate.speaker]} · ${candidate.gist}`,
    moveType: candidate.moveType,
    title: candidate.title,
    explanation: candidate.explanation,
    evalScore: candidate.evalScore,
  };
}

/**
 * Turns the model's numbers into a lively but consistent eval curve: the verdict decides the
 * direction, the model's own swing is amplified and never smaller than the type's minimum.
 */
export function shapeEvals(moves: Move[]): Move[] {
  let previousRaw = 0;
  let previous = 0;
  return moves.map((move) => {
    const raw = move.evalScore;
    const sign = move.speaker === "white" ? 1 : -1;
    const step = EVAL_SWING[move.moveType];

    let swing = (raw - previousRaw) * sign;
    if (swing * step < 0) {
      // Models often score from the mover's perspective; try the flipped sign first.
      const flipped = (-raw - previousRaw) * sign;
      swing = flipped * step > 0 && Math.abs(flipped) <= 2 * Math.abs(step) + 2 ? flipped : 0;
    }
    swing = Math.max(Math.abs(swing) * EVAL_GAIN, Math.abs(step)) * Math.sign(step);
    const value = Math.max(-10, Math.min(10, Math.round((previous + sign * swing) * 10) / 10));
    previousRaw = raw;
    previous = value;
    return { ...move, evalScore: value };
  });
}
