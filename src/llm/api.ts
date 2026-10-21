import type { LlmChatRequest, LlmModelInfo, LlmProviderInfo } from "../../shared/llm.ts";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function errorMessage(response: Response): Promise<string> {
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

export async function listProviders(): Promise<LlmProviderInfo[]> {
  const response = await fetch("/api/llm/providers");
  if (!response.ok) throw new Error(await errorMessage(response));

  const data: unknown = await response.json();
  const list = isRecord(data) && Array.isArray(data["providers"]) ? data["providers"] : null;
  if (list === null) throw new Error("unexpected provider list");

  return list.flatMap((raw): LlmProviderInfo[] => {
    if (!isRecord(raw) || typeof raw["id"] !== "string" || typeof raw["label"] !== "string") return [];
    return [
      {
        id: raw["id"],
        label: raw["label"],
        local: raw["local"] === true,
        needsKey: raw["needsKey"] === true,
        configured: raw["configured"] === true,
        defaultModel: typeof raw["defaultModel"] === "string" ? raw["defaultModel"] : "",
        suggested: Array.isArray(raw["suggested"])
          ? raw["suggested"].filter((item): item is string => typeof item === "string")
          : [],
      },
    ];
  });
}

export async function listModels(provider: string): Promise<{ models: LlmModelInfo[]; error?: string }> {
  const response = await fetch(`/api/llm/models?provider=${encodeURIComponent(provider)}`);
  if (!response.ok) throw new Error(await errorMessage(response));

  const data: unknown = await response.json();
  const list = isRecord(data) && Array.isArray(data["models"]) ? data["models"] : null;
  const models = (list ?? []).flatMap((raw): LlmModelInfo[] => {
    if (!isRecord(raw) || typeof raw["id"] !== "string") return [];
    return [{ id: raw["id"], label: typeof raw["label"] === "string" ? raw["label"] : raw["id"] }];
  });
  const error = isRecord(data) && typeof data["error"] === "string" ? data["error"] : undefined;
  return { models, error };
}

export async function chat(request: LlmChatRequest): Promise<string> {
  const response = await fetch("/api/llm/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!response.ok) throw new Error(await errorMessage(response));

  const data: unknown = await response.json();
  const text = isRecord(data) ? data["text"] : undefined;
  if (typeof text !== "string" || text.trim() === "") throw new Error("the provider returned an empty answer");
  return text;
}
