import { createReadStream, existsSync } from "node:fs";
import { stat } from "node:fs/promises";
import type { IncomingHttpHeaders, IncomingMessage, ServerResponse } from "node:http";

import type { ErrorResponse, HealthResponse } from "../shared/api.ts";
import type { LlmChatMessage, LlmChatRequest, LlmErrorBody } from "../shared/llm.ts";
import type { FacesRequest } from "../shared/people.ts";
import type { DiarizationRequest, DiarizationResponse } from "../shared/speakers.ts";
import type { ProsodyRequest, ProsodyResponse, SoundsResponse } from "../shared/sounds.ts";
import type { TranscriptionResponse } from "../shared/transcribe.ts";
import {
  DownloadLimitError,
  fileOf,
  releaseFile,
  removeJob,
  retainFile,
  startDownload,
  statusOf,
} from "./download.ts";
import type { DownloadRequest } from "./download.ts";
import { chat, LlmError, models, providers } from "./llm.ts";
import { diarize } from "./speakers.ts";
import { analyzeFrames } from "./vision.ts";
import { SAMPLE_RATE, decodeWav } from "./wav.ts";
import { detectProsody } from "./prosody.ts";
import { cookiesFile, saveCookies } from "./download-cache.ts";
import { updateYtdlp, ytdlpVersion } from "./download.ts";
import { search } from "./search.ts";
import { deleteProject, listProjects, loadProject, saveProject } from "./projects.ts";
import { classify, SOUND_MODEL } from "./yamnet.ts";
import { defaultLanguage, modelName, transcribe } from "./transcribe.ts";

const startedAt = Date.now();
const MAX_BODY_BYTES = 2_000_000;
const MAX_AUDIO_BYTES = 120_000_000;
const MAX_FACES_BODY_BYTES = 64_000_000;
const MAX_FRAME_SIDE = 2048;
const MAX_FRAME_PIXELS = 1_000_000;
const MAX_TOTAL_PIXELS = 12_000_000;

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
  if (response.headersSent || response.writableEnded || response.destroyed) return;
  const payload = JSON.stringify(body);
  response.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": Buffer.byteLength(payload),
    "cache-control": "no-store",
  });
  response.end(payload);
}

export function sendText(response: ServerResponse, status: number, text: string): void {
  if (response.headersSent || response.writableEnded || response.destroyed) return;
  response.writeHead(status, { "content-type": "text/plain; charset=utf-8" });
  response.end(text);
}

const LOOPBACK = new Set(["127.0.0.1", "localhost", "[::1]", "::1", "0:0:0:0:0:0:0:1"]);

function isLoopbackUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return (url.protocol === "http:" || url.protocol === "https:") && LOOPBACK.has(url.hostname);
  } catch {
    return false;
  }
}

/**
 * The API is for the local app only. A browser page on another origin (including one reached
 * through DNS rebinding) must not be able to drive it, but curl and the project's tests send no
 * Origin and no fetch metadata, so they still work.
 */
export function isTrustedApiRequest(headers: IncomingHttpHeaders): boolean {
  const host = headers.host;
  if (host !== undefined && !isLoopbackUrl(`http://${host}`)) return false;

  const origin = headers.origin;
  if (origin !== undefined && !isLoopbackUrl(origin)) return false;

  const site = headers["sec-fetch-site"];
  if (site !== undefined && site !== "same-origin" && site !== "none") return false;
  return true;
}

function contentType(request: IncomingMessage): string {
  const header = request.headers["content-type"];
  const raw = Array.isArray(header) ? (header[0] ?? "") : (header ?? "");
  return raw.split(";")[0]?.trim().toLowerCase() ?? "";
}

function requireJson(request: IncomingMessage): void {
  if (contentType(request) !== "application/json") {
    throw new BadRequest("Expected an application/json body", "", 415);
  }
}

const WAV_TYPES = new Set(["audio/wav", "audio/x-wav", "audio/wave", "application/octet-stream"]);

function requireWav(request: IncomingMessage): void {
  if (!WAV_TYPES.has(contentType(request))) {
    throw new BadRequest("Expected a WAV body (content-type: audio/wav)", "", 415);
  }
}

/** Standard base64 only; `Buffer.from` silently skips other characters. */
function decodeBase64(value: string, expectedLength: number | null, label: string): Buffer {
  const compact = value.trim();
  if (compact === "" || compact.length % 4 !== 0 || !/^[A-Za-z0-9+/]+={0,2}$/.test(compact)) {
    throw new BadRequest(`${label} is not valid base64`);
  }
  const decoded = Buffer.from(compact, "base64");
  if (expectedLength !== null && decoded.length !== expectedLength) {
    throw new BadRequest(`${label} must decode to ${expectedLength} bytes, got ${decoded.length}`);
  }
  return decoded;
}

function decodeAudioBase64(value: string, label: string): Buffer {
  const decoded = decodeBase64(value, null, label);
  if (decoded.length === 0) throw new BadRequest("The request has no audio");
  if (decoded.length > MAX_AUDIO_BYTES) throw new BadRequest("The audio is too large", "", 413);
  return decoded;
}

/** WAV parsing failures are client errors, not a crashed pipeline. */
function decodeAudio(wav: Buffer): Float32Array {
  try {
    return decodeWav(wav);
  } catch (error) {
    const hint = error instanceof Error ? error.message : String(error);
    throw new BadRequest("The audio file is not usable", hint);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isRole(value: unknown): value is LlmChatMessage["role"] {
  return value === "system" || value === "user" || value === "assistant";
}

async function readJsonBody(request: IncomingMessage, limit = MAX_BODY_BYTES): Promise<unknown> {
  const declared = Number(request.headers["content-length"] ?? "0");
  if (Number.isFinite(declared) && declared > limit) {
    throw new BadRequest("The request is too large", "", 413);
  }
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
  const declared = Number(request.headers["content-length"] ?? "0");
  if (Number.isFinite(declared) && declared > limit) {
    throw new BadRequest("The audio is too large", "", 413);
  }
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
  if (temperature !== undefined && (typeof temperature !== "number" || !Number.isFinite(temperature) || temperature < 0 || temperature > 2)) {
    throw new BadRequest('"temperature" must be a number from 0 to 2');
  }
  return {
    provider,
    model,
    messages,
    schema: isRecord(schema) ? schema : undefined,
    temperature,
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
    if (
      typeof start !== "number" ||
      typeof end !== "number" ||
      !Number.isFinite(start) ||
      !Number.isFinite(end) ||
      start < 0 ||
      end < 0
    ) {
      throw new BadRequest(`segments[${index}] needs non-negative numeric start and end`);
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
    if (
      typeof start !== "number" ||
      typeof end !== "number" ||
      !Number.isFinite(start) ||
      !Number.isFinite(end) ||
      start < 0 ||
      end <= start
    ) {
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

  let totalPixels = 0;
  const frames = raw.map((item, index) => {
    if (!isRecord(item)) throw new BadRequest(`frames[${index}] must be an object`);
    const time = item["time"];
    const width = item["width"];
    const height = item["height"];
    const rgb = item["rgb"];
    if (typeof time !== "number" || !Number.isFinite(time) || time < 0) {
      throw new BadRequest(`frames[${index}]: bad time`);
    }
    if (typeof width !== "number" || typeof height !== "number" || !Number.isFinite(width) || !Number.isFinite(height)) {
      throw new BadRequest(`frames[${index}]: bad size`);
    }
    const roundedWidth = Math.round(width);
    const roundedHeight = Math.round(height);
    if (
      roundedWidth < 1 ||
      roundedHeight < 1 ||
      roundedWidth > MAX_FRAME_SIDE ||
      roundedHeight > MAX_FRAME_SIDE
    ) {
      throw new BadRequest(`frames[${index}]: width and height must be from 1 to ${MAX_FRAME_SIDE}`);
    }
    const pixels = roundedWidth * roundedHeight;
    if (pixels > MAX_FRAME_PIXELS) throw new BadRequest(`frames[${index}]: the frame is too large`);
    totalPixels += pixels;
    if (totalPixels > MAX_TOTAL_PIXELS) throw new BadRequest("The frames are too large together");
    if (typeof rgb !== "string" || rgb === "") throw new BadRequest(`frames[${index}]: bad pixels`);
    const maxBase64 = pixels * 4;
    if (rgb.length > maxBase64) throw new BadRequest(`frames[${index}]: too many pixels for its size`);
    return { time, width: roundedWidth, height: roundedHeight, rgb };
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

/** Streams a finished download, or answers 404 before any header is written. */
async function serveDownloadFile(response: ServerResponse, id: string): Promise<void> {
  const file = fileOf(id);
  if (file === null) {
    sendJson(response, 404, { error: "not_found" } satisfies ErrorResponse);
    return;
  }

  let info: { isFile: () => boolean; size: number };
  try {
    info = await stat(file.path);
  } catch {
    sendJson(response, 404, { error: "not_found" } satisfies ErrorResponse);
    return;
  }
  if (!info.isFile() || !retainFile(id)) {
    sendJson(response, 404, { error: "not_found" } satisfies ErrorResponse);
    return;
  }

  let released = false;
  const release = (): void => {
    if (released) return;
    released = true;
    releaseFile(id);
  };

  try {
    response.writeHead(200, {
      "content-type": mediaType(file.name),
      "content-length": info.size,
      "cache-control": "no-store",
      "content-disposition": `inline; filename="${file.name.replace(/[\\"\r\n]/g, "_")}"`,
    });
  } catch (error) {
    release();
    throw error;
  }

  const stream = createReadStream(file.path);
  stream.on("close", release);
  stream.on("error", (error) => {
    release();
    console.error("download file stream failed:", error);
    if (!response.headersSent) {
      sendJson(response, 500, { error: "internal_error" } satisfies ErrorResponse);
      return;
    }
    response.destroy();
  });
  response.on("close", release);
  stream.pipe(response);
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

  if (!isTrustedApiRequest(request.headers)) {
    sendJson(response, 403, { error: "forbidden" } satisfies ErrorResponse);
    return;
  }

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
      requireJson(request);
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
      requireWav(request);
      const requested = url.searchParams.get("language")?.trim() ?? "";
      const language = /^[a-z]{2,3}$/.test(requested) ? requested : defaultLanguage();
      const wav = await readBinaryBody(request, MAX_AUDIO_BYTES);
      if (wav.length === 0) throw new BadRequest("The request has no audio");
      const audio = decodeAudio(wav);
      if (audio.length < SAMPLE_RATE * 0.2) throw new BadRequest("The clip is too short to transcribe");
      const segments = await transcribe(audio, language);
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
      requireJson(request);
      const payload = prosodyRequest(await readJsonBody(request, 40_000_000));
      const wav = decodeAudioBase64(payload.wav, '"wav"');
      const pcm = decodeAudio(wav);
      const result = detectProsody(pcm, payload.segments);
      const body: ProsodyResponse = { events: result.events, samples: result.samples };
      sendJson(response, 200, body);
    } catch (error) {
      fail(response, error);
    }
    return;
  }


  if (url.pathname === "/api/search") {
    if (method !== "GET") return methodNotAllowed(response);
    try {
      const query = url.searchParams.get("q") ?? "";
      if (query.length > 200) throw new BadRequest("The search query is too long");
      sendJson(response, 200, { hits: await search(query) });
    } catch (error) {
      fail(response, error);
    }
    return;
  }

  if (url.pathname === "/api/download/info") {
    if (method !== "GET") return methodNotAllowed(response);
    try {
      sendJson(response, 200, { version: await ytdlpVersion(), cookies: existsSync(cookiesFile()) });
    } catch (error) {
      fail(response, error);
    }
    return;
  }

  if (url.pathname === "/api/download/cookies") {
    if (method !== "POST") return methodNotAllowed(response);
    try {
      requireJson(request);
      const body = await readJsonBody(request);
      if (!isRecord(body) || typeof body["text"] !== "string") throw new BadRequest('"text" must hold the cookies');
      await saveCookies(body["text"]);
      sendJson(response, 200, { saved: true });
    } catch (error) {
      fail(response, error);
    }
    return;
  }

  if (url.pathname === "/api/download/update") {
    if (method !== "POST") return methodNotAllowed(response);
    try {
      sendJson(response, 200, { output: await updateYtdlp() });
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
          if (project === null) {
            sendJson(response, 404, { error: "not_found" } satisfies ErrorResponse);
            return;
          }
          sendJson(response, 200, project);
        }
        return;
      }
      if (method === "POST") {
        requireJson(request);
        const body = await readJsonBody(request);
        if (!isRecord(body)) throw new BadRequest("The request body must be a JSON object");
        const name = body["name"];
        if (name !== undefined && typeof name !== "string") throw new BadRequest('"name" must be a string');
        const data = body["data"];
        if (!isRecord(data) || data["version"] !== 1) {
          throw new BadRequest('"data" must be a version 1 project document');
        }
        const summary = await saveProject(typeof name === "string" ? name : "", data);
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
      requireWav(request);
      const wav = await readBinaryBody(request, MAX_AUDIO_BYTES);
      if (wav.length === 0) throw new BadRequest("The request has no audio");
      const pcm = decodeAudio(wav);
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
      requireJson(request);
      const payload = downloadRequest(await readJsonBody(request));
      sendJson(response, 201, { id: startDownload(payload) });
    } catch (error) {
      if (error instanceof DownloadLimitError) {
        const body: LlmErrorBody = { error: "llm_error", title: error.message, hint: "" };
        sendJson(response, 429, body);
        return;
      }
      fail(response, error);
    }
    return;
  }

  const downloadPath = /^\/api\/download\/([0-9a-f-]{36})(\/file)?$/.exec(url.pathname);
  if (downloadPath !== null) {
    const id = downloadPath[1] ?? "";
    if (downloadPath[2] !== undefined) {
      if (method !== "GET") return methodNotAllowed(response);
      await serveDownloadFile(response, id);
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
      requireJson(request);
      const payload = speakersRequest(await readJsonBody(request, MAX_AUDIO_BYTES));
      const wav = decodeAudioBase64(payload.wav, '"wav"');
      const result: DiarizationResponse = await diarize(decodeAudio(wav), payload.segments);
      sendJson(response, 200, result);
    } catch (error) {
      fail(response, error);
    }
    return;
  }

  if (url.pathname === "/api/faces") {
    if (method !== "POST") return methodNotAllowed(response);
    try {
      requireJson(request);
      const payload = facesRequest(await readJsonBody(request, MAX_FACES_BODY_BYTES));
      const frames = payload.frames.map((frame, index) => ({
        time: frame.time,
        width: frame.width,
        height: frame.height,
        rgb: Uint8Array.from(
          decodeBase64(frame.rgb, frame.width * frame.height * 3, `frames[${index}].rgb`),
        ),
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







