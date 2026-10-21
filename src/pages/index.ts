import type { RouteId } from "../app/routes.ts";
import { renderCreatePage } from "./create.ts";
import { renderMovesPage } from "./moves.ts";
import type { Page } from "./page.ts";
import { renderPlaceholder } from "./placeholder.ts";

/** Builds the page for a route; the router disposes the previous one. */
export function renderPage(route: RouteId): Page {
  if (route === "create") return renderCreatePage();
  if (route === "moves") return renderMovesPage();
  return renderPlaceholder(route);
}
