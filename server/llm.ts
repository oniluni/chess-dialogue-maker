import type {
  LlmChatRequest,
  LlmModelInfo,
  LlmProviderInfo,
} from "../shared/llm.ts";

export class LlmError extends Error {
  readonly title: string;
  readonly hint: string;
  /** HTTP status of the provider answer, when there was one. */
  readonly status: number;

  constructor(title: string, hint = "", status = 502) {
    super(title);
    this.title = title;
    this.hint = hint;
    this.status = status;
  }
}

type Kind = "ollama" | "openai" | "gemini" | "anthropic";

interface Spec {
  id: string;
  label: string;
  kind: Kind;
  local: boolean;
  baseUrl: string;
  chatUrl?: string;
  keyEnv?: string;
  keyOptional?: boolean;
  defaultModel: string;
  suggested: string[];
  extraHeaders?: Record<string, string>;
}

function env(name: string, fallback = ""): string {
  return process.env[name]?.trim() || fallback;
}

function specs(): Spec[] {
  return [
    {
      id: "ollama",
      label: "Ollama · local",
      kind: "ollama",
      local: true,
      baseUrl: env("OLLAMA_URL", "http://127.0.0.1:11434"),
      defaultModel: env("OLLAMA_MODEL", "qwen3.5:9b"),
      suggested: ["qwen3.5:9b", "gemma4:12b", "gemma4:26b", "qwen3.8:27b", "qwen3.5:4b"],
    },
    {
      id: "custom",
      label: "Local OpenAI-compatible",
      kind: "openai",
      local: true,
      baseUrl: env("CUSTOM_LLM_URL", "http://127.0.0.1:1234/v1"),
      keyEnv: "CUSTOM_LLM_KEY",
      keyOptional: true,
      defaultModel: env("CUSTOM_LLM_MODEL", ""),
      suggested: [],
    },
    {
      id: "pollinations",
      label: "Pollinations · no key",
      kind: "openai",
      local: false,
      baseUrl: "https://text.pollinations.ai",
      chatUrl: "https://text.pollinations.ai/openai",
      defaultModel: "openai-fast",
      suggested: ["openai-fast"],
    },
    {
      id: "groq",
      label: "Groq",
      kind: "openai",
      local: false,
      baseUrl: "https://api.groq.com/openai/v1",
      keyEnv: "GROQ_API_KEY",
      defaultModel: "llama-3.3-70b-versatile",
      suggested: ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "openai/gpt-oss-20b"],
    },
    {
      id: "openrouter",
      label: "OpenRouter",
      kind: "openai",
      local: false,
      baseUrl: "https://openrouter.ai/api/v1",
      keyEnv: "OPENROUTER_API_KEY",
      defaultModel: "google/gemma-4-31b-it:free",
      suggested: ["google/gemma-4-31b-it:free", "google/gemma-4-26b-a4b-it:free"],
      extraHeaders: {
        "HTTP-Referer": "https://github.com/olimjumm/chess-dialogue-maker",
        "X-Title": "Chess Dialogue Maker",
      },
    },
    {
      id: "mistral",
      label: "Mistral AI",
      kind: "openai",
      local: false,
      baseUrl: "https://api.mistral.ai/v1",
      keyEnv: "MISTRAL_API_KEY",
      defaultModel: "mistral-large-latest",
      suggested: ["mistral-large-latest", "mistral-medium-latest", "mistral-small-latest"],
    },
    {
      id: "gemini",
      label: "Gemini",
      kind: "gemini",
      local: false,
      baseUrl: "https://generativelanguage.googleapis.com/v1beta",
      keyEnv: "GEMINI_API_KEY",
      defaultModel: env("GEMINI_MODEL", "gemini-3.8-flash"),
      suggested: ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"],
    },
    {
      id: "anthropic",
      label: "Claude · Anthropic API",
      kind: "anthropic",
      local: false,
      baseUrl: "https://api.anthropic.com/v1",
      keyEnv: "ANTHROPIC_API_KEY",
      defaultModel: "claude-sonnet-5-5",
      suggested: ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"],
    },
  ];
}

function specById(id: string): Spec {
  const spec = specs().find((candidate) => candidate.id === id);
  if (spec === undefined) throw new LlmError(`Unknown provider "${id}"`, "Pick a provider from the list.");
  return spec;
}

function keyOf(spec: Spec): string {
  return spec.keyEnv === undefined ? "" : env(spec.keyEnv);
}

function configured(spec: Spec): boolean {
  if (spec.keyEnv === undefined || spec.keyOptional === true) return true;
  return keyOf(spec) !== "";
}

export function providers(): LlmProviderInfo[] {
  return specs().map((spec) => ({
    id: spec.id,
    label: spec.label,
    local: spec.local,
    needsKey: spec.keyEnv !== undefined && spec.keyOptional !== true,
    configured: configured(spec),
    defaultModel: spec.defaultModel,
    suggested: spec.suggested,
  }));
}

// ---------------------------------------------------------------- JSON helpers
function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function summarise(raw: string): string {
  const clean = raw.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
  return clean.slice(0, 300);
}

/** Turns a provider answer into a title and a hint the person can act on. */
function explain(status: number, detail: string): { title: string; hint: string } | null {
  const lower = detail.toLowerCase();
  if (status === 401 || status === 403) {
    return {
      title: "The provider rejected the key",
      hint: "Check the key in .env, then restart the backend.",
    };
  }
  if (status === 404) {
    return {
      title: "The model is not available",
      hint: "Pick another model from the list, or check the model id.",
    };
  }
  if (status === 429) {
    return {
      title: "The provider is rate limiting",
      hint: "Wait a minute, or switch to another provider or model.",
    };
  }
  if (status === 413 || lower.includes("context") || lower.includes("too long") || lower.includes("too many tokens")) {
    return {
      title: "The request is too long for this model",
      hint: "Shorten the clip or split it, then run the analysis again.",
    };
  }
  if (status >= 500) {
    return {
      title: "The provider is having trouble",
      hint: "Retry in a little while, or switch to another provider.",
    };
  }
  return null;
}

const MAX_RESPONSE_BYTES = 2_000_000;

/** Reads a provider answer without letting a broken or hostile endpoint fill memory. */
async function responseText(response: Response, label: string): Promise<string> {
  const declared = Number(response.headers.get("content-length") ?? "0");
  if (Number.isFinite(declared) && declared > MAX_RESPONSE_BYTES) {
    await response.body?.cancel();
    throw new LlmError(`${label} answered with too much data`);
  }
  if (response.body === null) return "";
  const reader = response.body.getReader();
  const chunks: Buffer[] = [];
  let size = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_RESPONSE_BYTES) {
        await reader.cancel();
        throw new LlmError(`${label} answered with too much data`);
      }
      chunks.push(Buffer.from(value));
    }
  } finally {
    reader.releaseLock();
  }
  return Buffer.concat(chunks).toString("utf8");
}

async function readJson(response: Response, label: string): Promise<unknown> {
  const raw = await responseText(response, label);
  if (!response.ok) {
    const known = explain(response.status, raw);
    throw new LlmError(known?.title ?? `${label} answered ${response.status}`, known?.hint ?? summarise(raw), response.status);
  }
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    throw new LlmError(`${label} returned no JSON`, summarise(raw));
  }
}

async function requestJson(
  method: "GET" | "POST",
  url: string,
  label: string,
  body?: unknown,
  headers: Record<string, string> = {},
): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers: { "content-type": "application/json", ...headers },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(180_000),
    });
  } catch (error) {
    throw new LlmError(
      `${label} is unreachable`,
      `Check the network and the base URL for ${label}. ${error instanceof Error ? error.message : String(error)}`,
      503,
    );
  }
  return readJson(response, label);
}

// ---------------------------------------------------------------- chat
export async function chat(request: LlmChatRequest): Promise<string> {
  const spec = specById(request.provider);
  const key = keyOf(spec);
  if (!configured(spec)) {
    throw new LlmError(
      `No API key for ${spec.label}`,
      `Add ${spec.keyEnv ?? "the key"} to .env and restart the backend.`,
    );
  }

  switch (spec.kind) {
    case "ollama":
      return ollamaChat(spec, request);
    case "openai":
      return openAiChat(spec, request, key);
    case "gemini":
      return geminiChat(spec, request, key);
    case "anthropic":
      return anthropicChat(spec, request, key);
  }
}

async function ollamaChat(spec: Spec, request: LlmChatRequest): Promise<string> {
  const data = await requestJson("POST", `${spec.baseUrl.replace(/\/$/, "")}/api/chat`, spec.label, {
    model: request.model || spec.defaultModel,
    messages: request.messages,
    stream: false,
    format: request.schema ?? "json",
    options: { temperature: request.temperature ?? 0.4 },
  });
  const content = str(record(record(data)?.["message"])?.["content"]);
  if (content === "") throw new LlmError(`${spec.label} returned an empty answer`);
  return content;
}

async function openAiChat(spec: Spec, request: LlmChatRequest, key: string): Promise<string> {
  const url = spec.chatUrl ?? `${spec.baseUrl.replace(/\/$/, "")}/chat/completions`;
  const body: Record<string, unknown> = {
    model: request.model || spec.defaultModel,
    messages: request.messages,
    temperature: request.temperature ?? 0.4,
  };
  if (request.schema !== undefined) body["response_format"] = { type: "json_object" };

  const headers: Record<string, string> = { ...spec.extraHeaders };
  if (key !== "") headers["authorization"] = `Bearer ${key}`;

  const data = await requestJson("POST", url, spec.label, body, headers);
  const choice = list(record(data)?.["choices"])[0];
  const content = str(record(record(choice)?.["message"])?.["content"]);
  if (content === "") throw new LlmError(`${spec.label} returned an empty answer`);
  return content;
}

async function geminiChat(spec: Spec, request: LlmChatRequest, key: string): Promise<string> {
  const system = request.messages
    .filter((message) => message.role === "system")
    .map((message) => message.content)
    .join("\n\n");
  const contents = request.messages
    .filter((message) => message.role !== "system")
    .map((message) => ({
      role: message.role === "assistant" ? "model" : "user",
      parts: [{ text: message.content }],
    }));

  const model = (request.model || spec.defaultModel).replace(/^models\//, "");
  const url = `${spec.baseUrl}/models/${encodeURIComponent(model)}:generateContent?key=${encodeURIComponent(key)}`;
  const body: Record<string, unknown> = {
    contents,
    generationConfig: { temperature: request.temperature ?? 0.4, responseMimeType: "application/json" },
  };
  if (system !== "") body["systemInstruction"] = { parts: [{ text: system }] };

  const data = await requestJson("POST", url, spec.label, body);
  const candidate = list(record(data)?.["candidates"])[0];
  const parts = list(record(record(candidate)?.["content"])?.["parts"]);
  const content = parts.map((part) => str(record(part)?.["text"])).join("");
  if (content === "") throw new LlmError(`${spec.label} returned an empty answer`);
  return content;
}

async function anthropicChat(spec: Spec, request: LlmChatRequest, key: string): Promise<string> {
  const system = request.messages
    .filter((message) => message.role === "system")
    .map((message) => message.content)
    .join("\n\n");
  const messages = request.messages
    .filter((message) => message.role !== "system")
    .map((message) => ({ role: message.role, content: message.content }));

  const body: Record<string, unknown> = {
    model: request.model || spec.defaultModel,
    max_tokens: 4096,
    temperature: request.temperature ?? 0.4,
    messages,
  };
  if (system !== "") body["system"] = system;

  const data = await requestJson("POST", `${spec.baseUrl}/messages`, spec.label, body, {
    "x-api-key": key,
    "anthropic-version": "2023-06-01",
  });
  const content = list(record(data)?.["content"])
    .filter((block) => record(block)?.["type"] === "text")
    .map((block) => str(record(block)?.["text"]))
    .join("");
  if (content === "") throw new LlmError(`${spec.label} returned an empty answer`);
  return content;
}

// ---------------------------------------------------------------- models
export async function models(providerId: string): Promise<LlmModelInfo[]> {
  const spec = specById(providerId);
  const key = keyOf(spec);
  if (!configured(spec)) return [];

  if (spec.kind === "ollama") {
    const data = await requestJson("GET", `${spec.baseUrl.replace(/\/$/, "")}/api/tags`, spec.label);
    return list(record(data)?.["models"])
      .map((model) => str(record(model)?.["name"]))
      .filter((name) => name !== "")
      .map((name) => ({ id: name, label: name }));
  }

  if (spec.kind === "gemini") {
    const data = await requestJson("GET", `${spec.baseUrl}/models?key=${encodeURIComponent(key)}`, spec.label);
    return list(record(data)?.["models"])
      .filter((model) => list(record(model)?.["supportedGenerationMethods"]).includes("generateContent"))
      .map((model) => str(record(model)?.["name"]).replace(/^models\//, ""))
      .filter((name) => name !== "")
      .map((name) => ({ id: name, label: name }));
  }

  if (spec.kind === "anthropic") {
    const data = await requestJson("GET", `${spec.baseUrl}/models`, spec.label, undefined, {
      "x-api-key": key,
      "anthropic-version": "2023-06-01",
    });
    return list(record(data)?.["data"])
      .map((model) => str(record(model)?.["id"]))
      .filter((id) => id !== "")
      .map((id) => ({ id, label: id }));
  }

  const headers: Record<string, string> = { ...spec.extraHeaders };
  if (key !== "") headers["authorization"] = `Bearer ${key}`;
  const data = await requestJson("GET", `${spec.baseUrl.replace(/\/$/, "")}/models`, spec.label, undefined, headers);
  return list(record(data)?.["data"])
    .map((model) => str(record(model)?.["id"]))
    .filter((id) => id !== "")
    .map((id) => ({ id, label: id }))
    .sort((left, right) => left.id.localeCompare(right.id));
}
