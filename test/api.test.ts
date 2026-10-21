import assert from "node:assert/strict";
import { createServer, request as httpRequest } from "node:http";
import type { Server } from "node:http";
import type { AddressInfo } from "node:net";
import { after, before, test } from "node:test";

import { handleApi } from "../server/api.ts";

let server: Server;
let port = 0;

before(async () => {
  server = createServer((request, response) => {
    const url = new URL(request.url ?? "/", "http://127.0.0.1");
    void handleApi(request, response, url).catch((error: unknown) => {
      console.error("test server failed:", error);
      if (!response.headersSent) response.writeHead(500);
      response.end();
    });
  });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve());
  });
  port = (server.address() as AddressInfo).port;
});

after(async () => {
  await new Promise<void>((resolve) => server.close(() => resolve()));
});

interface Reply {
  status: number;
  body: string;
}

function request(
  path: string,
  options: { method?: string; headers?: Record<string, string>; body?: string } = {},
): Promise<Reply> {
  return new Promise((resolve, reject) => {
    const body = options.body;
    const headers: Record<string, string> = { ...options.headers };
    if (body !== undefined) headers["content-length"] = String(Buffer.byteLength(body));
    const client = httpRequest(
      { hostname: "127.0.0.1", port, path, method: options.method ?? "GET", headers },
      (response) => {
        const chunks: Buffer[] = [];
        response.on("data", (chunk: Buffer) => chunks.push(chunk));
        response.on("end", () => resolve({ status: response.statusCode ?? 0, body: Buffer.concat(chunks).toString("utf8") }));
      },
    );
    client.on("error", reject);
    client.end(body);
  });
}

function originFor(): string {
  return `http://127.0.0.1:${port}`;
}

test("serves the health endpoint to local clients", async () => {
  const reply = await request("/api/health");
  assert.equal(reply.status, 200);
  assert.equal(JSON.parse(reply.body).service, "chess-dialogue-maker");
});

test("rejects a Host header that is not a loopback name", async () => {
  const reply = await request("/api/health", { headers: { host: "evil.example" } });
  assert.equal(reply.status, 403);
});

test("accepts the loopback Host and the Vite development origin", async () => {
  const reply = await request("/api/health", {
    headers: { host: `127.0.0.1:${port}`, origin: "http://localhost:5173" },
  });
  assert.equal(reply.status, 200);
});

test("rejects a cross-origin page", async () => {
  const reply = await request("/api/health", {
    headers: { origin: "https://evil.example", "sec-fetch-site": "cross-site" },
  });
  assert.equal(reply.status, 403);
});

test("rejects a browser request marked cross-site even without Origin", async () => {
  const reply = await request("/api/health", { headers: { "sec-fetch-site": "cross-site" } });
  assert.equal(reply.status, 403);
});

test("rejects an opaque Origin", async () => {
  const reply = await request("/api/health", { headers: { origin: "null" } });
  assert.equal(reply.status, 403);
});

test("requires a JSON content type on state-changing endpoints", async () => {
  const plain = await request("/api/download", {
    method: "POST",
    headers: { "content-type": "text/plain", origin: originFor() },
    body: JSON.stringify({ url: "https://example.test/video", start: null, end: null }),
  });
  assert.equal(plain.status, 415);

  const missing = await request("/api/download", {
    method: "POST",
    headers: { origin: originFor() },
    body: JSON.stringify({ url: "https://example.test/video", start: null, end: null }),
  });
  assert.equal(missing.status, 415);
});

test("still lets a non-browser client reach a JSON endpoint", async () => {
  const reply = await request("/api/download", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ url: "not-a-link", start: null, end: null }),
  });
  assert.equal(reply.status, 400);
});

test("rejects a state-changing request from another origin before reading it", async () => {
  const reply = await request("/api/download", {
    method: "POST",
    headers: { "content-type": "application/json", origin: "https://evil.example" },
    body: JSON.stringify({ url: "https://example.test/video", start: null, end: null }),
  });
  assert.equal(reply.status, 403);
});

test("requires a WAV content type for the audio endpoints", async () => {
  const reply = await request("/api/transcribe", {
    method: "POST",
    headers: { "content-type": "text/plain", origin: originFor() },
    body: "not audio",
  });
  assert.equal(reply.status, 415);
});

test("rejects malformed RGB frames before any model work", async () => {
  const short = await request("/api/faces", {
    method: "POST",
    headers: { "content-type": "application/json", origin: originFor() },
    body: JSON.stringify({ frames: [{ time: 0, width: 2, height: 2, rgb: "AAAA" }] }),
  });
  assert.equal(short.status, 400);

  const rounded = await request("/api/faces", {
    method: "POST",
    headers: { "content-type": "application/json", origin: originFor() },
    body: JSON.stringify({ frames: [{ time: 0, width: 0.4, height: 2, rgb: "AAAA" }] }),
  });
  assert.equal(rounded.status, 400);

  const oversized = await request("/api/faces", {
    method: "POST",
    headers: { "content-type": "application/json", origin: originFor() },
    body: JSON.stringify({ frames: [{ time: 0, width: 5000, height: 5000, rgb: "AAAA" }] }),
  });
  assert.equal(oversized.status, 400);
});

test("validates project saves without writing anything", async () => {
  const badVersion = await request("/api/projects", {
    method: "POST",
    headers: { "content-type": "application/json", origin: originFor() },
    body: JSON.stringify({ name: "x", data: { version: 2 } }),
  });
  assert.equal(badVersion.status, 400);

  const notJson = await request("/api/projects", {
    method: "POST",
    headers: { "content-type": "text/plain", origin: originFor() },
    body: "{}",
  });
  assert.equal(notJson.status, 415);
});

test("answers 404 for a project that does not exist", async () => {
  const reply = await request("/api/projects?id=does-not-exist-0000");
  assert.equal(reply.status, 404);
});

test("answers 404 for an unknown download file", async () => {
  const reply = await request("/api/download/00000000-0000-4000-8000-000000000000/file");
  assert.equal(reply.status, 404);
});

test("rejects cookie writes from another origin", async () => {
  const reply = await request("/api/download/cookies", {
    method: "POST",
    headers: { "content-type": "application/json", origin: "https://evil.example" },
    body: JSON.stringify({ text: "# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tFALSE\t0\tsid\tsecret" }),
  });
  assert.equal(reply.status, 403);
});
