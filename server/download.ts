import { spawn, spawnSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdir, readdir, rm, stat } from "node:fs/promises";
import { join } from "node:path";

import type { DownloadStatus } from "../shared/download.ts";
import { SECTION_MARGIN } from "../shared/download.ts";

export interface DownloadRequest {
  url: string;
  start: number | null;
  end: number | null;
}

interface Job extends DownloadStatus {
  id: string;
  createdAt: number;
  dir: string;
  name: string | null;
}

const CACHE_DIR = join(import.meta.dirname, "..", ".cache", "downloads");
const JOB_TTL = 60 * 60 * 1000;
const MAX_JOBS = 16;

const jobs = new Map<string, Job>();

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

function spawnYtdlp(
  bin: string,
  args: string[],
  onLine: (line: string) => void,
): Promise<{ code: number; output: string }> {
  return new Promise((resolve, reject) => {
    const command = scriptLike(bin) ? process.execPath : bin;
    const commandArgs = scriptLike(bin) ? [bin, ...args] : args;
    const child = spawn(command, commandArgs, { windowsHide: true });
    let output = "";

    const handle = (text: string): void => {
      output += text;
      if (output.length > 200_000) output = output.slice(-100_000);
      for (const line of text.split(/\r?\n|\r/)) {
        const clean = line.trim();
        if (clean !== "") onLine(clean);
      }
    };

    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", handle);
    child.stderr.on("data", handle);
    child.on("error", reject);
    child.on("close", (code) => resolve({ code: code ?? -1, output }));
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

async function runJob(job: Job, request: DownloadRequest): Promise<void> {
  let output = "";
  try {
    const bin = await resolveYtdlp();
    const section = sectionArgs(request);
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
      join(job.dir, "source.%(ext)s"),
      ...cookiesArgs(),
    ];
    if (hasNode()) args.push("--js-runtimes", "node");
    if (section !== null) args.push(...section.args);
    args.push(request.url);

    job.message = "Starting the downloadâ€¦";
    const result = await spawnYtdlp(bin, args, (line) => {
      output += `${line}\n`;
      const percent = parsePercent(line);
      if (percent !== null) {
        job.progress = percent;
        job.message = `Downloading ${Math.round(percent * 100)}%`;
      } else if (/\[Merger\]/.test(line)) {
        job.message = "Merging the audio and the videoâ€¦";
      }
    });

    if (result.code !== 0) {
      job.state = "error";
      job.message = classify(result.output);
      return;
    }

    const name = await newestSource(job.dir);
    if (name === null) {
      job.state = "error";
      job.message = "yt-dlp finished but wrote no video file.";
      return;
    }

    const info = await stat(join(job.dir, name));
    job.name = name;
    job.file = { name, size: info.size };
    job.progress = 1;
    job.state = "done";
    job.message = "Ready";
  } catch (error) {
    job.state = "error";
    job.message = error instanceof Error ? error.message : String(error);
    console.warn("download failed:", error);
  }
}

function cleanup(): void {
  const now = Date.now();
  for (const [id, job] of jobs) {
    if (now - job.createdAt < JOB_TTL) continue;
    jobs.delete(id);
    void rm(job.dir, { recursive: true, force: true });
  }
  if (jobs.size <= MAX_JOBS) return;
  const oldest = [...jobs.values()].sort((left, right) => left.createdAt - right.createdAt);
  for (const job of oldest.slice(0, jobs.size - MAX_JOBS)) {
    jobs.delete(job.id);
    void rm(job.dir, { recursive: true, force: true });
  }
}

export function startDownload(request: DownloadRequest): string {
  const url = request.url.trim();
  if (!validUrl(url)) throw new Error("Paste a link that starts with http:// or https://");
  if (request.start !== null && request.end !== null && request.end <= request.start) {
    throw new Error("The end of the fragment must be after its start");
  }

  cleanup();
  const id = randomUUID();
  const dir = join(CACHE_DIR, id);
  const job: Job = {
    id,
    createdAt: Date.now(),
    dir,
    name: null,
    state: "running",
    progress: 0,
    message: "Preparingâ€¦",
    file: null,
  };
  jobs.set(id, job);
  void mkdir(dir, { recursive: true })
    .then(() => runJob(job, { url, start: request.start, end: request.end }))
    .catch((error: unknown) => {
      job.state = "error";
      job.message = error instanceof Error ? error.message : String(error);
    });
  return id;
}

export function statusOf(id: string): DownloadStatus | null {
  const job = jobs.get(id);
  if (job === undefined) return null;
  return { state: job.state, progress: job.progress, message: job.message, file: job.file };
}

export function fileOf(id: string): { path: string; name: string; size: number } | null {
  const job = jobs.get(id);
  if (job === undefined || job.state !== "done" || job.name === null) return null;
  return { path: join(job.dir, job.name), name: job.name, size: job.file?.size ?? 0 };
}


export function removeJob(id: string): void {
  const job = jobs.get(id);
  if (job === undefined) return;
  jobs.delete(id);
  void rm(job.dir, { recursive: true, force: true });
}


