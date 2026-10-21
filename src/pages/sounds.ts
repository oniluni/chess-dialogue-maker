import { el } from "../ui/dom.ts";
import { formatTimestamp, parseTimestamp } from "../ui/text.ts";
import type { Page } from "./page.ts";

export type SoundKind = "laugh" | "applause" | "music" | "shout" | "silence" | "other";

export const SOUND_KINDS: Array<{ id: SoundKind; label: string }> = [
  { id: "laugh", label: "Laughter" },
  { id: "applause", label: "Applause" },
  { id: "music", label: "Music" },
  { id: "shout", label: "Shout" },
  { id: "silence", label: "Silence" },
  { id: "other", label: "Other" },
];

export interface SoundEvent {
  id: number;
  at: number;
  kind: SoundKind;
  label: string;
}

let events: SoundEvent[] = [];
let nextId = 1;

export function getSoundEvents(): SoundEvent[] {
  return events;
}

export function setSoundEvents(next: SoundEvent[]): void {
  events = next;
  nextId = Math.max(0, ...next.map((event) => event.id)) + 1;
}

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
  const status = el("p", { class: "status-line" });
  const list = el("ol", { class: "move-list" });
  const empty = el("p", { class: "placeholder", text: "No sound events yet." });

  const element = el("section", {
    class: "page",
    children: [
      el("h1", { class: "page-title", text: "Sounds" }),
      el("p", {
        class: "page-hint",
        text: "Sound tags of the clip: laughter, applause, music, a shout. Automatic detection comes next; for now they are added by hand.",
      }),
      el("div", { class: "toolbar", children: [timeInput, kindSelect, labelInput, addButton] }),
      status,
      empty,
      list,
    ],
  });

  function render(): void {
    list.replaceChildren(
      ...events.map((event) => {
        const kind = SOUND_KINDS.find((item) => item.id === event.kind)?.label ?? event.kind;
        const remove = el("button", { class: "button button-small", text: "✕", attrs: { type: "button" } });
        remove.addEventListener("click", () => {
          events = events.filter((item) => item.id !== event.id);
          render();
          setStatus("Event removed.");
        });
        return el("li", {
          class: "move-row",
          children: [
            el("span", { class: "move-time", text: formatTimestamp(event.at) }),
            el("span", { class: "move-side", text: kind }),
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
    events = [
      ...events,
      { id: nextId, at, kind: kindSelect.value as SoundKind, label: labelInput.value.trim() },
    ];
    nextId += 1;
    events.sort((left, right) => left.at - right.at);
    timeInput.value = "";
    labelInput.value = "";
    render();
    setStatus("Event added.");
  });

  render();
  return { element };
}
