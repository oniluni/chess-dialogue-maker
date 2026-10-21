import { store } from "../state.ts";
import { renderPage } from "../ui/pages.ts";
import { DEFAULT_ROUTE, isRouteId } from "./routes.ts";
import type { RouteId } from "./routes.ts";

function routeFromHash(): RouteId {
  const value = location.hash.replace(/^#\/?/, "");
  return isRouteId(value) ? value : DEFAULT_ROUTE;
}

/** Renders the page for the current hash and follows navigation. */
export function startRouter(main: HTMLElement): void {
  let rendered: RouteId | null = null;

  store.subscribe((state) => {
    if (state.route === rendered) return;
    rendered = state.route;
    main.replaceChildren(renderPage(state.route));
  });

  window.addEventListener("hashchange", () => {
    store.set({ route: routeFromHash() });
  });

  store.set({ route: routeFromHash() });
}
