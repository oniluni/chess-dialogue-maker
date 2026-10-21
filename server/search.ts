import { resolveYtdlp, runYtdlp } from "./download.ts";

export interface SearchHit {
  id: string;
  title: string;
  channel: string;
  duration: number | null;
  thumbnail: string;
  url: string;
}

const CHANNEL = /^@([\w.-]+)\s+(.+)$/;
const MAX_QUERY = 200;
const MAX_HITS = 12;
const TIMEOUT_MS = 60_000;
const MAX_OUTPUT = 8_000_000;

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
}

/** Searches YouTube through yt-dlp; `@handle words` searches inside that channel. */
export async function search(query: string): Promise<SearchHit[]> {
  const text = query.trim();
  if (text === "") return [];
  if (text.length > MAX_QUERY) throw new Error(`the search query is longer than ${MAX_QUERY} characters`);

  const bin = await resolveYtdlp();
  const args = [
    "--flat-playlist",
    "--dump-json",
    "--no-warnings",
    "--skip-download",
    "--playlist-end",
    String(MAX_HITS),
    queryUrl(text),
  ];

  const result = await runYtdlp(bin, args, () => undefined, TIMEOUT_MS, MAX_OUTPUT);
  if (result.timedOut) throw new Error("the YouTube search timed out");
  if (result.code !== 0) {
    const lines = result.output.trim().split(/\r?\n/);
    throw new Error(lines[lines.length - 1] ?? "yt-dlp search failed");
  }

  const hits: SearchHit[] = [];
  for (const line of result.output.split(/\r?\n/)) {
    if (hits.length >= MAX_HITS) break;
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
