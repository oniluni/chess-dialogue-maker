import { spawn, spawnSync } from "node:child_process";
import type { ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { mkdir, readdir, rm, stat } from "node:fs/promises";
import { join, resolve } from "node:path";

import type { DownloadStatus } from "../shared/download.ts";
import { SECTION_MARGIN } from "../shared/download.ts";
import { appendCacheEntry, cacheHit, cachedPath, cookiesFile, readCache } from "./download-cache.ts";

export interface DownloadRequest {
  url: string;
  start: number | null;
  end: number | null;
}

export interface DownloadOptions {
  /** Cache root; tests point it at a temporary directory. */
  root?: string;
  /** Whole-job limit, including the full-video retry. */
  timeoutMs?: number;
}

/** Raised when too many jobs are already queued or running. */
export class DownloadLimitError extends Error {
  constructor() {
    super("Too many downloads are already running; wait for one to finish.");
    this.name = "DownloadLimitError";
  }
}

interface Job extends DownloadStatus {
  id: string;
  createdAt: number;
  request: DownloadRequest;
  cacheRoot: string;
  /** Working directory of this job; `null` for a cache hit (the cached directory is not owned). */
  dir: string | null;
  /** Absolute path of the finished file, whichever directory holds it. */
  path: string | null;
  name: string | null;
  child: ChildProcess | null;
  timeoutMs: number;
  cancelled: boolean;
  timedOut: boolean;
  deleteRequested: boolean;
  inUse: number;
  finishedAt: number | null;
}

interface YtdlpRun {
  code: number;
  output: string;
  timedOut: boolean;
}

const CACHE_DIR = join(import.meta.dirname, "..", ".cache", "downloads");
const JOB_TTL = 60 * 60 * 1000;
/** Finished jobs kept for the UI; active jobs are never evicted. */
const MAX_JOBS = 16;
/** yt-dlp processes that may run at the same time. */
const MAX_CONCURRENT = 2;
const MAX_QUEUED_OR_RUNNING = 16;
const JOB_TIMEOUT = 10 * 60 * 1000;
const KILL_GRACE = 5_000;

const jobs = new Map<string, Job>();
const queue: Job[] = [];
let active = 0;
let stopping = false;

let ytdlpChecked: string | null = null;
let nodeOnPath: boolean | null = null;

function hasNode(): boolean {
  if (nodeOnPath === null) {
    try {
      nodeOnPath = spawnSync("node", ["--version"], { timeout: 5000, windowsHide: true }).status === 0;
    } catch {
      nodeOnPath = false;
    }
  }
  return nodeOnPath;
}

function scriptLike(bin: string): boolean {
  return /\.[cm]?js$/.test(bin);
}

async function version(bin: string): Promise<string | null> {
  const command = scriptLike(bin) ? process.execPath : bin;
  const args = scriptLike(bin) ? [bin, "--version"] : ["--version"];
  try {
    const result = spawnSync(command, args, { timeout: 15000, windowsHide: true, encoding: "utf8" });
    if (result.status !== 0) return null;
    return (result.stdout ?? "").trim().split(/\r?\n/)[0] ?? null;
  } catch {
    return null;
  }
}

/** `YTDLP_BIN` first (a path or a .js script), then `yt-dlp` from PATH. */
export async function resolveYtdlp(): Promise<string> {
  if (ytdlpChecked !== null) return ytdlpChecked;

  const configured = process.env["YTDLP_BIN"]?.trim();
  if (configured) {
    const found = await version(configured);
    if (found === null) throw new Error(`YTDLP_BIN does not run: ${configured}`);
    ytdlpChecked = configured;
    return configured;
  }

  const found = await version("yt-dlp");
  if (found === null) {
    throw new Error("yt-dlp is not installed. Install it (winget install yt-dlp) or set YTDLP_BIN to the executable.");
  }
  ytdlpChecked = "yt-dlp";
  return "yt-dlp";
}

function validUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}

function sectionArgs(request: DownloadRequest): { from: number; to: number | null; args: string[] } | null {
  const { start, end } = request;
  if (start === null && end === null) return null;
  const from = Math.max(0, (start ?? 0) - SECTION_MARGIN);
  const to = end === null ? null : end + SECTION_MARGIN;
  const range = to === null ? `*${from.toFixed(3)}-inf` : `*${from.toFixed(3)}-${to.toFixed(3)}`;
  return { from, to, args: ["--download-sections", range, "--force-keyframes-at-cuts"] };
}

function cookiesArgs(): string[] {
  const saved = cookiesFile();
  if (existsSync(saved)) return ["--cookies", saved];
  const browser = process.env["YTDLP_COOKIES_FROM_BROWSER"]?.trim();
  const file = process.env["YTDLP_COOKIES"]?.trim();
  if (file) return ["--cookies", file];
  if (browser) return ["--cookies-from-browser", browser];
  return [];
}

/** yt-dlp's own messages are long; keep the last lines and hide cookie-looking text. */
function redact(text: string): string {
  return text.replace(/(?<=(cookie|authorization|token|sid|session)[=:]\s*)\S+/gi, "***");
}

function classify(output: string): string {
  const text = redact(output);
  const patterns: Array<[RegExp, string]> = [
    [/sign in to confirm|not a bot/i, "YouTube asks to confirm you are not a bot. Set YTDLP_COOKIES_FROM_BROWSER=chrome (or YTDLP_COOKIES=path)."],
    [/private video/i, "This video is private."],
    [/members-only|join this channel/i, "This video is for channel members only."],
    [/confirm your age|age-restricted|inappropriate for some users/i, "This video is age-restricted; cookies are required."],
    [/not available in your country|geo.?block/i, "This video is not available in your country."],
    [/video unavailable|does not exist|has been removed/i, "The video is unavailable."],
    [/ffmpeg (is )?not (installed|found)/i, "ffmpeg is missing; yt-dlp needs it to merge the streams."],
    [/requested format is not available/i, "The requested format is not available for this video."],
    [/HTTP Error 429|too many requests/i, "The site is rate-limiting the download; try again later."],
    [/Unsupported URL|is not a valid URL/i, "This link is not supported."],
  ];
  for (const [pattern, message] of patterns) {
    if (pattern.test(text)) return message;
  }
  const lines = text.split(/\r?\n/).map((line) => line.trim()).filter((line) => line !== "");
  const tail = lines.slice(-3).join(" ");
  return tail === "" ? "The download failed." : `The download failed: ${tail.slice(0, 400)}`;
}

function parsePercent(line: string): number | null {
  const match = /\[download\]\s+([\d.]+)%/.exec(line);
  if (match === null) return null;
  const value = Number(match[1]);
  return Number.isFinite(value) ? Math.min(0.99, value / 100) : null;
}

/** Kills yt-dlp and its ffmpeg children; yt-dlp may run them in its own process group. */
function killTree(child: ChildProcess): void {
  const pid = child.pid;
  if (pid === undefined || child.exitCode !== null || child.signalCode !== null) return;
  if (process.platform === "win32") {
    const killer = spawn("taskkill", ["/pid", String(pid), "/T", "/F"], {
      windowsHide: true,
      stdio: "ignore",
    });
    killer.on("error", () => {
      child.kill();
    });
    killer.unref();
    return;
  }

  try {
    process.kill(-pid, "SIGTERM");
  } catch {
    child.kill("SIGTERM");
  }
  const force = setTimeout(() => {
    try {
      process.kill(-pid, "SIGKILL");
    } catch {
      child.kill("SIGKILL");
    }
  }, KILL_GRACE);
  force.unref();
}

export function runYtdlp(
  bin: string,
  args: string[],
  onLine: (line: string) => void,
  timeoutMs: number,
  maxOutput = 200_000,
  onChild?: (child: ChildProcess | null) => void,
): Promise<YtdlpRun> {
  return new Promise((resolvePromise, reject) => {
    const command = scriptLike(bin) ? process.execPath : bin;
    const commandArgs = scriptLike(bin) ? [bin, ...args] : args;
    const child = spawn(command, commandArgs, {
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
      detached: process.platform !== "win32",
    });
    onChild?.(child);
    let output = "";
    let timedOut = false;
    let settled = false;

    const timer = setTimeout(() => {
      timedOut = true;
      killTree(child);
    }, Math.max(1_000, timeoutMs));

    const finish = (callback: () => void): void => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      onChild?.(null);
      callback();
    };

    const handle = (text: string): void => {
      output += text;
      if (output.length > maxOutput) output = output.slice(-Math.floor(maxOutput / 2));
      for (const line of text.split(/\r?\n|\r/)) {
        const clean = line.trim();
        if (clean !== "") onLine(clean);
      }
    };

    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", handle);
    child.stderr.on("data", handle);
    child.on("error", (error) => finish(() => reject(error)));
    child.on("close", (code) => finish(() => resolvePromise({ code: code ?? -1, output, timedOut })));
  });
}

async function newestSource(dir: string): Promise<string | null> {
  const entries = await readdir(dir);
  const sources = entries.filter((name) => name.startsWith("source.") && !/\.(part|ytdl)$/.test(name));
  if (sources.length === 0) return null;
  const sized = await Promise.all(
    sources.map(async (name) => ({ name, size: (await stat(join(dir, name))).size })),
  );
  sized.sort((left, right) => right.size - left.size);
  return sized[0]?.name ?? null;
}

function removeDir(dir: string): void {
  void rm(dir, { recursive: true, force: true }).catch((error: unknown) => {
    console.warn("could not remove the download directory:", error);
  });
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function failJob(job: Job, message: string): void {
  if (job.cancelled) return;
  job.state = "error";
  job.message = message;
  job.finishedAt = Date.now();
}

async function runJob(job: Job): Promise<void> {
  const dir = job.dir;
  if (dir === null) return;

  try {
    const bin = await resolveYtdlp();
    if (job.cancelled) return;

    const section = sectionArgs(job.request);
    const args = [
      "--no-playlist",
      "--newline",
      "--progress",
      "--retries",
      "3",
      "--fragment-retries",
      "5",
      "--socket-timeout",
      "30",
      "-f",
      "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b",
      "--merge-output-format",
      "mp4",
      "-o",
      join(dir, "source.%(ext)s"),
      ...cookiesArgs(),
    ];
    if (hasNode()) args.push("--js-runtimes", "node");
    if (section !== null) args.push(...section.args);
    args.push(job.request.url);

    const deadline = Date.now() + job.timeoutMs;
    const remaining = (): number => Math.max(1_000, deadline - Date.now());
    job.message = "Starting the download…";
    job.state = "running";

    const onLine = (line: string): void => {
      const percent = parsePercent(line);
      if (percent !== null) {
        job.progress = percent;
        job.message = `Downloading ${Math.round(percent * 100)}%`;
      } else if (/\[Merger\]/.test(line)) {
        job.message = "Merging the audio and the video…";
      } else if (/^\[download\] Destination:/.test(line)) {
        job.message = "Downloading…";
      }
    };

    const result = await runYtdlp(bin, args, onLine, remaining(), 200_000, (child) => {
      job.child = child;
    });
    if (job.cancelled) return;
    if (result.timedOut) {
      job.timedOut = true;
      failJob(job, "The download timed out.");
      return;
    }

    let code = result.code;
    if (code !== 0 && section !== null) {
      // YouTube sometimes refuses a fragment but serves the whole video.
      job.message = "The fragment is unavailable; downloading the whole video…";
      job.progress = 0;
      const retry = args.filter((arg) => !section.args.includes(arg));
      const again = await runYtdlp(bin, retry, onLine, remaining(), 200_000, (child) => {
        job.child = child;
      });
      if (job.cancelled) return;
      if (again.timedOut) {
        job.timedOut = true;
        failJob(job, "The download timed out.");
        return;
      }
      code = again.code;
      if (code !== 0) {
        failJob(job, classify(again.output));
        return;
      }
    } else if (code !== 0) {
      failJob(job, classify(result.output));
      return;
    }

    const name = await newestSource(dir);
    if (job.cancelled) return;
    if (name === null) {
      failJob(job, "yt-dlp finished but wrote no video file.");
      return;
    }

    const path = join(dir, name);
    const info = await stat(path);
    if (job.cancelled) return;
    job.name = name;
    job.path = path;
    job.file = { name, size: info.size };
    try {
      await appendCacheEntry(job.cacheRoot, {
        url: job.request.url,
        start: job.request.start,
        end: job.request.end,
        dir,
        name,
        size: info.size,
      });
    } catch (error) {
      // The video is complete; a broken cache index only costs a re-download next time.
      console.warn("could not record the download cache:", error);
    }
    if (job.cancelled) return;
    job.progress = 1;
    job.state = "done";
    job.message = "Ready";
    job.finishedAt = Date.now();
  } catch (error) {
    if (job.cancelled) return;
    const message = job.timedOut ? "The download timed out." : classify(errorMessage(error));
    failJob(job, message);
    console.warn("download failed:", error);
  } finally {
    job.child = null;
  }
}

function pump(): void {
  if (stopping) return;
  while (active < MAX_CONCURRENT) {
    const job = queue.shift();
    if (job === undefined) break;
    if (job.cancelled || jobs.get(job.id) !== job) continue;
    active += 1;
    void runJob(job)
      .catch((error: unknown) => {
        failJob(job, classify(errorMessage(error)));
        console.warn("download crashed:", error);
      })
      .finally(() => {
        active -= 1;
        if (job.finishedAt === null) job.finishedAt = Date.now();
        pump();
        cleanup();
      });
  }
}

function pathShared(job: Job): boolean {
  if (job.path === null) return false;
  for (const other of jobs.values()) {
    if (other !== job && other.path === job.path) return true;
  }
  return false;
}

function cleanup(): void {
  const now = Date.now();
  for (const [id, job] of jobs) {
    if (job.state === "running" || job.inUse > 0 || pathShared(job)) continue;
    if (job.finishedAt !== null && now - job.finishedAt >= JOB_TTL) {
      jobs.delete(id);
      discardJob(job);
    }
  }

  const excess = jobs.size - MAX_JOBS;
  if (excess <= 0) return;
  const removable = [...jobs.values()]
    .filter((job) => job.state !== "running" && job.inUse === 0 && !pathShared(job))
    .sort((left, right) => left.createdAt - right.createdAt);
  for (const job of removable.slice(0, excess)) {
    jobs.delete(job.id);
    discardJob(job);
  }
}

/** Stops the child and frees the directory; a cache-hit job does not own the cached directory. */
function discardJob(job: Job): void {
  job.cancelled = true;
  const dir = job.dir;
  job.dir = null;
  if (job.child !== null) {
    const child = job.child;
    killTree(child);
    if (dir !== null) child.once("close", () => removeDir(dir));
    return;
  }
  if (dir !== null) removeDir(dir);
}

async function prepare(job: Job): Promise<void> {
  try {
    const hit = cacheHit(await readCache(job.cacheRoot), job.request.url, job.request.start, job.request.end);
    if (job.cancelled) return;
    if (hit !== null) {
      const path = cachedPath(hit);
      try {
        const info = await stat(path);
        if (job.cancelled) return;
        job.name = hit.name;
        job.path = path;
        job.file = { name: hit.name, size: info.size };
        // The cached directory belongs to an older job (or to the cache itself); this job
        // must never remove it.
        job.dir = null;
        job.progress = 1;
        job.state = "done";
        job.message = "Ready (already downloaded)";
        job.finishedAt = Date.now();
        cleanup();
        return;
      } catch {
        // The index is stale: the file disappeared, so download it again.
      }
    }

    const dir = job.dir;
    if (dir === null) return;
    await mkdir(dir, { recursive: true });
    if (job.cancelled) {
      removeDir(dir);
      return;
    }
    job.message = "Waiting for another download to finish…";
    queue.push(job);
    pump();
  } catch (error) {
    failJob(job, errorMessage(error));
    console.warn("download could not start:", error);
  }
}

export function startDownload(request: DownloadRequest, options: DownloadOptions = {}): string {
  const url = request.url.trim();
  if (!validUrl(url)) throw new Error("Paste a link that starts with http:// or https://");
  if (request.start !== null && request.end !== null && request.end <= request.start) {
    throw new Error("The end of the fragment must be after its start");
  }

  cleanup();
  const pending = [...jobs.values()].filter((job) => job.state === "running").length;
  if (pending >= MAX_QUEUED_OR_RUNNING) throw new DownloadLimitError();

  const id = randomUUID();
  const cacheRoot = resolve(options.root ?? CACHE_DIR);
  const timeoutMs =
    options.timeoutMs !== undefined && Number.isFinite(options.timeoutMs) && options.timeoutMs > 0
      ? options.timeoutMs
      : JOB_TIMEOUT;
  const job: Job = {
    id,
    createdAt: Date.now(),
    request: { url, start: request.start, end: request.end },
    cacheRoot,
    dir: join(cacheRoot, id),
    path: null,
    name: null,
    state: "running",
    progress: 0,
    message: "Preparing…",
    file: null,
    child: null,
    timeoutMs,
    cancelled: false,
    timedOut: false,
    deleteRequested: false,
    inUse: 0,
    finishedAt: null,
  };
  jobs.set(id, job);
  void prepare(job);
  return id;
}

export function statusOf(id: string): DownloadStatus | null {
  const job = jobs.get(id);
  if (job === undefined) return null;
  return { state: job.state, progress: job.progress, message: job.message, file: job.file };
}

export function fileOf(id: string): { path: string; name: string; size: number } | null {
  const job = jobs.get(id);
  if (job === undefined || job.state !== "done" || job.path === null || job.name === null) return null;
  return { path: job.path, name: job.name, size: job.file?.size ?? 0 };
}

/** Marks a file as being streamed, so cleanup cannot delete its directory under the reader. */
export function retainFile(id: string): boolean {
  const job = jobs.get(id);
  if (job === undefined || job.state !== "done" || job.path === null) return false;
  job.inUse += 1;
  return true;
}

export function releaseFile(id: string): void {
  const job = jobs.get(id);
  if (job === undefined) return;
  job.inUse = Math.max(0, job.inUse - 1);
  if (job.inUse === 0 && job.deleteRequested) {
    jobs.delete(id);
    discardJob(job);
  }
}

export function removeJob(id: string): void {
  const job = jobs.get(id);
  if (job === undefined) return;
  if (job.inUse > 0) {
    job.deleteRequested = true;
    job.cancelled = true;
    return;
  }
  jobs.delete(id);
  discardJob(job);
}

/** Kills every yt-dlp process; called when the backend shuts down. */
export function stopDownloads(): void {
  stopping = true;
  queue.length = 0;
  for (const job of jobs.values()) {
    job.cancelled = true;
    if (job.child !== null) killTree(job.child);
  }
  jobs.clear();
}

export async function ytdlpVersion(): Promise<string> {
  const bin = await resolveYtdlp();
  const result = await runYtdlp(bin, ["--version"], () => undefined, 15_000);
  if (result.timedOut) throw new Error("yt-dlp did not answer in time");
  return result.output.trim().split(/\r?\n/)[0] ?? "";
}

export async function updateYtdlp(): Promise<string> {
  const bin = await resolveYtdlp();
  const result = await runYtdlp(bin, ["-U"], () => undefined, 120_000);
  if (result.timedOut) throw new Error("the yt-dlp update timed out");
  if (result.code !== 0) throw new Error(classify(result.output));
  return result.output.trim();
}
