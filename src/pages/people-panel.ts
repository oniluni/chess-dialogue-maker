import { cropAvatar } from "../analysis/avatar.ts";
import { detectPeople } from "../analysis/vision.ts";
import type { Side } from "../analysis/move.ts";
import { setAvatars } from "../render/avatar-store.ts";
import { getCast, getClip, getNames, getPeople, setCast, setNames, setPeople } from "../session.ts";
import type { CastEntry } from "../session.ts";
import { el } from "../ui/dom.ts";
import { messageOf } from "../ui/text.ts";

export interface CastPanel {
  element: HTMLElement;
  refresh(): void;
}

/** Finds the people in the clip and gives them names and sides. */
export function createCastPanel(options: { onAvatars: () => void }): CastPanel {
  let busy = false;

  const findButton = el("button", { class: "button", text: "Find people", attrs: { type: "button" } });
  const applyButton = el("button", { class: "button button-primary", text: "Apply names", attrs: { type: "button" } });
  const status = el("p", { class: "status-line" });
  const list = el("div", { class: "cast-list" });

  const element = el("section", {
    class: "analysis",
    children: [
      el("h2", { class: "section-title", text: "People" }),
      el("div", { class: "toolbar", children: [findButton, applyButton] }),
      status,
      list,
    ],
  });

  function setStatus(text: string): void {
    status.textContent = text;
  }

  function save(person: number, name: string, side: Side | null): void {
    const entries = [...getCast()];
    const index = entries.findIndex((entry) => entry.person === person);
    const entry: CastEntry = { person, name, side };
    if (index < 0) entries.push(entry);
    else entries[index] = entry;
    setCast(entries);
  }

  function render(): void {
    const found = getPeople();
    if (found === null || found.persons.length === 0) {
      list.replaceChildren(el("p", { class: "placeholder", text: "No faces yet: run «Find people»." }));
      return;
    }

    const entries = getCast();
    list.replaceChildren(
      ...found.persons.map((person) => {
        const entry = entries.find((item) => item.person === person.id);
        const name = el("input", {
          class: "text-input",
          attrs: { type: "text", value: entry?.name ?? "", "aria-label": `Name of person ${person.id + 1}` },
        });
        name.addEventListener("input", () => {
          save(person.id, name.value, entry?.side ?? null);
        });

        const side = el("select", { class: "select", attrs: { "aria-label": `Side of person ${person.id + 1}` } });
        side.append(el("option", { text: "Not a player", attrs: { value: "" } }));
        side.append(el("option", { text: "White", attrs: { value: "white" } }));
        side.append(el("option", { text: "Black", attrs: { value: "black" } }));
        side.value = entry?.side ?? "";
        side.addEventListener("change", () => {
          const chosen = side.value === "white" || side.value === "black" ? (side.value as Side) : null;
          save(person.id, name.value, chosen);
        });

        return el("div", {
          class: "cast-row",
          children: [
            el("span", { class: "cast-person", text: `Person ${person.id + 1}` }),
            el("span", { class: "meta", text: `${person.frames} frames · ${Math.round(person.share * 100)}%` }),
            name,
            side,
          ],
        });
      }),
    );
  }

  findButton.addEventListener("click", () => {
    void (async (): Promise<void> => {
      const clip = getClip();
      if (clip === null || busy) return;
      busy = true;
      findButton.disabled = true;
      setStatus("Looking for faces…");
      try {
        const result = await detectPeople(clip.file, clip.range.start, clip.range.end);
        if (result.persons.length === 0) {
          setPeople(null);
          setStatus("No faces found in the kept range.");
        } else {
          setPeople(result);
          const names = getNames();
          setCast(
            result.persons.map((person) => ({
              person: person.id,
              name: person.id === 0 ? names.white : person.id === 1 ? names.black : "",
              side: person.id === 0 ? "white" : person.id === 1 ? "black" : null,
            })),
          );
          setStatus(`${result.persons.length} ${result.persons.length === 1 ? "person" : "people"} found.`);

          const urls: Partial<Record<Side, string | null>> = {};
          for (const person of result.persons.slice(0, 2)) {
            const side: Side = person.id === 0 ? "white" : "black";
            try {
              urls[side] = await cropAvatar(clip.file, person.preview.time, person.preview);
            } catch (error) {
              console.warn("avatar crop failed:", error);
            }
          }
          if (Object.keys(urls).length > 0) {
            setAvatars(urls);
            options.onAvatars();
          }
        }
      } catch (error) {
        setStatus(messageOf(error));
      } finally {
        busy = false;
        findButton.disabled = false;
        render();
      }
    })();
  });

  applyButton.addEventListener("click", () => {
    const entries = getCast();
    const white = entries.find((entry) => entry.side === "white");
    const black = entries.find((entry) => entry.side === "black");
    if (white !== undefined && white.name.trim() !== "") setNames({ white: white.name.trim() });
    if (black !== undefined && black.name.trim() !== "") setNames({ black: black.name.trim() });
    setStatus("The names are applied to the players.");
  });

  return {
    element,
    refresh() {
      render();
    },
  };
}
