export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** The server answers failures as `{title, hint}`; fall back to the status line. */
export async function errorMessage(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (isRecord(body) && typeof body["title"] === "string") {
      const hint = typeof body["hint"] === "string" ? body["hint"] : "";
      return hint === "" ? body["title"] : `${body["title"]} — ${hint}`;
    }
  } catch {
    // not JSON: fall through to the status line
  }
  return `request failed: HTTP ${response.status}`;
}
