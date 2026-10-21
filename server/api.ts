import type { IncomingMessage, ServerResponse } from "node:http";

import type { ErrorResponse, HealthResponse } from "../shared/api.ts";
import type { LlmChatMessage, LlmChatRequest, LlmErrorBody } from "../shared/llm.ts";
import type { TranscriptionResponse } from "../shared/transcribe.ts";
import { chat, LlmError, models, providers } from "./llm.ts";
import { defaultLanguage, modelName, transcribe } from "./transcribe.ts";

const startedAt = Date.now();
const MAX_BODY_BYTES = 2_000_000;
const MAX_AUDIO_BYTES = 120_000_000;

/** A malformed request: the UI only needs the message, but the status must stay honest. */
class BadRequest extends Error {
  readonly title: string;
  readonly hint: string;
  readonly status: number;

  constructor(title: string, hint = "", status = 400) {
    super(title);
    this.title = title;
    this.hint = hint;
    this.status = status;
  }
}

export function sendJson(response: ServerResponse, status: number, body: unknown): void {
  const payload = JSON.stringify(body);
  response.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": Buffer.byteLength(payload),
    "cache-control": "no-store",
  });
  response.end(payload);
}

export function sendText(response: ServerResponse, status: number, text: string): void {
  response.writeHead(status, { "content-type": "text/plain; charset=utf-8" });
  response.end(text);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isRole(value: unknown): value is LlmChatMessage["role"] {
  return value === "system" || value === "user" || value === "assistant";
}

async function readJsonBody(request: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of request) {
    const buffer = typeof chunk === "string" ? Buffer.from(chunk, "utf8") : Buffer.from(chunk);
    size += buffer.length;
    if (size > MAX_BODY_BYTES) throw new BadRequest("The request is too large", "", 413);
    chunks.push(buffer);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8")) as unknown;
  } catch {
    throw new BadRequest("The request body is not valid JSON");
  }
}

async function readBinaryBody(request: IncomingMessage, limit: number): Promise<Buffer> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of request) {
    const buffer = typeof chunk === "string" ? Buffer.from(chunk, "utf8") : Buffer.from(chunk);
    size += buffer.length;
    if (size > limit) throw new BadRequest("The audio is too large", "", 413);
    chunks.push(buffer);
  }
  return Buffer.concat(chunks);
}

function chatRequest(value: unknown): LlmChatRequest {
  if (!isRecord(value)) throw new BadRequest("The request body must be a JSON object");

  const provider = value["provider"];
  const model = value["model"];
  const rawMessages = value["messages"];
  if (typeof provider !== "string" || provider === "") {
    throw new BadRequest('"provider" is required');
  }
  if (typeof model !== "string") throw new BadRequest('"model" must be a string');
  if (!Array.isArray(rawMessages)) throw new BadRequest('"messages" must be an array');

  const messages: LlmChatMessage[] = rawMessages.map((raw, index) => {
    if (!isRecord(raw)) throw new BadRequest(`messages[${index}] must be an object`);
    const role = raw["role"];
    const content = raw["content"];
    if (!isRole(role)) throw new BadRequest(`messages[${index}].role is invalid`);
    if (typeof content !== "string") throw new BadRequest(`messages[${index}].content must be a string`);
    return { role, content };
  });

  const schema = value["schema"];
  const temperature = value["temperature"];
  return {
    provider,
    model,
    messages,
    schema: isRecord(schema) ? schema : undefined,
    temperature: typeof temperature === "number" ? temperature : undefined,
  };
}

function fail(response: ServerResponse, error: unknown): void {
  if (error instanceof BadRequest) {
    const body: LlmErrorBody = { error: "llm_error", title: error.title, hint: error.hint };
    sendJson(response, error.status, body);
    return;
  }
  if (error instanceof LlmError) {
    const body: LlmErrorBody = { error: "llm_error", title: error.title, hint: error.hint };
    sendJson(response, 502, body);
    return;
  }
  // Anything else — a missing model file, a crashed pipeline — still has to reach the UI.
  console.error("request failed:", error);
  const hint = error instanceof Error ? error.message : String(error);
  const body: LlmErrorBody = { error: "llm_error", title: "The request failed", hint };
  sendJson(response, 500, body);
}

function methodNotAllowed(response: ServerResponse): void {
  sendJson(response, 405, { error: "method_not_allowed" } satisfies ErrorResponse);
}

/** Every `/api/*` request. */
export async function handleApi(
  request: IncomingMessage,
  response: ServerResponse,
  url: URL,
): Promise<void> {
  const method = request.method ?? "GET";

  if (url.pathname === "/api/health") {
    if (method !== "GET") return methodNotAllowed(response);
    const body: HealthResponse = {
      ok: true,
      service: "chess-dialogue-maker",
      uptimeSeconds: Math.round((Date.now() - startedAt) / 1000),
    };
    sendJson(response, 200, body);
    return;
  }

  if (url.pathname === "/api/llm/providers") {
    if (method !== "GET") return methodNotAllowed(response);
    sendJson(response, 200, { providers: providers() });
    return;
  }

  if (url.pathname === "/api/llm/models") {
    if (method !== "GET") return methodNotAllowed(response);
    const provider = url.searchParams.get("provider") ?? "";
    try {
      sendJson(response, 200, { models: await models(provider) });
    } catch (error) {
      // A missing model list is not fatal: the UI falls back to the suggested models.
      const title = error instanceof LlmError ? error.title : "could not load models";
      console.warn(`models: ${title}`);
      sendJson(response, 200, { models: [], error: title });
    }
    return;
  }

  if (url.pathname === "/api/llm/chat") {
    if (method !== "POST") return methodNotAllowed(response);
    try {
      const payload = chatRequest(await readJsonBody(request));
      sendJson(response, 200, { text: await chat(payload) });
    } catch (error) {
      fail(response, error);
    }
    return;
  }

  if (url.pathname === "/api/transcribe") {
    if (method === "GET") {
      sendJson(response, 200, { model: modelName(), language: defaultLanguage() });
      return;
    }
    if (method !== "POST") return methodNotAllowed(response);
    try {
      const requested = url.searchParams.get("language")?.trim() ?? "";
      const language = /^[a-z]{2,3}$/.test(requested) ? requested : defaultLanguage();
      const wav = await readBinaryBody(request, MAX_AUDIO_BYTES);
      if (wav.length === 0) throw new BadRequest("The request has no audio");
      const segments = await transcribe(wav, language);
      const body: TranscriptionResponse = { segments, language, model: modelName() };
      sendJson(response, 200, body);
    } catch (error) {
      fail(response, error);
    }
    return;
  }

  if (url.pathname.startsWith("/api/")) {
    sendJson(response, 404, { error: "not_found" } satisfies ErrorResponse);
    return;
  }
}
