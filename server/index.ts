import "./env.ts";

import { createServer } from "node:http";
import type { IncomingMessage, ServerResponse } from "node:http";
import { existsSync } from "node:fs";

import { handleApi, sendJson, sendText } from "./api.ts";
import { DIST_DIR, HOST, PORT } from "./config.ts";
import { serveStatic } from "./static.ts";

async function handle(request: IncomingMessage, response: ServerResponse): Promise<void> {
  const url = new URL(request.url ?? "/", `http://${HOST}:${PORT}`);

  if (url.pathname.startsWith("/api/")) {
    await handleApi(request, response, url);
    return;
  }

  if (request.method !== "GET") {
    sendJson(response, 405, { error: "method_not_allowed" });
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
