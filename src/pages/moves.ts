import { MOVE_GLYPHS, movesToJson, parseMoves, SIDE_LABELS } from "../analysis/move.ts";
import type { Move } from "../analysis/move.ts";
import { getClip, getMoves, setMoves } from "../session.ts";
import { el } from "../ui/dom.ts";
import { formatEval, formatTimestamp, messageOf } from "../ui/text.ts";
import type { Page } from "./page.ts";

export function renderMovesPage(): Page {
  const clip = getClip();
  let exportUrl: string | null = null;

  const fileInput = el("input", {
    class: "visually-hidden",
    attrs: { type: "file", accept: ".json,application/json" },
  });
  const importButton = el("button", { class: "button", text: "Import JSON", attrs: { type: "button" } });
  const exportLink = el("a", { class: "button", text: "Export JSON", attrs: { download: "moves.json" } });
  const count = el("span", { class: "meta" });
  const status = el("p", { class: "status-line" });
  const list = el("ol", { class: "move-list" });
  const empty = el("p", { class: "placeholder" });

  const element = el("section", {
    class: "page",
    children: [
      el("h1", { class: "page-title", text: "Moves" }),
      el("p", {
        class: "page-hint",
        text: "The move cards of the review: import a moves.json file or export the list.",
      }),
      el("div", { class: "toolbar", children: [importButton, exportLink, count] }),
      fileInput,
      status,
      empty,
      list,
    ],
  });

  function setStatus(text: string): void {
    status.textContent = text;
  }

  function row(move: Move, index: number): HTMLElement {
    return el("li", {
      class: "move-row",
      children: [
        el("span", { class: "move-index", text: String(index + 1) }),
        el("span", {
          class: "move-time",
          text: `${formatTimestamp(move.start)}–${formatTimestamp(move.end)}`,
        }),
        el("span", { class: "move-badge", text: MOVE_GLYPHS[move.moveType] }),
        el("span", { class: "move-side", text: SIDE_LABELS[move.speaker] }),
        el("span", { class: "move-title", text: move.title }),
        el("span", { class: "move-eval", text: formatEval(move.evalScore) }),
      ],
    });
  }

  function refreshExport(): void {
    if (exportUrl !== null) {
      URL.revokeObjectURL(exportUrl);
      exportUrl = null;
    }
    const moves = getMoves();
    if (moves.length === 0) {
      exportLink.removeAttribute("href");
      return;
    }
    exportUrl = URL.createObjectURL(new Blob([movesToJson(moves)], { type: "application/json" }));
    exportLink.href = exportUrl;
  }

  function renderList(): void {
    const moves = getMoves();
    list.replaceChildren(...moves.map((move, index) => row(move, index)));
    list.hidden = moves.length === 0;
    empty.hidden = moves.length > 0;
    exportLink.hidden = moves.length === 0;
    count.textContent = moves.length === 1 ? "1 move" : `${moves.length} moves`;
    refreshExport();
  }

  async function importFile(file: File): Promise<void> {
    if (clip === null) {
      setStatus("Open a clip on the Create video page first.");
      return;
    }
    try {
      const parsed = parseMoves(await file.text(), clip.media.info.duration);
      setMoves(parsed);
      renderList();
      const amount = parsed.length === 1 ? "1 move" : `${parsed.length} moves`;
      setStatus(`Imported ${amount} from ${file.name}`);
    } catch (error) {
      setStatus(messageOf(error));
    }
  }

  importButton.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    const file = fileInput.files?.[0];
    if (file !== undefined) void importFile(file);
  });

  if (clip === null) {
    importButton.disabled = true;
    empty.textContent = "Open a clip on the Create video page first.";
  } else if (getMoves().length === 0) {
    empty.textContent = "No moves yet. Import a moves.json file.";
  }
  renderList();

  return {
    element,
    dispose() {
      if (exportUrl !== null) URL.revokeObjectURL(exportUrl);
      exportUrl = null;
    },
  };
}
