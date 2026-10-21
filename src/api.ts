import type { HealthResponse } from "../shared/api.ts";
import { store } from "./state.ts";
import type { BackendStatus } from "./state.ts";

const HEALTH_PATH = "/api/health";
const HEALTH_INTERVAL_MS = 15_000;
const HEALTH_TIMEOUT_MS = 3_000;

function isHealthResponse(value: unknown): value is HealthResponse {
  if (typeof value !== "object" || value === null) return false;
  if (!("ok" in value) || !("service" in value)) return false;
  return value.ok === true && value.service === "chess-dialogue-maker";
}

async function fetchHealth(): Promise<HealthResponse> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), HEALTH_TIMEOUT_MS);
  try {
    const response = await fetch(HEALTH_PATH, { signal: controller.signal });
    if (!response.ok) throw new Error(`${HEALTH_PATH}: HTTP ${response.status}`);
    const payload: unknown = await response.json();
    if (!isHealthResponse(payload)) throw new Error(`${HEALTH_PATH}: unexpected payload`);
    return payload;
  } finally {
    clearTimeout(timer);
  }
}

let reported: BackendStatus = "unknown";

function report(status: BackendStatus, error?: unknown): void {
  if (status === reported) return;
  reported = status;
  if (status === "offline") console.warn("backend unreachable:", error);
  store.set({ backend: status });
}

/** Polls the local backend so the shell can show whether it is up. */
export function startHealthPolling(): void {
  const check = async (): Promise<void> => {
    try {
      await fetchHealth();
      report("online");
    } catch (error) {
      report("offline", error);
    }
  };

  void check();
  setInterval(() => void check(), HEALTH_INTERVAL_MS);
}
