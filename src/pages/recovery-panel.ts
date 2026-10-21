import type { AnalysisAttempt } from "../analysis/analyze.ts";
import type { Move } from "../analysis/move.ts";
import { planRecovery, resolveRecovery } from "../analysis/recovery.ts";
import type { RecoveryChoice } from "../analysis/recovery.ts";
import type { TranscriptSegment } from "../analysis/transcript.ts";
import { getNames } from "../session.ts";
import { el } from "../ui/dom.ts";
import { formatTimestamp } from "../ui/text.ts";

function previewOf(candidate: { title: string; explanation: string } | undefined): string {
  return candidate === undefined ? "" : `${candidate.title} — ${candidate.explanation}`;
}

/**
 * The recovery window: the base answer sets the step boundaries, every answer offers its
 * overlapping move as a variant, and the person picks one per step.
 */
export function createRecoveryPanel(
  attempts: AnalysisAttempt[],
  segments: TranscriptSegment[],
  duration: number | null,
  onResolved: (moves: Move[]) => void,
): HTMLElement | null {
  const plan = planRecovery(attempts);
  if (plan === null) return null;

  const choices: Array<RecoveryChoice | null> = plan.steps.map(() => null);

  const rows = plan.steps.map((step, index) => {
    const first = segments[step.firstSegment];
    const last = segments[step.lastSegment];
    const time =
      first === undefined || last === undefined
        ? ""
        : `${formatTimestamp(first.start)}–${formatTimestamp(last.end)}`;

    const select = el("select", {
      class: "select",
      attrs: { "aria-label": `Variant for step ${index + 1}` },
    });
    for (const alternative of step.alternatives) {
      select.append(
        el("option", {
          text: `Attempt ${alternative.attempt} · ${alternative.candidate.moveType} · ${alternative.candidate.title}`,
          attrs: { value: `${alternative.attempt}:${alternative.position}` },
        }),
      );
    }
    select.value = `${plan.baseAttempt}:${step.index}`;
    choices[index] = { attempt: plan.baseAttempt, position: step.index };

    const preview = el("p", { class: "recovery-preview", text: previewOf(step.alternatives[0]?.candidate) });
    select.addEventListener("change", () => {
      const [attempt, position] = select.value.split(":").map(Number);
      const alternative = step.alternatives.find(
        (item) => item.attempt === attempt && item.position === position,
      );
      choices[index] = alternative === undefined ? null : { attempt: alternative.attempt, position: alternative.position };
      preview.textContent = previewOf(alternative?.candidate);
    });

    return el("div", {
      class: "recovery-step",
      children: [
        el("div", {
          class: "recovery-head",
          children: [
            el("span", { class: "recovery-number", text: String(index + 1) }),
            el("span", { class: "move-time", text: time }),
            select,
          ],
        }),
        preview,
      ],
    });
  });

  const issues = el("ul", { class: "editor-notes" });
  for (const attempt of attempts) {
    const issue = attempt.issues[0];
    if (issue !== undefined) {
      issues.append(el("li", { class: "note-warning", text: `Attempt ${attempt.index}: ${issue}` }));
    }
  }

  const applyButton = el("button", {
    class: "button button-primary",
    text: "Use these variants",
    attrs: { type: "button" },
  });
  applyButton.addEventListener("click", () => {
    const moves = resolveRecovery(plan, choices, segments, getNames(), duration);
    if (moves.length === 0) return;
    applyButton.disabled = true;
    onResolved(moves);
  });

  return el("div", {
    class: "recovery",
    children: [
      el("h3", { class: "section-title", text: "Recovery: pick a variant for each step" }),
      el("p", {
        class: "page-hint",
        text: "None of the answers passed the checks. The step times come from the answer with the fewest problems; the text is yours to choose. Edit it later on the Moves page.",
      }),
      ...rows,
      issues,
      el("div", { class: "toolbar", children: [applyButton] }),
    ],
  });
}
