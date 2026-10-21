/** Response shapes exchanged between the UI and the local backend. */

/** Health check the UI polls to tell whether the local backend is up. */
export interface HealthResponse {
  ok: true;
  service: "chess-dialogue-maker";
  uptimeSeconds: number;
}

/** Any failed API call: stable code plus an optional human-readable message. */
export interface ErrorResponse {
  error: string;
  message?: string;
}
