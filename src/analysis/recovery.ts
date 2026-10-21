import type { Names } from "../names.ts";
import type { AnalysisAttempt } from "./analyze.ts";
import { sanitizeMoves } from "./move.ts";
import type { Move } from "./move.ts";
import { moveFromCandidate, shapeEvals } from "./quality.ts";
import type { MoveCandidate } from "./quality.ts";
import type { TranscriptSegment } from "./transcript.ts";

export interface RecoveryAlternative {
  attempt: number;
  /** Index inside that attempt's list. */
  position: number;
  candidate: MoveCandidate;
}

export interface RecoveryStep {
  index: number;
  firstSegment: number;
  lastSegment: number;
  alternatives: RecoveryAlternative[];
}

export interface RecoveryPlan {
  baseAttempt: number;
  steps: RecoveryStep[];
}

export interface RecoveryChoice {
  attempt: number;
  position: number;
}

function overlaps(left: MoveCandidate, right: MoveCandidate): boolean {
  return left.firstSegment <= right.lastSegment && right.firstSegment <= left.lastSegment;
}

/**
 * The base answer (fewest problems) gives the step boundaries; every other answer offers
 * its overlapping move as an alternative for each step.
 */
export function planRecovery(attempts: AnalysisAttempt[]): RecoveryPlan | null {
  const usable = attempts.filter(
    (attempt): attempt is AnalysisAttempt & { candidates: MoveCandidate[] } => attempt.candidates !== null,
  );
  const base = [...usable].sort((left, right) => left.issues.length - right.issues.length)[0];
  if (base === undefined) return null;

  const steps = base.candidates.map((candidate, index) => {
    const alternatives: RecoveryAlternative[] = [];
    for (const attempt of usable) {
      attempt.candidates.forEach((other, position) => {
        if (overlaps(other, candidate)) {
          alternatives.push({ attempt: attempt.index, position, candidate: other });
        }
      });
    }
    return {
      index,
      firstSegment: candidate.firstSegment,
      lastSegment: candidate.lastSegment,
      alternatives,
    };
  });
  return { baseAttempt: base.index, steps };
}

/** Builds the moves from the chosen variant of every step, keeping the base time ranges. */
export function resolveRecovery(
  plan: RecoveryPlan,
  choices: Array<RecoveryChoice | null>,
  segments: TranscriptSegment[],
  names: Names,
  duration: number | null,
  edits: Array<{ title: string; explanation: string } | null> = [],
): Move[] {
  const moves = plan.steps.flatMap((step, index) => {
    const choice = choices[index] ?? { attempt: plan.baseAttempt, position: step.index };
    const picked =
      step.alternatives.find(
        (alternative) => alternative.attempt === choice.attempt && alternative.position === choice.position,
      ) ?? step.alternatives[0];
    if (picked === undefined) return [];

    const edit = edits[index];
    const built: MoveCandidate = {
      ...picked.candidate,
      firstSegment: step.firstSegment,
      lastSegment: step.lastSegment,
      title: edit !== null && edit !== undefined && edit.title.trim() !== "" ? edit.title.trim() : picked.candidate.title,
      explanation:
        edit !== null && edit !== undefined && edit.explanation.trim() !== ""
          ? edit.explanation.trim()
          : picked.candidate.explanation,
    };
    return [moveFromCandidate(built, segments, names)];
  });
  return shapeEvals(sanitizeMoves(moves, duration));
}
