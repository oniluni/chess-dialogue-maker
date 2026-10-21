import { createServer } from "node:http";
import type { IncomingMessage, ServerResponse } from "node:http";
import { existsSync } from "node:fs";

import type { ErrorResponse, HealthResponse } from "../shared/api.ts";
import { DIST_DIR, HOST, PORT } from "./config.ts";
import { serveStatic } from "./static.ts";

const startedAt = Date.now();

function sendJson(response: ServerResponse, status: number, body: unknown): void {
  const payload = JSON.stringify(body);
  response.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": Buffer.byteLength(payload),
    "cache-control": "no-store",
  });
  response.end(payload);
}

function sendText(response: ServerResponse, status: number, text: string): void {
  response.writeHead(status, { "content-type": "text/plain; charset=utf-8" });
  response.end(text);
}

async function handle(request: IncomingMessage, response: ServerResponse): Promise<void> {
  if (request.method !== "GET") {
    sendJson(response, 405, { error: "method_not_allowed" } satisfies ErrorResponse);
    return;
  }

  const url = new URL(request.url ?? "/", `http://${HOST}:${PORT}`);

  if (url.pathname === "/api/health") {
    const body: HealthResponse = {
      ok: true,
      service: "chess-dialogue-maker",
      uptimeSeconds: Math.round((Date.now() - startedAt) / 1000),
    };
    sendJson(response, 200, body);
    return;
  }

  if (url.pathname.startsWith("/api/")) {
    sendJson(response, 404, { error: "not_found" } satisfies ErrorResponse);
    return;
  }

  if (!existsSync(DIST_DIR)) {
    sendText(response, 503, "The UI is not built yet: run `pnpm build` first.\n");
    return;
  }

  await serveStatic(DIST_DIR, url.pathname, response);
}

const server = createServer((request, response) => {
  handle(request, response).catch((error: unknown) => {
    console.error("request failed:", error);
    if (!response.headersSent) {
      sendText(response, 500, "internal error\n");
      return;
    }
    response.destroy();
  });
});

server.listen(PORT, HOST, () => {
  console.log(`backend: http://${HOST}:${PORT}`);
});

for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.on(signal, () => {
    server.closeAllConnections();
    server.close(() => process.exit(0));
  });
}
