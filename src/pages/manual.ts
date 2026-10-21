import type { Dialogue, DialogueMoment } from "../analysis/dialogue.ts";
import { attachMoves, buildDialogue, fromMoves, fromTranscript } from "../analysis/dialogue.ts";
import { MOVE_GLYPHS, MOVE_TYPES, SIDE_LABELS } from "../analysis/move.ts";
import type { MoveType, Side } from "../analysis/move.ts";
import { getClip, getDialogue, getMoves, getNames, getTranscript, setDialogue, setMoves } from "../session.ts";
import { el } from "../ui/dom.ts";
import { formatTimestamp } from "../ui/text.ts";
import type { Page } from "./page.ts";

function sideValue(value: string): Side | null {
  return value === "white" || value === "black" ? value : null;
}

function typeValue(value: string): MoveType | null {
  return MOVE_TYPES.find((type) => type === value) ?? null;
}

/** Manual analysis: the dialogue lines and the moments that become cards, no model involved. */
export function renderManualPage(): Page {
  const clip = getClip();
  let dialogue: Dialogue | null = getDialogue();

  const linesHost = el("div", { class: "manual-lines" });
  const momentsHost = el("div", { class: "moments" });
  const notes = el("ul", { class: "editor-notes" });
  const status = el("p", { class: "status-line" });
  const counter = el("span", { class: "meta" });
  const empty = el("p", { class: "placeholder" });

  const transcriptButton = el("button", { class: "button", text: "Lines from transcript", attrs: { type: "button" } });
  const movesButton = el("button", { class: "button", text: "Lines from moves", attrs: { type: "button" } });
  const addButton = el("button", { class: "button", text: "Add moment", attrs: { type: "button" } });
  const buildButton = el("button", { class: "button button-primary", text: "Build moves", attrs: { type: "button" } });

  const element = el("section", {
    class: "page",
    children: [
      el("h1", { class: "page-title", text: "Manual analysis" }),
      el("p", {
        class: "page-hint",
        text: "Write the review by hand: the lines give the timing, the moments become the cards.",
      }),
      el("div", { class: "toolbar", children: [transcriptButton, movesButton, addButton, buildButton, counter] }),
      status,
      notes,
      empty,
      linesHost,
      momentsHost,
    ],
  });

  function setStatus(text: string): void {
    status.textContent = text;
  }

  function setNotes(errors: string[], warnings: string[]): void {
    notes.replaceChildren(
      ...errors.map((text) => el("li", { class: "note-error", text })),
      ...warnings.map((text) => el("li", { class: "note-warning", text })),
    );
  }

  function renderLines(): void {
    if (dialogue === null) {
      linesHost.replaceChildren();
      return;
    }
    linesHost.replaceChildren(
      el("h2", { class: "section-title", text: "Dialogue lines" }),
      ...dialogue.lines.map((line, index) => {
        const text = el("input", {
          class: "text-input line-text",
          attrs: { type: "text", value: line.text, "aria-label": `Line ${index + 1}` },
        });
        text.addEventListener("input", () => {
          if (dialogue !== null) dialogue.lines[index] = { ...(dialogue.lines[index] ?? line), text: text.value };
        });

        const side = el("select", { class: "select line-side", attrs: { "aria-label": `Speaker of line ${index + 1}` } });
        side.append(el("option", { text: "—", attrs: { value: "" } }));
        for (const value of ["white", "black"] as const) {
          side.append(el("option", { text: SIDE_LABELS[value], attrs: { value } }));
        }
        side.value = line.speaker ?? "";
        side.addEventListener("change", () => {
          if (dialogue !== null) dialogue.lines[index] = { ...(dialogue.lines[index] ?? line), speaker: sideValue(side.value) };
        });

        const strength = el("input", {
          class: "text-input strength-input",
          attrs: {
            type: "number",
            min: "-3",
            max: "3",
            step: "0.5",
            placeholder: "\u00b13",
            "aria-label": `Strength of line ${index + 1}`,
          },
        });
        strength.value = line.strength === null ? "" : String(line.strength);
        strength.addEventListener("input", () => {
          if (dialogue === null) return;
          const value = strength.value.trim() === "" ? null : Number(strength.value);
          dialogue.lines[index] = {
            ...(dialogue.lines[index] ?? line),
            strength: value === null || !Number.isFinite(value) ? null : Math.max(-3, Math.min(3, value)),
          };
        });

        const motif = el("input", {
          class: "text-input motif-input",
          attrs: {
            type: "text",
            value: line.motif,
            placeholder: "motif",
            "aria-label": `Motif of line ${index + 1}`,
          },
        });
        motif.addEventListener("input", () => {
          if (dialogue !== null) dialogue.lines[index] = { ...(dialogue.lines[index] ?? line), motif: motif.value };
        });

        return el("div", {
          class: "line-row",
          children: [
            el("span", { class: "line-number", text: String(index + 1) }),
            el("span", { class: "move-time", text: `${formatTimestamp(line.start)}–${formatTimestamp(line.end)}` }),
            text,
            side,
            strength,
            motif,
          ],
        });
      }),
    );
  }

  function momentCard(moment: DialogueMoment, index: number): HTMLElement {
    const lineCount = dialogue?.lines.length ?? 0;

    const from = el("select", { class: "select", attrs: { "aria-label": "First line of the moment" } });
    const to = el("select", { class: "select", attrs: { "aria-label": "Last line of the moment" } });
    for (let lineIndex = 0; lineIndex < lineCount; lineIndex += 1) {
      const line = dialogue?.lines[lineIndex];
      const label = `${lineIndex + 1} · ${line === undefined ? "" : formatTimestamp(line.start)}`;
      from.append(el("option", { text: label, attrs: { value: String(lineIndex) } }));
      to.append(el("option", { text: label, attrs: { value: String(lineIndex) } }));
    }
    from.value = String(Math.min(moment.from, Math.max(0, lineCount - 1)));
    to.value = String(Math.min(moment.to, Math.max(0, lineCount - 1)));
    from.addEventListener("change", () => {
      if (dialogue !== null) dialogue.moments[index] = { ...(dialogue.moments[index] ?? moment), from: Number(from.value) };
    });
    to.addEventListener("change", () => {
      if (dialogue !== null) dialogue.moments[index] = { ...(dialogue.moments[index] ?? moment), to: Number(to.value) };
    });

    const side = el("select", { class: "select", attrs: { "aria-label": "Side of the moment" } });
    side.append(el("option", { text: "Auto", attrs: { value: "" } }));
    for (const value of ["white", "black"] as const) {
      side.append(el("option", { text: SIDE_LABELS[value], attrs: { value } }));
    }
    side.value = moment.speaker ?? "";
    side.addEventListener("change", () => {
      if (dialogue !== null) dialogue.moments[index] = { ...(dialogue.moments[index] ?? moment), speaker: sideValue(side.value) };
    });

    const type = el("select", { class: "select", attrs: { "aria-label": "Type of the moment" } });
    type.append(el("option", { text: "Best", attrs: { value: "" } }));
    for (const value of MOVE_TYPES) {
      type.append(el("option", { text: `${MOVE_GLYPHS[value]} ${value}`, attrs: { value } }));
    }
    type.value = moment.moveType ?? "";
    type.addEventListener("change", () => {
      if (dialogue !== null) dialogue.moments[index] = { ...(dialogue.moments[index] ?? moment), moveType: typeValue(type.value) };
    });

    const evalInput = el("input", {
      class: "text-input eval-input",
      attrs: { type: "number", step: "0.1", min: "-10", max: "10", placeholder: "auto", "aria-label": "Eval of the moment" },
    });
    evalInput.value = moment.evalScore === null ? "" : String(moment.evalScore);
    evalInput.addEventListener("input", () => {
      if (dialogue === null) return;
      const value = evalInput.value.trim() === "" ? null : Number(evalInput.value);
      dialogue.moments[index] = {
        ...(dialogue.moments[index] ?? moment),
        evalScore: value === null || !Number.isFinite(value) ? null : Math.max(-10, Math.min(10, value)),
      };
    });

    const gist = el("input", { class: "text-input", attrs: { type: "text", value: moment.gist, "aria-label": "Gist of the moment" } });
    gist.addEventListener("input", () => {
      if (dialogue !== null) dialogue.moments[index] = { ...(dialogue.moments[index] ?? moment), gist: gist.value };
    });

    const title = el("input", { class: "text-input", attrs: { type: "text", value: moment.title, "aria-label": "Title of the moment" } });
    title.addEventListener("input", () => {
      if (dialogue !== null) dialogue.moments[index] = { ...(dialogue.moments[index] ?? moment), title: title.value };
    });

    const explanation = el("textarea", {
      class: "text-input",
      attrs: { rows: "2", "aria-label": "Explanation of the moment" },
    });
    explanation.value = moment.explanation;
    explanation.addEventListener("input", () => {
      if (dialogue !== null) dialogue.moments[index] = { ...(dialogue.moments[index] ?? moment), explanation: explanation.value };
    });

    const remove = el("button", { class: "button button-small button-danger", text: "Remove", attrs: { type: "button" } });
    remove.addEventListener("click", () => {
      if (dialogue === null) return;
      dialogue.moments = dialogue.moments.filter((_, item) => item !== index);
      renderMoments();
    });

    return el("div", {
      class: "moment-card",
      children: [
        el("div", {
          class: "moment-head",
          children: [
            el("span", { class: "recovery-number", text: String(index + 1) }),
            el("label", { class: "field", children: [el("span", { text: "Lines" }), el("div", { class: "field-row", children: [from, to] })] }),
            el("label", { class: "field", children: [el("span", { text: "Side" }), side] }),
            el("label", { class: "field", children: [el("span", { text: "Type" }), type] }),
            el("label", { class: "field", children: [el("span", { text: "Eval" }), evalInput] }),
            remove,
          ],
        }),
        el("label", { class: "field", children: [el("span", { text: "Gist" }), gist] }),
        el("label", { class: "field", children: [el("span", { text: "Card title" }), title] }),
        el("label", { class: "field", children: [el("span", { text: "Explanation" }), explanation] }),
      ],
    });
  }

  function renderMoments(): void {
    if (dialogue === null) {
      momentsHost.replaceChildren();
      return;
    }
    momentsHost.replaceChildren(
      el("h2", { class: "section-title", text: "Moments" }),
      ...dialogue.moments.map((moment, index) => momentCard(moment, index)),
    );
  }

  function renderAll(): void {
    const current = dialogue;
    empty.hidden = current !== null;
    if (current === null) {
      empty.textContent =
        clip === null
          ? "Open a clip on the Create video page first."
          : getTranscript().length > 0
            ? "Load the lines from the transcript to start."
            : "No transcript yet: transcribe the clip on the Create page, or start from the current moves.";
    }
    linesHost.hidden = current === null;
    momentsHost.hidden = current === null;
    addButton.disabled = current === null;
    buildButton.disabled = current === null;
    counter.textContent = current === null ? "" : `${current.lines.length} lines · ${current.moments.length} moments`;
    renderLines();
    renderMoments();
  }

  transcriptButton.addEventListener("click", () => {
    const segments = getTranscript();
    if (segments.length === 0) {
      setStatus("There is no transcript yet: transcribe the clip on the Create page.");
      return;
    }
    const base = fromTranscript(segments);
    dialogue = getMoves().length > 0 ? attachMoves(base, getMoves()) : base;
    setDialogue(dialogue);
    renderAll();
    setStatus(`Loaded ${segments.length} lines from the transcript.`);
  });

  movesButton.addEventListener("click", () => {
    const moves = getMoves();
    if (moves.length === 0) {
      setStatus("There are no moves yet to start from.");
      return;
    }
    dialogue = fromMoves(moves);
    setDialogue(dialogue);
    renderAll();
    setStatus(`Loaded ${moves.length} moves as lines; the words have to be typed in.`);
  });

  addButton.addEventListener("click", () => {
    if (dialogue === null) return;
    const last = dialogue.moments[dialogue.moments.length - 1];
    const size = dialogue.lines.length - 1;
    const from = Math.max(0, Math.min(last === undefined ? 0 : last.to + 1, size));
    dialogue.moments = [
      ...dialogue.moments,
      { from, to: from, title: "", gist: "", explanation: "", moveType: null, speaker: null, evalScore: null },
    ];
    renderAll();
  });

  buildButton.addEventListener("click", () => {
    if (dialogue === null || clip === null) return;
    const result = buildDialogue(dialogue, getNames(), clip.media.info.duration);
    setNotes(result.errors, result.warnings);
    if (result.errors.length > 0) {
      setStatus("Fix the moments below, then build again.");
      return;
    }
    setMoves(result.moves);
    setStatus(`${result.moves.length === 1 ? "1 move" : `${result.moves.length} moves`} built — open the Create page to render.`);
  });

  renderAll();
  if (dialogue === null && clip !== null && getTranscript().length > 0) {
    setStatus("Load the lines from the transcript, then group them into moments.");
  }

  return { element };
}

