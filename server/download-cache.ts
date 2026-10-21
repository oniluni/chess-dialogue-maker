import { constants, statSync } from "node:fs";
import { chmod, lstat, mkdir, open, readFile, unlink } from "node:fs/promises";
import { dirname, isAbsolute, join, resolve, sep } from "node:path";

const CACHE_ROOT = join(import.meta.dirname, "..", ".cache");
const COOKIES_FILE = join(CACHE_ROOT, "yt-cookies.txt");

export function cookiesFile(): string {
  return COOKIES_FILE;
}

function cookieText(text: string): string {
  const trimmed = text.trim();
  if (trimmed === "") throw new Error("The cookies text is empty");
  if (!trimmed.includes("\t") && !trimmed.startsWith("# Netscape")) {
    throw new Error(
      "Paste cookies in Netscape format: the lines are tab-separated, ending with the name and the value",
    );
  }
  return trimmed;
}

/**
 * Writes a Netscape cookies.txt readable only by the current user. POSIX gets mode 0600 and an
 * owner-only directory; Windows inherits the ACL of the user's profile, which is the platform
 * mechanism there. A pre-existing symlink is removed instead of followed.
 */
export async function saveCookiesTo(file: string, text: string): Promise<void> {
  const content = `${cookieText(text)}\n`;
  const directory = dirname(file);
  await mkdir(directory, { recursive: true, mode: 0o700 });
  if (process.platform !== "win32") {
    await chmod(directory, 0o700).catch((error: unknown) => {
      console.warn("could not restrict the cookies directory:", error);
    });
  }

  try {
    const info = await lstat(file);
    if (!info.isFile() && !info.isSymbolicLink()) throw new Error("the cookies path is not a regular file");
    if (info.isSymbolicLink()) await unlink(file);
  } catch (error) {
    if (!isMissing(error)) throw error;
  }

  const flags =
    process.platform === "win32"
      ? constants.O_WRONLY | constants.O_CREAT | constants.O_TRUNC
      : constants.O_WRONLY | constants.O_CREAT | constants.O_TRUNC | constants.O_NOFOLLOW;
  const handle = await open(file, flags, 0o600);
  try {
    await handle.writeFile(content, "utf8");
  } finally {
    await handle.close();
  }
  if (process.platform !== "win32") await chmod(file, 0o600);
}

/** Netscape cookies.txt pasted in the UI; yt-dlp gets it as --cookies. */
export async function saveCookies(text: string): Promise<void> {
  await saveCookiesTo(COOKIES_FILE, text);
}

function isMissing(error: unknown): boolean {
  return typeof error === "object" && error !== null && "code" in error && error.code === "ENOENT";
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function optionalSeconds(value: unknown): number | null | undefined {
  if (value === null) return null;
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : undefined;
}

function inside(root: string, candidate: string): boolean {
  const base = resolve(root);
  const target = resolve(candidate);
  return target === base || target.startsWith(`${base}${sep}`);
}

/** A usable entry: a real file name inside the cache directory, never a path from the index. */
function entryOf(value: unknown, root: string): DownloadCacheEntry | null {
  if (!isRecord(value)) return null;
  const url = value["url"];
  const start = optionalSeconds(value["start"]);
  const end = optionalSeconds(value["end"]);
  const dir = value["dir"];
  const name = value["name"];
  const size = value["size"];
  if (typeof url !== "string" || url === "") return null;
  if (start === undefined || end === undefined) return null;
  if (start !== null && end !== null && end <= start) return null;
  if (typeof dir !== "string" || !isAbsolute(dir) || !inside(root, dir)) return null;
  if (typeof name !== "string" || name === "" || name === "." || name === "..") return null;
  if (name !== name.split(/[\\/]/).pop() || name.includes("\0")) return null;
  if (typeof size !== "number" || !Number.isFinite(size) || size < 0) return null;
  return { url, start, end, dir, name, size };
}

/** The last downloads, so the same link with the same fragment is not fetched twice. */
export async function readCache(dir: string): Promise<DownloadCacheEntry[]> {
  try {
    const parsed: unknown = JSON.parse(await readFile(indexFile(dir), "utf8"));
    if (!Array.isArray(parsed)) return [];
    return parsed.flatMap((entry) => {
      const valid = entryOf(entry, dir);
      return valid === null ? [] : [valid];
    });
  } catch (error) {
    if (!isMissing(error)) console.warn("the download cache index is unreadable:", error);
    return [];
  }
}

async function writeCache(dir: string, entries: DownloadCacheEntry[]): Promise<void> {
  await mkdir(dir, { recursive: true });
  await writeFileText(indexFile(dir), JSON.stringify(entries.slice(-40)));
}

async function writeFileText(file: string, text: string): Promise<void> {
  const handle = await open(file, constants.O_WRONLY | constants.O_CREAT | constants.O_TRUNC, 0o600);
  try {
    await handle.writeFile(text, "utf8");
  } finally {
    await handle.close();
  }
}

let writes: Promise<void> = Promise.resolve();

/**
 * Appends one entry and rewrites the index. Writes are serialised so two downloads finishing
 * together cannot overwrite each other's entry with a stale copy of the list.
 */
export function appendCacheEntry(dir: string, entry: DownloadCacheEntry): Promise<void> {
  const task = writes.then(async () => {
    const entries = await readCache(dir);
    entries.push(entry);
    await writeCache(dir, entries);
  });
  writes = task.catch(() => undefined);
  return task;
}

export function cacheHit(
  entries: DownloadCacheEntry[],
  url: string,
  start: number | null,
  end: number | null,
): DownloadCacheEntry | null {
  for (const entry of entries) {
    if (entry.url !== url || entry.start !== start || entry.end !== end) continue;
    try {
      if (statSync(cachedPath(entry)).isFile()) return entry;
    } catch {
      // The index is stale: the file is gone or unreadable.
    }
  }
  return null;
}

export function cachedPath(entry: DownloadCacheEntry): string {
  return join(entry.dir, entry.name);
}
