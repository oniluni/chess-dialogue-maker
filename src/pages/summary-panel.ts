import type { Move } from "../analysis/move.ts";
import { formatEval } from "../analysis/move.ts";
import { getLlmChoice, getMoves, getTranscript } from "../session.ts";
import { chat } from "../llm/api.ts";
import { el } from "../ui/dom.ts";
import { messageOf } from "../ui/text.ts";

/** A short written summary of the review, asked from the same model as the analysis. */
export function createSummaryPanel(): HTMLElement {
  const button = el("button", { class: "button", text: "Summarise", attrs: { type: "button" } });
  const status = el("p", { class: "status-line" });
  const text = el("p", { class: "summary-text" });

  const element = el("section", {
    class: "analysis",
    children: [
      el("h2", { class: "section-title", text: "Summary" }),
      el("div", { class: "toolbar", children: [button] }),
      status,
      text,
    ],
  });

  function lines(segments: ReturnType<typeof getTranscript>): string {
    return segments.map((segment) => `${segment.id}: ${segment.text}`).join("\n");
  }

  function moves(): string {
    return getMoves()
      .map((move: Move) => `${move.moveNumber} — ${move.title} (${formatEval(move.evalScore)})`)
      .join("\n");
  }

  button.addEventListener("click", () => {
    void (async (): Promise<void> => {
      const segments = getTranscript();
      const choice = getLlmChoice();
      if (segments.length === 0) {
        status.textContent = "Transcribe the clip first.";
        return;
      }
      if (choice.provider === "") {
        status.textContent = "Pick a provider in the analysis block first.";
        return;
      }

      button.disabled = true;
      status.textContent = "Asking the model…";
      text.textContent = "";
      try {
        const answer = await chat({
          provider: choice.provider,
          model: choice.model,
          temperature: 0.5,
          messages: [
            {
              role: "system",
              content:
                "You write a short summary of a dialogue review for a video description. " +
                "Two or three sentences, no lists, no markdown, no clichés. " +
                "Write in the language of the dialogue and never invent facts.",
            },
            {
              role: "user",
              content:
                `Dialogue:\n${lines(segments)}\n\n` +
                (getMoves().length > 0 ? `Moves:\n${moves()}\n\n` : "") +
                "Summarise what the exchange is about and who is winning it.",
            },
          ],
        });
        text.textContent = answer.trim();
        status.textContent = "";
      } catch (error) {
        status.textContent = messageOf(error);
      } finally {
        button.disabled = false;
      }
    })();
  });

  return element;
}
