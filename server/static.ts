import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import type { ServerResponse } from "node:http";
import { extname, resolve, sep } from "node:path";

const MIME_TYPES: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".map": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".webp": "image/webp",
  ".woff2": "font/woff2",
  ".wasm": "application/wasm",
};

const FALLBACK_MIME = "application/octet-stream";

/** Absolute file path for a URL path, or null when it leaves `root` or is missing. */
async function resolveFile(root: string, pathname: string): Promise<string | null> {
  const candidate = pathname === "/" ? "index.html" : pathname.slice(1);
  const absolute = resolve(root, candidate);
  if (absolute !== root && !absolute.startsWith(root + sep)) {
    return null;
  }
  try {
    const info = await stat(absolute);
    return info.isFile() ? absolute : null;
  } catch {
    // A missing file is a 404, not an error worth logging.
    return null;
  }
}

/** Streams a file from `root`; answers 400/404 itself when the path cannot be served. */
export async function serveStatic(root: string, pathname: string, response: ServerResponse): Promise<void> {
  let decoded: string;
  try {
    decoded = decodeURIComponent(pathname);
  } catch {
    response.writeHead(400, { "content-type": "text/plain; charset=utf-8" });
    response.end("bad request path\n");
    return;
  }

  const file = await resolveFile(root, decoded);
  if (file === null) {
    response.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
    response.end("not found\n");
    return;
  }

  response.writeHead(200, {
    "content-type": MIME_TYPES[extname(file)] ?? FALLBACK_MIME,
    "cache-control": "no-cache",
  });
  const stream = createReadStream(file);
  stream.on("error", () => response.destroy());
  stream.pipe(response);
}
