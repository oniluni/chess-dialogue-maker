import { existsSync } from "node:fs";
import { mkdir, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import { join } from "node:path";

const COOKIES_FILE = join(import.meta.dirname, "..", ".cache", "yt-cookies.txt");

export function cookiesFile(): string {
  return COOKIES_FILE;
}

/** Netscape cookies.txt pasted in the UI; yt-dlp gets it as --cookies. */
export async function saveCookies(text: string): Promise<void> {
  const trimmed = text.trim();
  if (trimmed === "") throw new Error("The cookies text is empty");
  if (!trimmed.includes("\t") && !trimmed.startsWith("# Netscape")) {
    throw new Error("Paste cookies in Netscape format: the lines are tab-separated, ending with the name and the value");
  }
  await mkdir(join(import.meta.dirname, "..", ".cache"), { recursive: true });
  await writeFile(COOKIES_FILE, `${trimmed}\n`, "utf8");
}

export interface DownloadCacheEntry {
  url: string;
  start: number | null;
  end: number | null;
  dir: string;
  name: string;
  size: number;
}

function indexFile(dir: string): string {
  return join(dir, "index.json");
}

/** The last downloads, so the same link with the same fragment is not fetched twice. */
export async function readCache(dir: string): Promise<DownloadCacheEntry[]> {
  try {
    const parsed: unknown = JSON.parse(await readFile(indexFile(dir), "utf8"));
    return Array.isArray(parsed) ? (parsed as DownloadCacheEntry[]) : [];
  } catch {
    return [];
  }
}

export async function writeCache(dir: string, entries: DownloadCacheEntry[]): Promise<void> {
  await mkdir(dir, { recursive: true });
  await writeFile(indexFile(dir), JSON.stringify(entries.slice(-40)), "utf8");
}

export function cacheHit(
  entries: DownloadCacheEntry[],
  url: string,
  start: number | null,
  end: number | null,
): DownloadCacheEntry | null {
  for (const entry of entries) {
    if (entry.url !== url || entry.start !== start || entry.end !== end) continue;
    if (existsSync(join(entry.dir, entry.name))) return entry;
  }
  return null;
}
