import { store } from "../state.ts";
import { renderPage } from "../pages/index.ts";
import type { Page } from "../pages/page.ts";
import { DEFAULT_ROUTE, isRouteId } from "./routes.ts";
import type { RouteId } from "./routes.ts";

function routeFromHash(): RouteId {
  const value = location.hash.replace(/^#\/?/, "");
  return isRouteId(value) ? value : DEFAULT_ROUTE;
}

let rerender: (() => void) | null = null;

/** Renders the current route again, keeping the hash: used after a project is loaded. */
export function reloadPage(): void {
  rerender?.();
}

/** Renders the page for the current hash and follows navigation. */
export function startRouter(main: HTMLElement): void {
  let shown: { route: RouteId; page: Page } | null = null;

  const show = (route: RouteId): void => {
    shown?.page.dispose?.();
    const page = renderPage(route);
    shown = { route, page };
    main.replaceChildren(page.element);
  };

  rerender = () => {
    show(shown?.route ?? routeFromHash());
  };

  store.subscribe((state) => {
    if (shown?.route === state.route) return;
    show(state.route);
  });

  window.addEventListener("hashchange", () => {
    store.set({ route: routeFromHash() });
  });

  store.set({ route: routeFromHash() });
}
