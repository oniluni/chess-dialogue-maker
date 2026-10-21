import type { RouteId } from "../app/routes.ts";
import { renderCreatePage } from "./create.ts";
import { renderManualPage } from "./manual.ts";
import { renderMovesPage } from "./moves.ts";
import type { Page } from "./page.ts";
import { renderSoundsPage } from "./sounds.ts";
import { renderStylePage } from "./style.ts";

const PAGES: Record<RouteId, () => Page> = {
  create: renderCreatePage,
  moves: renderMovesPage,
  manual: renderManualPage,
  sounds: renderSoundsPage,
  style: renderStylePage,
};

/** Builds the page for a route; the router disposes the previous one. */
export function renderPage(route: RouteId): Page {
  return PAGES[route]();
}
