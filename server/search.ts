import { spawn } from "node:child_process";
import { resolveYtdlp } from "./download.ts";

export interface SearchHit {
  id: string;
  title: string;
  channel: string;
  duration: number | null;
  thumbnail: string;
  url: string;
}

const CHANNEL = /^@([\w.-]+)\s+(.+)$/;

function queryUrl(query: string): string {
  const channel = CHANNEL.exec(query.trim());
  if (channel === null) return `ytsearch12:${query.trim()}`;
  const handle = channel[1] ?? "";
  const words = channel[2] ?? "";
  return `https://www.youtube.com/@${handle}/search?query=${encodeURIComponent(words)}`;
}

interface RawResult {
  id?: unknown;
  title?: unknown;
  channel?: unknown;
  uploader?: unknown;
  duration?: unknown;
  thumbnail?: unknown;
  url?: unknown;
  webpage_url?: unknown;
}

/** Searches YouTube through yt-dlp; `@handle words` searches inside that channel. */
export async function search(query: string): Promise<SearchHit[]> {
  const text = query.trim();
  if (text === "") return [];

  const bin = await resolveYtdlp();
  const args = [
    "--flat-playlist",
    "--dump-json",
    "--no-warnings",
    "--skip-download",
    "--playlist-end",
    "12",
    queryUrl(text),
  ];

  const output = await new Promise<string>((resolve, reject) => {
    const child = spawn(bin, args, { windowsHide: true });
    let buffer = "";
    let error = "";
    child.stdout.on("data", (chunk: Buffer) => {
      buffer += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk: Buffer) => {
      error += chunk.toString("utf8");
    });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) resolve(buffer);
      else reject(new Error(error.trim().split(/\r?\n/).slice(-1)[0] ?? "yt-dlp search failed"));
    });
  });

  const hits: SearchHit[] = [];
  for (const line of output.split(/\r?\n/)) {
    if (line.trim() === "") continue;
    let raw: RawResult;
    try {
      raw = JSON.parse(line) as RawResult;
    } catch {
      continue;
    }
    const id = typeof raw.id === "string" ? raw.id : "";
    const title = typeof raw.title === "string" ? raw.title : "";
    if (id === "" || title === "") continue;
    hits.push({
      id,
      title,
      channel: typeof raw.channel === "string" ? raw.channel : typeof raw.uploader === "string" ? raw.uploader : "",
      duration: typeof raw.duration === "number" ? raw.duration : null,
      thumbnail: typeof raw.thumbnail === "string" ? raw.thumbnail : "",
      url: `https://www.youtube.com/watch?v=${id}`,
    });
  }
  return hits;
}

