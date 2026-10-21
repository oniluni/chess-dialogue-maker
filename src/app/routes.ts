export type RouteId = "create" | "moves" | "manual" | "sounds" | "style";

export interface Route {
  id: RouteId;
  label: string;
  hint: string;
}

export const ROUTES: readonly Route[] = [
  { id: "create", label: "Создание видео", hint: "Источник, обрезка, разбор и сборка." },
  { id: "moves", label: "Ходы", hint: "Правка карточек разбора." },
  { id: "manual", label: "Ручной разбор", hint: "Реплики, оценки и ключевые моменты." },
  { id: "sounds", label: "Звуки", hint: "События и подача речи." },
  { id: "style", label: "Стиль видео", hint: "Цвета, шрифты и раскладка." },
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
