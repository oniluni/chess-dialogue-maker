import { pauseSfxEvents } from "../audio/events.ts";
import type { SoundBoard } from "../audio/player.ts";
import { PAUSE_SETS, TYPING_SETS } from "../audio/sfx.ts";
import { getScheduleOptions, setScheduleOptions } from "../session.ts";
import { el } from "../ui/dom.ts";

const PREVIEW_CHARS = 40;
const PREVIEW_SECONDS = 2.5;

/** Sound toggles, volume and the two set pickers, with an audition button. */
export function createAudioPanel(soundBoard: SoundBoard): HTMLElement {
  const options = getScheduleOptions();

  const sfxToggle = el("input", { attrs: { type: "checkbox" } });
  sfxToggle.checked = options.sfxEnabled;

  const volume = el("input", {
    class: "volume",
    attrs: {
      type: "range",
      min: "0",
      max: "1",
      step: "0.05",
      value: String(options.sfxVolume),
      "aria-label": "Sound volume",
    },
  });
  const typingSelect = el("select", {
    class: "select",
    attrs: { "aria-label": "Typing sound" },
    children: TYPING_SETS.map((set) => el("option", { text: set.label, attrs: { value: set.id } })),
  });
  typingSelect.value = options.sfxTypingSet;

  const pauseSelect = el("select", {
    class: "select",
    attrs: { "aria-label": "Pause sound" },
    children: PAUSE_SETS.map((set) => el("option", { text: set.label, attrs: { value: set.id } })),
  });
  pauseSelect.value = options.sfxPauseSet;

  const previewButton = el("button", { class: "button", text: "Preview sounds", attrs: { type: "button" } });

  sfxToggle.addEventListener("change", () => {
    setScheduleOptions({ sfxEnabled: sfxToggle.checked });
  });
  volume.addEventListener("input", () => {
    const value = Number(volume.value);
    setScheduleOptions({ sfxVolume: value });
    soundBoard.setVolume(value);
  });
  typingSelect.addEventListener("change", () => {
    setScheduleOptions({ sfxTypingSet: typingSelect.value });
    soundBoard.setSets(typingSelect.value, pauseSelect.value);
  });
  pauseSelect.addEventListener("change", () => {
    setScheduleOptions({ sfxPauseSet: pauseSelect.value });
    soundBoard.setSets(typingSelect.value, pauseSelect.value);
  });
  previewButton.addEventListener("click", () => {
    void (async (): Promise<void> => {
      const current = getScheduleOptions();
      soundBoard.setVolume(current.sfxVolume);
      soundBoard.setSets(current.sfxTypingSet, current.sfxPauseSet);
      await soundBoard.unlock();
      if (!current.sfxEnabled || current.sfxVolume <= 0) return;
      await soundBoard.playSequence(pauseSfxEvents(PREVIEW_CHARS, PREVIEW_SECONDS, current));
    })();
  });

  return el("div", {
    class: "audio",
    children: [
      el("label", { class: "option", children: [sfxToggle, el("span", { text: "Sound effects" })] }),
      el("label", { class: "field", children: [el("span", { text: "Volume" }), volume] }),
      el("label", { class: "field", children: [el("span", { text: "Typing" }), typingSelect] }),
      el("label", { class: "field", children: [el("span", { text: "Pause" }), pauseSelect] }),
      previewButton,
    ],
  });
}
