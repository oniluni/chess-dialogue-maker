import type { RouteId } from "./app/routes.ts";

export type BackendStatus = "unknown" | "online" | "offline";

/** Everything the UI needs to know right now. Grows as pages gain state. */
export interface AppState {
  route: RouteId;
  backend: BackendStatus;
}

type Listener = (state: AppState) => void;

export interface Store {
  get(): AppState;
  set(patch: Partial<AppState>): void;
  subscribe(listener: Listener): () => void;
}

function createStore(initial: AppState): Store {
  let state = initial;
  const listeners = new Set<Listener>();

  return {
    get: () => state,
    set(patch) {
      state = { ...state, ...patch };
      for (const listener of listeners) listener(state);
    },
    subscribe(listener) {
      listeners.add(listener);
      listener(state);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

export const store = createStore({ route: "create", backend: "unknown" });
