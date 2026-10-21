import type { SpeakerHint } from "../../shared/speakers.ts";
import type { Side } from "../analysis/move.ts";
import { detectSpeakers } from "../analysis/speakers.ts";
import { extractWav } from "../media/wav.ts";
import { getClip, getSpeakerHints, getTranscript, setSpeakerHints } from "../session.ts";
import { el } from "../ui/dom.ts";
import { messageOf } from "../ui/text.ts";

export interface SpeakerContext {
  hints: SpeakerHint[];
  sides: Record<"A" | "B", Side>;
}

export interface SpeakersPanel {
  element: HTMLElement;
  refresh(): void;
  /** What the analysis prompt and checks need, or `null` before detection. */
  context(): SpeakerContext | null;
}

/** Who speaks: detect the two voices, show the split and map each voice to a player. */
export function createSpeakersPanel(options: { onStatus: (text: string) => void }): SpeakersPanel {
  const button = el("button", { class: "button", text: "Detect speakers", attrs: { type: "button" } });
  const info = el("span", { class: "meta" });
  const sideA = el("select", { class: "select", attrs: { "aria-label": "Voice A side" } });
  const sideB = el("select", { class: "select", attrs: { "aria-label": "Voice B side" } });
  for (const select of [sideA, sideB]) {
    select.append(el("option", { text: "White", attrs: { value: "white" } }));
    select.append(el("option", { text: "Black", attrs: { value: "black" } }));
  }
  sideA.value = "white";
  sideB.value = "black";

  const element = el("div", {
    class: "toolbar",
    children: [
      button,
      el("label", { class: "field", children: [el("span", { text: "Voice A" }), sideA] }),
      el("label", { class: "field", children: [el("span", { text: "Voice B" }), sideB] }),
      info,
    ],
  });

  function refresh(): void {
    const hints = getSpeakerHints();
    const clip = getClip();
    const segments = getTranscript();
    button.disabled = clip === null || segments.length === 0;
    sideA.disabled = hints === null;
    sideB.disabled = hints === null;

    if (hints === null) {
      info.textContent = segments.length === 0 ? "" : "no voice hints yet";
      return;
    }

    sideA.value = hints.sides.A;
    sideB.value = hints.sides.B;
    let countA = 0;
    let countB = 0;
    let unsure = 0;
    for (const hint of hints.hints) {
      if (hint.label === "A" && hint.confidence >= 0.8) countA += 1;
      else if (hint.label === "B" && hint.confidence >= 0.8) countB += 1;
      else unsure += 1;
    }
    info.textContent = `A: ${countA} · B: ${countB} · unsure: ${unsure} · confidence ${hints.reliability.toFixed(2)}`;
  }

  function setSides(changed: "A" | "B"): void {
    const hints = getSpeakerHints();
    if (hints === null) return;
    const chosen: Side = (changed === "A" ? sideA.value : sideB.value) === "black" ? "black" : "white";
    const other: Side = chosen === "white" ? "black" : "white";
    setSpeakerHints({
      ...hints,
      sides: changed === "A" ? { A: chosen, B: other } : { A: other, B: chosen },
    });
    refresh();
  }

  async function detect(): Promise<void> {
    const clip = getClip();
    const segments = getTranscript();
    if (clip === null || segments.length === 0) return;

    button.disabled = true;
    options.onStatus("Comparing the voices…");
    try {
      const wav = await extractWav(clip.file);
      const result = await detectSpeakers(
        wav,
        segments.map((segment) => ({ start: segment.start, end: segment.end })),
      );
      if (getClip() !== clip || getTranscript() !== segments) return;
      if (result.hints.length !== segments.length) throw new Error("the answer does not match the transcript");

      const previous = getSpeakerHints();
      setSpeakerHints({
        hints: result.hints,
        sides: previous === null ? { A: "white", B: "black" } : previous.sides,
        reliability: result.reliability,
      });
      options.onStatus(`Voices compared: ${result.reliability.toFixed(2)} reliability.`);
    } catch (error) {
      options.onStatus(messageOf(error));
    } finally {
      refresh();
    }
  }

  button.addEventListener("click", () => {
    void detect();
  });
  sideA.addEventListener("change", () => {
    setSides("A");
  });
  sideB.addEventListener("change", () => {
    setSides("B");
  });

  refresh();

  return {
    element,
    refresh,
    context() {
      const hints = getSpeakerHints();
      return hints === null ? null : { hints: hints.hints, sides: hints.sides };
    },
  };
}
