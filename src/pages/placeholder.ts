import { routeById } from "../app/routes.ts";
import type { RouteId } from "../app/routes.ts";
import { el } from "../ui/dom.ts";
import type { Page } from "./page.ts";

type StubRoute = Exclude<RouteId, "create" | "moves" | "manual">;

const PLANNED: Record<StubRoute, string> = {
  sounds: "sound events",
  style: "the style editor",
};

/** Stub for pages the rewrite has not reached yet. */
export function renderPlaceholder(route: StubRoute): Page {
  const meta = routeById(route);
  return {
    element: el("section", {
      class: "page",
      children: [
        el("h1", { class: "page-title", text: meta.label }),
        el("p", { class: "page-hint", text: meta.hint }),
        el("p", { class: "placeholder", text: `Skeleton page: ${PLANNED[route]} lands here.` }),
      ],
    }),
  };
}
