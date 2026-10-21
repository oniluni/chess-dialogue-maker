import {
  addSoundEvent,
  getSoundEvents,
  isSoundKind,
  removeSoundEvent,
  replaceDetected,
  SOUND_KINDS,
  soundKindLabel,
} from "../analysis/sound-events.ts";
import type { SoundKind } from "../analysis/sound-events.ts";
import { detectDelivery } from "../analysis/delivery.ts";
import { detectSounds } from "../analysis/sound-detect.ts";
import { getClip } from "../session.ts";
import { el } from "../ui/dom.ts";
import { formatTimestamp, messageOf, parseTimestamp } from "../ui/text.ts";
import type { Page } from "./page.ts";

/** Manual sound tags: what happens when, to be shown as tags over the video later. */
export function renderSoundsPage(): Page {
  const timeInput = el("input", {
    class: "text-input time-input",
    attrs: { type: "text", placeholder: "0:12.5", "aria-label": "Event time" },
  });
  const kindSelect = el("select", { class: "select", attrs: { "aria-label": "Sound kind" } });
  for (const kind of SOUND_KINDS) {
    kindSelect.append(el("option", { text: kind.label, attrs: { value: kind.id } }));
  }
  const labelInput = el("input", {
    class: "text-input",
    attrs: { type: "text", placeholder: "note (optional)", "aria-label": "Event note" },
  });
  const addButton = el("button", { class: "button", text: "Add event", attrs: { type: "button" } });
  const detectButton = el("button", { class: "button", text: "Detect sounds", attrs: { type: "button" } });
  const deliveryButton = el("button", { class: "button", text: "Detect delivery", attrs: { type: "button" } });

  /** What the model already found, so a second detection adds to it instead of wiping it. */
  function modelEvents(): Array<{ at: number; end: number | null; kind: SoundKind; label: string; peak: number; reliable: boolean }> {
    return getSoundEvents()
      .filter((event) => event.source === "model")
      .map((event) => ({ at: event.at, end: event.end, kind: event.kind, label: event.label, peak: event.peak, reliable: event.reliable }));
  }
  const status = el("p", { class: "status-line" });
  const list = el("ol", { class: "move-list" });
  const empty = el("p", { class: "placeholder", text: "No sound events yet." });

  const element = el("section", {
    class: "page",
    children: [
      el("h1", { class: "page-title", text: "Sounds" }),
      el("p", {
        class: "page-hint",
        text: "Sound tags of the clip: laughter, applause, music, a shout. Automatic detection comes next; for now they are added by hand and drawn over the video.",
      }),
      el("div", { class: "toolbar", children: [timeInput, kindSelect, labelInput, addButton, detectButton, deliveryButton] }),
      status,
      empty,
      list,
    ],
  });

  function render(): void {
    const events = getSoundEvents();
    list.replaceChildren(
      ...events.map((event) => {
        const remove = el("button", { class: "button button-small", text: "âœ•", attrs: { type: "button" } });
        remove.addEventListener("click", () => {
          removeSoundEvent(event.id);
          render();
          setStatus("Event removed.");
        });
        return el("li", {
          class: "move-row",
          children: [
            el("span", { class: "move-time", text: formatTimestamp(event.at) }),
            el("span", { class: "move-side", text: soundKindLabel(event.kind) }),
            el("span", { class: "move-title", text: event.label }),
            remove,
          ],
        });
      }),
    );
    list.hidden = events.length === 0;
    empty.hidden = events.length > 0;
  }

  function setStatus(text: string): void {
    status.textContent = text;
  }

  addButton.addEventListener("click", () => {
    const at = parseTimestamp(timeInput.value);
    if (at === null) {
      setStatus("Set the time: 1:02 or 62.");
      return;
    }
    if (Number.isNaN(at) || at < 0) {
      setStatus(`"${timeInput.value}" is not a time.`);
      return;
    }
    addSoundEvent({
      at,
      end: null,
      kind: kindSelect.value as SoundKind,
      label: labelInput.value.trim(),
      source: "user",
      peak: 1,
      reliable: true,
    });
    timeInput.value = "";
    labelInput.value = "";
    render();
    setStatus("Event added.");
  });

  detectButton.addEventListener("click", () => {
    void (async (): Promise<void> => {
      const clip = getClip();
      if (clip === null) {
        setStatus("Open a clip first.");
        return;
      }
      detectButton.disabled = true;
      setStatus("Listening to the clipâ€¦");
      try {
        const found = await detectSounds(clip.file);
        replaceDetected([
          ...modelEvents(),
          ...found.map((event) => ({
            at: event.start,
            end: event.end,
            kind: isSoundKind(event.kind) ? event.kind : ("other" as SoundKind),
            label: event.label,
            peak: event.peak,
            reliable: event.reliable,
          })),
        ]);
        render();
        setStatus(`Heard ${found.length} ${found.length === 1 ? "event" : "events"}.`);
      } catch (error) {
        setStatus(messageOf(error));
      } finally {
        detectButton.disabled = false;
      }
    })();
  });

  deliveryButton.addEventListener("click", () => {
    void (async (): Promise<void> => {
      const clip = getClip();
      if (clip === null) {
        setStatus("Open a clip first.");
        return;
      }
      deliveryButton.disabled = true;
      setStatus("Comparing the deliveryâ€¦");
      try {
        const found = await detectDelivery(clip.file);
        replaceDetected([
          ...modelEvents(),
          ...found.map((event) => ({
            at: event.start,
            end: event.end,
            kind: isSoundKind(event.kind) ? event.kind : ("other" as SoundKind),
            label: event.label,
            peak: event.peak,
            reliable: event.reliable,
          })),
        ]);
        render();
        setStatus(`Found ${found.length} delivery ${found.length === 1 ? "event" : "events"}.`);
      } catch (error) {
        setStatus(messageOf(error));
      } finally {
        deliveryButton.disabled = false;
      }
    })();
  });

  render();
  return { element };
}




