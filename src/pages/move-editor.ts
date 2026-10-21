import type { MoveDraft } from "../analysis/move-draft.ts";
import { checkDraft, defaultDraft, draftOf } from "../analysis/move-draft.ts";
import { MOVE_GLYPHS, MOVE_TYPES, SIDE_LABELS } from "../analysis/move.ts";
import type { Move, MoveType } from "../analysis/move.ts";
import type { Names } from "../names.ts";
import { el } from "../ui/dom.ts";
import { formatTimestamp } from "../ui/text.ts";

export interface MoveEditorOptions {
  moves: () => Move[];
  names: () => Names;
  duration: () => number | null;
  playhead: () => number;
  onSaved: (move: Move, index: number | null) => void;
  onDeleted: (index: number) => void;
  onSwapped: (index: number, step: number) => void;
}

export interface MoveEditor {
  element: HTMLElement;
  show(move: Move, index: number): void;
  clear(): void;
  dispose(): void;
}

const DELETE_CONFIRM_MS = 5000;

/** The edit form of one move: timecodes, side, type, eval and the card text. */
export function createMoveEditor(options: MoveEditorOptions): MoveEditor {
  let index: number | null = null;
  let deleteArmed = false;
  let deleteTimer: number | null = null;

  const startInput = el("input", { class: "text-input time-input", attrs: { type: "text" } });
  const endInput = el("input", { class: "text-input time-input", attrs: { type: "text" } });
  const startPick = el("button", {
    class: "button button-small",
    text: "⏱",
    attrs: { type: "button", title: "Take the playhead" },
  });
  const endPick = el("button", {
    class: "button button-small",
    text: "⏱",
    attrs: { type: "button", title: "Take the playhead" },
  });
  const speakerSelect = el("select", { class: "select", attrs: { "aria-label": "Side" } });
  const typeSelect = el("select", { class: "select", attrs: { "aria-label": "Move type" } });
  for (const type of MOVE_TYPES) {
    typeSelect.append(el("option", { text: `${MOVE_GLYPHS[type]}  ${type}`, attrs: { value: type } }));
  }
  const evalInput = el("input", {
    class: "text-input eval-input",
    attrs: { type: "number", step: "0.1", min: "-10", max: "10", "aria-label": "Evaluation" },
  });
  const gistInput = el("input", { class: "text-input", attrs: { type: "text", "aria-label": "Gist" } });
  const titleInput = el("input", { class: "text-input", attrs: { type: "text", "aria-label": "Card title" } });
  const explanationInput = el("textarea", {
    class: "text-input",
    attrs: { rows: "3", "aria-label": "Explanation" },
  });

  const saveButton = el("button", { class: "button button-primary", text: "Save move", attrs: { type: "button" } });
  const newButton = el("button", { class: "button", text: "New move", attrs: { type: "button" } });
  const upButton = el("button", { class: "button", text: "↑ Earlier", attrs: { type: "button" } });
  const downButton = el("button", { class: "button", text: "↓ Later", attrs: { type: "button" } });
  const deleteButton = el("button", { class: "button button-danger", text: "Delete", attrs: { type: "button" } });

  const notes = el("ul", { class: "editor-notes" });

  const element = el("section", {
    class: "move-editor",
    children: [
      el("h2", { class: "section-title", text: "Edit the move" }),
      el("div", {
        class: "editor-grid",
        children: [
          el("label", { class: "field", children: [el("span", { text: "Start" }), el("div", { class: "field-row", children: [startInput, startPick] })] }),
          el("label", { class: "field", children: [el("span", { text: "End" }), el("div", { class: "field-row", children: [endInput, endPick] })] }),
          el("label", { class: "field", children: [el("span", { text: "Side" }), speakerSelect] }),
          el("label", { class: "field", children: [el("span", { text: "Type" }), typeSelect] }),
          el("label", { class: "field", children: [el("span", { text: "Eval" }), evalInput] }),
        ],
      }),
      el("label", { class: "field", children: [el("span", { text: "Gist (after the name)" }), gistInput] }),
      el("label", { class: "field", children: [el("span", { text: "Card title" }), titleInput] }),
      el("label", { class: "field", children: [el("span", { text: "Explanation" }), explanationInput] }),
      el("div", { class: "toolbar", children: [saveButton, newButton, upButton, downButton, deleteButton] }),
      notes,
    ],
  });

  function setNotes(errors: string[], warnings: string[]): void {
    notes.replaceChildren(
      ...errors.map((text) => el("li", { class: "note-error", text })),
      ...warnings.map((text) => el("li", { class: "note-warning", text })),
    );
  }

  function disarmDelete(): void {
    deleteArmed = false;
    if (deleteTimer !== null) clearTimeout(deleteTimer);
    deleteTimer = null;
    deleteButton.textContent = "Delete";
  }

  function fill(draft: MoveDraft): void {
    startInput.value = draft.start;
    endInput.value = draft.end;
    speakerSelect.replaceChildren(
      ...(["white", "black"] as const).map((side) =>
        el("option", {
          text: `${SIDE_LABELS[side]} · ${options.names()[side]}`,
          attrs: { value: side },
        }),
      ),
    );
    speakerSelect.value = draft.speaker;
    typeSelect.value = draft.moveType;
    evalInput.value = String(draft.evalScore);
    gistInput.value = draft.gist;
    titleInput.value = draft.title;
    explanationInput.value = draft.explanation;
  }

  function readDraft(): MoveDraft {
    return {
      start: startInput.value,
      end: endInput.value,
      speaker: speakerSelect.value === "black" ? "black" : "white",
      moveType: MOVE_TYPES.find((type) => type === typeSelect.value) ?? "Best",
      evalScore: Number(evalInput.value),
      gist: gistInput.value,
      title: titleInput.value,
      explanation: explanationInput.value,
    };
  }

  function show(move: Move, moveIndex: number): void {
    index = moveIndex;
    disarmDelete();
    fill(draftOf(move));
    saveButton.textContent = "Save move";
    upButton.disabled = false;
    downButton.disabled = false;
    deleteButton.disabled = false;
    setNotes([], []);
  }

  function clear(): void {
    index = null;
    disarmDelete();
    fill({ start: "", end: "", speaker: "white", moveType: "Best", evalScore: 0, gist: "", title: "", explanation: "" });
    saveButton.textContent = "Add move";
    upButton.disabled = true;
    downButton.disabled = true;
    deleteButton.disabled = true;
    setNotes([], []);
  }

  function onSave(): void {
    const check = checkDraft(readDraft(), options.moves(), index, options.names(), options.duration());
    setNotes(check.errors, check.warnings);
    if (check.move === null) return;
    options.onSaved(check.move, index);
  }

  saveButton.addEventListener("click", onSave);

  newButton.addEventListener("click", () => {
    index = null;
    disarmDelete();
    fill(defaultDraft(options.moves(), options.playhead(), options.duration()));
    saveButton.textContent = "Add move";
    upButton.disabled = true;
    downButton.disabled = true;
    deleteButton.disabled = true;
    setNotes([], []);
  });

  startPick.addEventListener("click", () => {
    startInput.value = formatTimestamp(options.playhead());
  });
  endPick.addEventListener("click", () => {
    endInput.value = formatTimestamp(options.playhead());
  });

  upButton.addEventListener("click", () => {
    if (index !== null) options.onSwapped(index, -1);
  });
  downButton.addEventListener("click", () => {
    if (index !== null) options.onSwapped(index, 1);
  });

  deleteButton.addEventListener("click", () => {
    if (index === null) return;
    if (!deleteArmed) {
      deleteArmed = true;
      deleteButton.textContent = "Delete?";
      deleteTimer = window.setTimeout(disarmDelete, DELETE_CONFIRM_MS);
      return;
    }
    disarmDelete();
    options.onDeleted(index);
  });

  clear();

  return {
    element,
    show,
    clear,
    dispose() {
      disarmDelete();
    },
  };
}
