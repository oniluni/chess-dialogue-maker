import { randomUUID } from "node:crypto";
import { mkdir, rename, rm, stat, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

const MAX_BYTES = 256 * 1024 * 1024;
const TIMEOUT_MS = 10 * 60 * 1000;

function isMissing(error: unknown): boolean {
  return typeof error === "object" && error !== null && "code" in error && error.code === "ENOENT";
}

/**
 * Downloads a model once and writes it atomically: a failed or interrupted download leaves a
 * `.part` file behind instead of a truncated file that the next start would treat as valid.
 * No checksum is pinned here because the upstream model files do not publish a stable one that
 * this project can maintain; HTTPS and the atomic write are the guarantees we can make.
 */
export async function ensureModel(url: string, path: string): Promise<void> {
  try {
    const info = await stat(path);
    if (info.isFile() && info.size > 0) return;
  } catch (error) {
    if (!isMissing(error)) throw error;
  }

  const response = await fetch(url, { signal: AbortSignal.timeout(TIMEOUT_MS) });
  if (!response.ok) throw new Error(`download failed: HTTP ${response.status}`);
  const declared = Number(response.headers.get("content-length") ?? "0");
  if (Number.isFinite(declared) && declared > MAX_BYTES) {
    throw new Error("the model file is larger than this backend accepts");
  }
  if (response.body === null) throw new Error("the model download has no body");

  const reader = response.body.getReader();
  const chunks: Buffer[] = [];
  let size = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_BYTES) {
        await reader.cancel();
        throw new Error("the model file is larger than this backend accepts");
      }
      chunks.push(Buffer.from(value));
    }
  } finally {
    reader.releaseLock();
  }
  if (size === 0) throw new Error("the model download is empty");

  await mkdir(dirname(path), { recursive: true });
  const temporary = `${path}.${randomUUID()}.part`;
  try {
    await writeFile(temporary, Buffer.concat(chunks));
    await rename(temporary, path);
  } catch (error) {
    await rm(temporary, { force: true }).catch(() => undefined);
    throw error;
  }
}
