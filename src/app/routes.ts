export type RouteId = "create" | "moves" | "manual" | "sounds" | "style";

export interface Route {
  id: RouteId;
  label: string;
  hint: string;
}

export const ROUTES: readonly Route[] = [
  { id: "create", label: "Create video", hint: "Source, trim, analysis and render." },
  { id: "moves", label: "Moves", hint: "Edit the move cards." },
  { id: "manual", label: "Manual analysis", hint: "Lines, ratings and key moments." },
  { id: "sounds", label: "Sounds", hint: "Events and speech delivery." },
  { id: "style", label: "Style", hint: "Colours, fonts and layout." },
];

export const DEFAULT_ROUTE: RouteId = "create";

export function isRouteId(value: string): value is RouteId {
  return ROUTES.some((route) => route.id === value);
}

export function routeById(id: RouteId): Route {
  const route = ROUTES.find((candidate) => candidate.id === id);
  if (route === undefined) throw new Error(`unknown route: ${id}`);
  return route;
}
