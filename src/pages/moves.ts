import { removeMove, swapMoves, upsertMoves } from "../analysis/move-draft.ts";
import { formatEval, MOVE_GLYPHS, movesToJson, parseMoves, SIDE_LABELS } from "../analysis/move.ts";
import type { Move } from "../analysis/move.ts";
import {
  getClip,
  getMoves,
  getMovesBackup,
  getNames,
  restoreMovesBackup,
  setMoves,
} from "../session.ts";
import { el } from "../ui/dom.ts";
import { formatTimestamp, messageOf } from "../ui/text.ts";
import { createMoveEditor } from "./move-editor.ts";
import type { MoveEditor } from "./move-editor.ts";
import type { Page } from "./page.ts";

export function renderMovesPage(): Page {
  const clip = getClip();
  let exportUrl: string | null = null;
  let selected: number | null = null;

  const fileInput = el("input", {
    class: "visually-hidden",
    attrs: { type: "file", accept: ".json,application/json" },
  });
  const importButton = el("button", { class: "button", text: "Import JSON", attrs: { type: "button" } });
  const exportLink = el("a", { class: "button", text: "Export JSON", attrs: { download: "moves.json" } });
  const restoreButton = el("button", { class: "button", text: "Restore analysis", attrs: { type: "button" } });
  const count = el("span", { class: "meta" });
  const status = el("p", { class: "status-line" });
  const list = el("ol", { class: "move-list" });
  const empty = el("p", { class: "placeholder" });

  const video =
    clip === null
      ? null
      : el("video", {
          class: "move-video",
          attrs: { src: clip.objectUrl, controls: "", playsinline: "" },
        });

  const editor: MoveEditor = createMoveEditor({
    moves: getMoves,
    names: getNames,
    duration: () => (clip === null ? null : clip.media.info.duration),
    playhead: () => (video === null ? 0 : video.currentTime),
    onSaved: (move, index) => {
      const result = upsertMoves(getMoves(), index, move, clip === null ? null : clip.media.info.duration);
      setMoves(result.moves);
      renderList();
      select(result.index);
      setStatus(index === null ? "Move added." : "Move saved.");
    },
    onDeleted: (index) => {
      setMoves(removeMove(getMoves(), index, clip === null ? null : clip.media.info.duration));
      renderList();
      select(null);
      setStatus("Move deleted.");
    },
    onSwapped: (index, step) => {
      const result = swapMoves(getMoves(), index, step, clip === null ? null : clip.media.info.duration);
      setMoves(result.moves);
      renderList();
      select(result.index);
      setStatus("Move moved on the timeline.");
    },
  });

  const element = el("section", {
    class: "page",
    children: [
      el("h1", { class: "page-title", text: "Moves" }),
      el("p", {
        class: "page-hint",
        text: "Edit the cards of the review: pick a move, change its text and times, or add one at the playhead.",
      }),
      el("div", { class: "toolbar", children: [importButton, exportLink, restoreButton, count] }),
      fileInput,
      status,
      video,
      empty,
      el("div", { class: "moves-layout", children: [list, editor.element] }),
    ],
  });

  function setStatus(text: string): void {
    status.textContent = text;
  }

  function row(move: Move, index: number): HTMLElement {
    const button = el("button", {
      class: `move-row${index === selected ? " is-selected" : ""}`,
      attrs: { type: "button" },
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
    button.addEventListener("click", () => {
      select(index);
    });
    return el("li", { children: [button] });
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
    empty.hidden = moves.length > 0 || clip === null;
    if (clip !== null && moves.length === 0) {
      empty.textContent = "No moves yet: add one at the playhead or import moves.json.";
    }
    exportLink.hidden = moves.length === 0;
    count.textContent = moves.length === 1 ? "1 move" : `${moves.length} moves`;
    restoreButton.disabled = getMovesBackup() === null;
    refreshExport();
  }

  function select(index: number | null): void {
    const move = index === null ? undefined : getMoves()[index];
    if (index === null || move === undefined) {
      selected = null;
      editor.clear();
    } else {
      selected = index;
      editor.show(move, index);
      if (video !== null) video.currentTime = move.start;
    }
    [...list.querySelectorAll(".move-row")].forEach((item, rowIndex) => {
      item.classList.toggle("is-selected", rowIndex === selected);
    });
  }

  async function importFile(file: File): Promise<void> {
    if (clip === null) {
      setStatus("Open a clip on the Create video page first.");
      return;
    }
    try {
      const parsed = parseMoves(await file.text(), clip.media.info.duration);
      setMoves(parsed, { keepBackup: true });
      renderList();
      select(null);
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
  restoreButton.addEventListener("click", () => {
    if (!restoreMovesBackup()) {
      setStatus("There is no saved analysis to restore.");
      return;
    }
    renderList();
    select(null);
    setStatus("The original analysis is back.");
  });

  if (clip === null) {
    importButton.disabled = true;
    empty.textContent = "Open a clip on the Create video page first.";
    editor.element.hidden = true;
  }
  renderList();

  return {
    element,
    dispose() {
      editor.dispose();
      if (exportUrl !== null) URL.revokeObjectURL(exportUrl);
      exportUrl = null;
      if (video !== null) {
        video.pause();
        video.removeAttribute("src");
        video.load();
      }
    },
  };
}
