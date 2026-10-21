import type { RouteId } from "../app/routes.ts";
import { renderCreatePage } from "./create.ts";
import type { Page } from "./page.ts";
import { renderPlaceholder } from "./placeholder.ts";

/** Builds the page for a route; the router disposes the previous one. */
export function renderPage(route: RouteId): Page {
  return route === "create" ? renderCreatePage() : renderPlaceholder(route);
}
