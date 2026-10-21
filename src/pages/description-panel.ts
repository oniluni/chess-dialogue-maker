import { getMoves, getNames } from "../session.ts";
import { el } from "../ui/dom.ts";

const HASHTAGS = ["#chess", "#dialoguereview", "#shorts"];

/** Rules only, no model: a caption from the source title and the players. */
function caption(title: string, kind: string): string {
  const clean = title
    .replace(/#\w+/g, " ")
    .replace(/[|•·–—-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  const names = getNames();
  const moves = getMoves().length;
  const lead =
    kind === "interview"
      ? "Dialogue review of an interview"
      : kind === "scene"
        ? "Dialogue review of a scene"
        : "Dialogue review";

  const parts = [lead + (clean === "" ? "" : `: ${clean}`), `${names.white} vs ${names.black}`];
  if (moves > 0) parts.push(`${moves} ${moves === 1 ? "move" : "moves"}`);
  return `${parts.join(" · ")}\n\n${HASHTAGS.join(" ")}`;
}

export function createDescriptionPanel(): HTMLElement {
  const titleInput = el("input", {
    class: "text-input",
    attrs: { type: "text", placeholder: "source title", "aria-label": "Source title" },
  });
  const kindSelect = el("select", { class: "select", attrs: { "aria-label": "Source kind" } });
  kindSelect.append(el("option", { text: "Scene / film", attrs: { value: "scene" } }));
  kindSelect.append(el("option", { text: "Interview", attrs: { value: "interview" } }));
  kindSelect.append(el("option", { text: "Other", attrs: { value: "other" } }));

  const makeButton = el("button", { class: "button", text: "Make caption", attrs: { type: "button" } });
  const copyButton = el("button", { class: "button", text: "Copy", attrs: { type: "button" } });
  const output = el("textarea", {
    class: "text-input",
    attrs: { rows: "4", "aria-label": "Caption", readonly: "" },
  });
  const status = el("p", { class: "status-line" });

  const element = el("section", {
    class: "analysis",
    children: [
      el("h2", { class: "section-title", text: "Description" }),
      el("div", { class: "toolbar", children: [titleInput, kindSelect, makeButton, copyButton] }),
      output,
      status,
    ],
  });

  makeButton.addEventListener("click", () => {
    output.value = caption(titleInput.value, kindSelect.value);
    status.textContent = "Caption built from the title and the players.";
  });

  copyButton.addEventListener("click", () => {
    if (output.value === "") {
      status.textContent = "Build the caption first.";
      return;
    }
    void navigator.clipboard
      .writeText(output.value)
      .then(() => {
        status.textContent = "Copied.";
      })
      .catch(() => {
        status.textContent = "The browser did not allow copying: select the text and copy it.";
      });
  });

  return element;
}
