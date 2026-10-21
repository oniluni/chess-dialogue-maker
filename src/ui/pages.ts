import { routeById } from "../app/routes.ts";
import type { RouteId } from "../app/routes.ts";
import { el } from "./dom.ts";

const PLANNED: Record<RouteId, string> = {
  create: "медиа-ядро: фрагмент, таймлайн и сборка",
  moves: "редактор ходов",
  manual: "ручной разбор реплик",
  sounds: "звуковые события",
  style: "редактор стиля",
};

/** Page stub: the shell proves navigation before the pages gain content. */
export function renderPage(route: RouteId): HTMLElement {
  const meta = routeById(route);
  return el("section", {
    class: "page",
    children: [
      el("h1", { class: "page-title", text: meta.label }),
      el("p", { class: "page-hint", text: meta.hint }),
      el("p", { class: "placeholder", text: `Каркас страницы. Здесь появится ${PLANNED[route]}.` }),
    ],
  });
}
