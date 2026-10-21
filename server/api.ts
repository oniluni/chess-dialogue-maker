import { createReadStream } from "node:fs";
import type { IncomingMessage, ServerResponse } from "node:http";

import type { ErrorResponse, HealthResponse } from "../shared/api.ts";
import type { LlmChatMessage, LlmChatRequest, LlmErrorBody } from "../shared/llm.ts";
import type { FacesRequest } from "../shared/people.ts";
import type { DiarizationRequest, DiarizationResponse } from "../shared/speakers.ts";
import type { ProsodyRequest, ProsodyResponse, SoundsResponse } from "../shared/sounds.ts";
import type { TranscriptionResponse } from "../shared/transcribe.ts";
import { fileOf, removeJob, startDownload, statusOf } from "./download.ts";
import type { DownloadRequest } from "./download.ts";
import { chat, LlmError, models, providers } from "./llm.ts";
import { diarize } from "./speakers.ts";
import { analyzeFrames } from "./vision.ts";
import { SAMPLE_RATE, decodeWav } from "./wav.ts";
import { detectProsody } from "./prosody.ts";
import { deleteProject, listProjects, loadProject, saveProject } from "./projects.ts";
import { classify, SOUND_MODEL } from "./yamnet.ts";
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

async function readJsonBody(request: IncomingMessage, limit = MAX_BODY_BYTES): Promise<unknown> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of request) {
    const buffer = typeof chunk === "string" ? Buffer.from(chunk, "utf8") : Buffer.from(chunk);
    size += buffer.length;
    if (size > limit) throw new BadRequest("The request is too large", "", 413);
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

function secondsField(value: unknown, field: string): number | null {
  if (value === undefined || value === null) return null;
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    throw new BadRequest(`"${field}" must be a number of seconds`);
  }
  return value;
}

function downloadRequest(value: unknown): DownloadRequest {
  if (!isRecord(value)) throw new BadRequest("The request body must be a JSON object");
  const raw = value["url"];
  if (typeof raw !== "string" || raw.trim() === "") throw new BadRequest('"url" is required');

  const url = raw.trim();
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    throw new BadRequest("Paste a link that starts with http:// or https://");
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new BadRequest("Paste a link that starts with http:// or https://");
  }

  const start = secondsField(value["start"], "start");
  const end = secondsField(value["end"], "end");
  if (start !== null && end !== null && end <= start) {
    throw new BadRequest("The end of the fragment must be after its start");
  }
  return { url, start, end };
}

function speakersRequest(value: unknown): DiarizationRequest {
  if (!isRecord(value)) throw new BadRequest("The request body must be a JSON object");
  const wav = value["wav"];
  if (typeof wav !== "string" || wav === "") throw new BadRequest('"wav" is required');

  const raw = value["segments"];
  if (!Array.isArray(raw) || raw.length === 0) throw new BadRequest('"segments" must be a non-empty array');
  if (raw.length > 1000) throw new BadRequest("Too many segments");

  const segments = raw.map((item, index) => {
    if (!isRecord(item)) throw new BadRequest(`segments[${index}] must be an object`);
    const start = item["start"];
    const end = item["end"];
    if (typeof start !== "number" || typeof end !== "number" || !Number.isFinite(start) || !Number.isFinite(end)) {
      throw new BadRequest(`segments[${index}] needs numeric start and end`);
    }
    if (end <= start) throw new BadRequest(`segments[${index}]: end must be after start`);
    return { start, end };
  });
  return { wav, segments };
}

function prosodyRequest(value: unknown): ProsodyRequest {
  if (!isRecord(value)) throw new BadRequest("The request body must be a JSON object");
  const wav = value["wav"];
  if (typeof wav !== "string" || wav === "") throw new BadRequest('"wav" must hold base64 audio');
  const raw = value["segments"];
  if (!Array.isArray(raw) || raw.length === 0) throw new BadRequest('"segments" must be a non-empty array');
  if (raw.length > 500) throw new BadRequest("Too many segments");

  const segments = raw.map((item, index) => {
    if (!isRecord(item)) throw new BadRequest(`segments[${index}] must be an object`);
    const start = item["start"];
    const end = item["end"];
    const label = item["label"];
    if (typeof start !== "number" || typeof end !== "number" || end <= start) {
      throw new BadRequest(`segments[${index}]: bad times`);
    }
    if (label !== null && label !== "A" && label !== "B") throw new BadRequest(`segments[${index}]: bad label`);
    const voice: "A" | "B" | null = label === "A" ? "A" : label === "B" ? "B" : null;
    return { start, end, label: voice };
  });
  return { wav, segments };
}
function facesRequest(value: unknown): FacesRequest {
  if (!isRecord(value)) throw new BadRequest("The request body must be a JSON object");
  const raw = value["frames"];
  if (!Array.isArray(raw) || raw.length === 0) throw new BadRequest('"frames" must be a non-empty array');
  if (raw.length > 80) throw new BadRequest("Too many frames");

  const frames = raw.map((item, index) => {
    if (!isRecord(item)) throw new BadRequest(`frames[${index}] must be an object`);
    const time = item["time"];
    const width = item["width"];
    const height = item["height"];
    const rgb = item["rgb"];
    if (typeof time !== "number" || !Number.isFinite(time)) throw new BadRequest(`frames[${index}]: bad time`);
    if (typeof width !== "number" || typeof height !== "number" || width <= 0 || height <= 0) {
      throw new BadRequest(`frames[${index}]: bad size`);
    }
    if (typeof rgb !== "string" || rgb === "") throw new BadRequest(`frames[${index}]: bad pixels`);
    return { time, width: Math.round(width), height: Math.round(height), rgb };
  });
  return { frames };
}

function mediaType(name: string): string {
  if (name.endsWith(".mp4") || name.endsWith(".m4v")) return "video/mp4";
  if (name.endsWith(".webm")) return "video/webm";
  if (name.endsWith(".mkv")) return "video/x-matroska";
  if (name.endsWith(".mov")) return "video/quicktime";
  return "application/octet-stream";
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
  // Anything else ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬ÂÃƒâ€šÃ‚Â a missing model file, a crashed pipeline ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬ÂÃƒâ€šÃ‚Â still has to reach the UI.
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

  if (url.pathname === "/api/prosody") {
    if (method !== "POST") return methodNotAllowed(response);
    try {
      const payload = prosodyRequest(await readJsonBody(request, 40_000_000));
      const wav = Buffer.from(payload.wav, "base64");
      if (wav.length === 0) throw new BadRequest("The request has no audio");
      const pcm = decodeWav(wav);
      const result = detectProsody(pcm, payload.segments);
      const body: ProsodyResponse = { events: result.events, samples: result.samples };
      sendJson(response, 200, body);
    } catch (error) {
      fail(response, error);
    }
    return;
  }

  if (url.pathname === "/api/projects") {
    try {
      if (method === "GET") {
        const id = url.searchParams.get("id");
        if (id === null) {
          sendJson(response, 200, { projects: await listProjects() });
        } else {
          const project = await loadProject(id);
          if (project === null) throw new BadRequest("No such project");
          sendJson(response, 200, project);
        }
        return;
      }
      if (method === "POST") {
        const body = await readJsonBody(request);
        if (!isRecord(body)) throw new BadRequest("The request body must be a JSON object");
        const name = typeof body["name"] === "string" ? body["name"] : "";
        const summary = await saveProject(name, body["data"]);
        sendJson(response, 201, summary);
        return;
      }
      if (method === "DELETE") {
        const id = url.searchParams.get("id");
        if (id === null) throw new BadRequest("No project id");
        sendJson(response, 200, { removed: await deleteProject(id) });
        return;
      }
      return methodNotAllowed(response);
    } catch (error) {
      fail(response, error);
    }
    return;
  }
  if (url.pathname === "/api/sounds") {
    if (method !== "POST") return methodNotAllowed(response);
    try {
      const wav = await readBinaryBody(request, MAX_AUDIO_BYTES);
      if (wav.length === 0) throw new BadRequest("The request has no audio");
      const pcm = decodeWav(wav);
      const duration = pcm.length / SAMPLE_RATE;
      const body: SoundsResponse = { events: await classify(pcm, duration), model: SOUND_MODEL };
      sendJson(response, 200, body);
    } catch (error) {
      fail(response, error);
    }
    return;
  }

  if (url.pathname === "/api/download") {
    if (method !== "POST") return methodNotAllowed(response);
    try {
      const payload = downloadRequest(await readJsonBody(request));
      sendJson(response, 201, { id: startDownload(payload) });
    } catch (error) {
      fail(response, error);
    }
    return;
  }

  const downloadPath = /^\/api\/download\/([0-9a-f-]{36})(\/file)?$/.exec(url.pathname);
  if (downloadPath !== null) {
    const id = downloadPath[1] ?? "";
    if (downloadPath[2] !== undefined) {
      if (method !== "GET") return methodNotAllowed(response);
      const file = fileOf(id);
      if (file === null) {
        sendJson(response, 404, { error: "not_found" } satisfies ErrorResponse);
        return;
      }
      response.writeHead(200, {
        "content-type": mediaType(file.name),
        "content-length": file.size,
        "cache-control": "no-store",
        "content-disposition": `inline; filename="${file.name}"`,
      });
      createReadStream(file.path).pipe(response);
      return;
    }
    if (method === "DELETE") {
      removeJob(id);
      sendJson(response, 200, { ok: true });
      return;
    }
    if (method !== "GET") return methodNotAllowed(response);
    const status = statusOf(id);
    if (status === null) {
      sendJson(response, 404, { error: "not_found" } satisfies ErrorResponse);
      return;
    }
    sendJson(response, 200, status);
    return;
  }
  if (url.pathname === "/api/speakers") {
    if (method !== "POST") return methodNotAllowed(response);
    try {
      const payload = speakersRequest(await readJsonBody(request, MAX_AUDIO_BYTES));
      const wav = Buffer.from(payload.wav, "base64");
      if (wav.length === 0) throw new BadRequest("The request has no audio");
      const result: DiarizationResponse = await diarize(decodeWav(wav), payload.segments);
      sendJson(response, 200, result);
    } catch (error) {
      fail(response, error);
    }
    return;
  }

  if (url.pathname === "/api/faces") {
    if (method !== "POST") return methodNotAllowed(response);
    try {
      const payload = facesRequest(await readJsonBody(request, MAX_AUDIO_BYTES));
      const frames = payload.frames.map((frame) => ({
        time: frame.time,
        width: frame.width,
        height: frame.height,
        rgb: Uint8Array.from(Buffer.from(frame.rgb, "base64")),
      }));
      sendJson(response, 200, await analyzeFrames(frames));
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






