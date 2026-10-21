import { store } from "../state.ts";
import { renderPage } from "../pages/index.ts";
import type { Page } from "../pages/page.ts";
import { DEFAULT_ROUTE, isRouteId } from "./routes.ts";
import type { RouteId } from "./routes.ts";

function routeFromHash(): RouteId {
  const value = location.hash.replace(/^#\/?/, "");
  return isRouteId(value) ? value : DEFAULT_ROUTE;
}

/** Renders the page for the current hash and follows navigation. */
export function startRouter(main: HTMLElement): void {
  let shown: { route: RouteId; page: Page } | null = null;

  store.subscribe((state) => {
    if (shown?.route === state.route) return;
    shown?.page.dispose?.();
    const page = renderPage(state.route);
    shown = { route: state.route, page };
    main.replaceChildren(page.element);
  });

  window.addEventListener("hashchange", () => {
    store.set({ route: routeFromHash() });
  });

  store.set({ route: routeFromHash() });
}
