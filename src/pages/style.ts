import { getStyle, setStyle, STYLE_PRESETS } from "../render/style.ts";
import { el } from "../ui/dom.ts";
import type { Page } from "./page.ts";

/** Visual presets: colours of the page, the card and the players bar. */
export function renderStylePage(): Page {
  const current = el("p", { class: "meta" });
  const buttons = STYLE_PRESETS.map((preset) => {
    const button = el("button", { class: "button", text: preset.label, attrs: { type: "button" } });
    button.addEventListener("click", () => {
      setStyle(preset.id);
      refresh();
    });
    return button;
  });

  const element = el("section", {
    class: "page",
    children: [
      el("h1", { class: "page-title", text: "Style" }),
      el("p", {
        class: "page-hint",
        text: "The look of the finished video: the page, the move card and the players bar.",
      }),
      el("div", { class: "toolbar", children: buttons }),
      current,
      el("p", {
        class: "page-hint",
        text: "The preview and the export both read the preset, so changing it here changes the rendered frames.",
      }),
    ],
  });

  function refresh(): void {
    const style = getStyle();
    current.textContent = `Current: ${style.label} · page ${style.pageBg} · card ${style.cardBg} · players ${style.playersBg}`;
    buttons.forEach((button, index) => {
      const preset = STYLE_PRESETS[index];
      button.classList.toggle("button-primary", preset !== undefined && preset.id === style.id);
    });
  }

  refresh();
  return { element };
}
