import { store } from "../state.ts";
import type { BackendStatus } from "../state.ts";
import { el } from "../ui/dom.ts";
import { ROUTES } from "./routes.ts";

export interface Shell {
  main: HTMLElement;
}

const STATUS_TEXT: Record<BackendStatus, string> = {
  unknown: "server: checking",
  online: "server: online",
  offline: "server: offline",
};

/** Builds the header, the page navigation and the page slot. */
export function mountShell(root: HTMLElement): Shell {
  const status = el("span", {
    class: "status-pill",
    text: STATUS_TEXT.unknown,
    attrs: { "data-status": "unknown" },
  });

  const links = ROUTES.map((route) =>
    el("a", {
      class: "nav-link",
      text: route.label,
      attrs: { href: `#/${route.id}`, "data-route": route.id },
    }),
  );

  const main = el("main", { class: "main" });

  root.replaceChildren(
    el("header", {
      class: "header",
      children: [el("span", { class: "brand", text: "Chess Dialogue Maker" }), status],
    }),
    el("div", {
      class: "body",
      children: [
        el("aside", { class: "sidebar", children: [el("nav", { class: "nav", children: links })] }),
        main,
      ],
    }),
  );

  store.subscribe((state) => {
    status.textContent = STATUS_TEXT[state.backend];
    status.dataset["status"] = state.backend;
    for (const link of links) {
      link.classList.toggle("is-active", link.dataset["route"] === state.route);
    }
  });

  return { main };
}
