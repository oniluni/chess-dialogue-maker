import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { createServer, request as httpRequest } from "node:http";
import type { Server } from "node:http";
import type { AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { after, before, test } from "node:test";

import { handleApi } from "../server/api.ts";

const scratch = mkdtempSync(join(tmpdir(), "cdm-download-"));
const fakeBin = join(scratch, "fake-yt-dlp.mjs");
writeFileSync(
  fakeBin,
  `import { appendFileSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

const args = process.argv.slice(2);
if (args.includes("--version")) {
  console.log("fake yt-dlp 1.0");
  process.exit(0);
}
const out = args.indexOf("-o");
const template = out >= 0 ? args[out + 1] : "";
const log = process.env.FAKE_YTDLP_LOG ?? "";
if (log) appendFileSync(log, \`start \${process.pid}\\n\`);
const sleep = Number(process.env.FAKE_YTDLP_SLEEP ?? "0");
if (sleep > 0) await new Promise((resolve) => setTimeout(resolve, sleep));
if (!template) {
  console.error("no output template");
  process.exit(2);
}
const target = template.replace("%(ext)s", "mp4");
mkdirSync(dirname(target), { recursive: true });
writeFileSync(target, "fake video bytes");
if (log) appendFileSync(log, \`end \${process.pid}\\n\`);
`,
  "utf8",
);

process.env["YTDLP_BIN"] = fakeBin;
const download = await import("../server/download.ts");

let apiServer: Server;
let apiPort = 0;

before(async () => {
  process.env["YTDLP_BIN"] = fakeBin;
  apiServer = createServer((request, response) => {
    const url = new URL(request.url ?? "/", "http://127.0.0.1");
    void handleApi(request, response, url).catch((error: unknown) => {
      console.error("test API failed:", error);
      if (!response.headersSent) response.writeHead(500);
      response.end();
    });
  });
  await new Promise<void>((resolve, reject) => {
    apiServer.once("error", reject);
    apiServer.listen(0, "127.0.0.1", () => resolve());
  });
  apiPort = (apiServer.address() as AddressInfo).port;
});

after(async () => {
  await new Promise<void>((resolve) => apiServer.close(() => resolve()));
  await rm(scratch, { recursive: true, force: true });
});

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return (error as NodeJS.ErrnoException).code === "EPERM";
  }
}

async function waitFor(id: string, timeoutMs = 15_000): Promise<import("../shared/download.ts").DownloadStatus> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const status = download.statusOf(id);
    if (status !== null && status.state !== "running") return status;
    await sleep(25);
  }
  throw new Error(`the download did not finish: ${JSON.stringify(download.statusOf(id))}`);
}

async function waitUntil(check: () => boolean, timeoutMs = 10_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (check()) return;
    await sleep(50);
  }
  throw new Error("the condition was not met in time");
}

interface RunLog {
  starts: Array<{ pid: number; at: number }>;
  ends: Array<{ pid: number; at: number }>;
}

async function readRunLog(file: string): Promise<RunLog> {
  if (!existsSync(file)) return { starts: [], ends: [] };
  const lines = (await readFile(file, "utf8")).trim().split(/\r?\n/).filter((line) => line !== "");
  const starts: RunLog["starts"] = [];
  const ends: RunLog["ends"] = [];
  lines.forEach((line, index) => {
    const [kind, pid] = line.split(" ");
    if (kind === "start" && pid !== undefined) starts.push({ pid: Number(pid), at: index });
    if (kind === "end" && pid !== undefined) ends.push({ pid: Number(pid), at: index });
  });
  return { starts, ends };
}

function getFile(path: string): Promise<{ status: number; body: Buffer; type: string }> {
  return new Promise((resolve, reject) => {
    const client = httpRequest({ hostname: "127.0.0.1", port: apiPort, path }, (response) => {
      const chunks: Buffer[] = [];
      response.on("data", (chunk: Buffer) => chunks.push(chunk));
      response.on("end", () =>
        resolve({
          status: response.statusCode ?? 0,
          body: Buffer.concat(chunks),
          type: String(response.headers["content-type"] ?? ""),
        }),
      );
    });
    client.on("error", reject);
    client.end();
  });
}

test("streams a download that came from the cache", async () => {
  const root = await mkdtemp(join(tmpdir(), "cdm-cache-serve-"));
  const request = { url: "https://example.test/api-cache-hit", start: null, end: null };
  try {
    const firstId = download.startDownload(request, { root, timeoutMs: 15_000 });
    assert.equal((await waitFor(firstId)).state, "done");
    const first = await getFile(`/api/download/${firstId}/file`);
    assert.equal(first.status, 200);
    assert.equal(first.type, "video/mp4");
    assert.equal(first.body.toString("utf8"), "fake video bytes");

    const secondId = download.startDownload(request, { root, timeoutMs: 15_000 });
    assert.equal((await waitFor(secondId)).state, "done");
    const second = await getFile(`/api/download/${secondId}/file`);
    assert.equal(second.status, 200);
    assert.equal(second.body.toString("utf8"), "fake video bytes");

    download.removeJob(secondId);
    await sleep(100);
    const cachedFile = download.fileOf(firstId);
    assert.ok(cachedFile !== null);
    assert.ok(existsSync(cachedFile.path));

    download.removeJob(firstId);
    await waitUntil(() => !existsSync(cachedFile.path));
    const missing = await getFile(`/api/download/${firstId}/file`);
    assert.equal(missing.status, 404);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("serves a download from the cache with the cached file path", async () => {
  const root = await mkdtemp(join(tmpdir(), "cdm-cache-hit-"));
  const request = { url: "https://example.test/cache-hit", start: null, end: null };
  try {
    const firstId = download.startDownload(request, { root, timeoutMs: 15_000 });
    const first = await waitFor(firstId);
    assert.equal(first.state, "done");
    assert.equal(first.message, "Ready");
    const firstFile = download.fileOf(firstId);
    assert.ok(firstFile !== null);
    assert.ok(existsSync(firstFile.path));

    const secondId = download.startDownload(request, { root, timeoutMs: 15_000 });
    const second = await waitFor(secondId);
    assert.equal(second.state, "done");
    assert.equal(second.message, "Ready (already downloaded)");
    const secondFile = download.fileOf(secondId);
    assert.ok(secondFile !== null);
    assert.equal(secondFile.path, firstFile.path);
    assert.ok(existsSync(secondFile.path));

    // Dropping the job that hit the cache must not delete the cached file.
    download.removeJob(secondId);
    await sleep(100);
    assert.ok(existsSync(firstFile.path));

    download.removeJob(firstId);
    await waitUntil(() => !existsSync(firstFile.path));
  } finally {
    download.removeJob("missing");
    await rm(root, { recursive: true, force: true });
  }
});

test("runs at most two yt-dlp processes at a time and queues the rest", async () => {
  const root = await mkdtemp(join(tmpdir(), "cdm-concurrency-"));
  const log = join(root, "run.log");
  process.env["FAKE_YTDLP_LOG"] = log;
  process.env["FAKE_YTDLP_SLEEP"] = "300";
  try {
    const ids = [0, 1, 2].map((index) =>
      download.startDownload(
        { url: `https://example.test/concurrent/${index}`, start: null, end: null },
        { root, timeoutMs: 20_000 },
      ),
    );
    const statuses = await Promise.all(ids.map((id) => waitFor(id, 20_000)));
    for (const status of statuses) assert.equal(status.state, "done");

    const events = await readRunLog(log);
    assert.equal(events.starts.length, 3);
    assert.equal(events.ends.length, 3);

    const timeline = [
      ...events.starts.map((event) => ({ at: event.at, delta: 1 })),
      ...events.ends.map((event) => ({ at: event.at, delta: -1 })),
    ].sort((left, right) => left.at - right.at);
    let active = 0;
    let peak = 0;
    for (const event of timeline) {
      active += event.delta;
      peak = Math.max(peak, active);
    }
    assert.equal(peak, 2);

    for (const id of ids) download.removeJob(id);
    await waitUntil(() => ids.every((id) => download.statusOf(id) === null));
  } finally {
    delete process.env["FAKE_YTDLP_LOG"];
    delete process.env["FAKE_YTDLP_SLEEP"];
    await rm(root, { recursive: true, force: true });
  }
});

test("cancelling a job terminates its yt-dlp process", async () => {
  const root = await mkdtemp(join(tmpdir(), "cdm-cancel-"));
  const log = join(root, "run.log");
  process.env["FAKE_YTDLP_LOG"] = log;
  process.env["FAKE_YTDLP_SLEEP"] = "10000";
  try {
    const id = download.startDownload(
      { url: "https://example.test/cancel", start: null, end: null },
      { root, timeoutMs: 30_000 },
    );
    await waitUntil(() => existsSync(log));
    await waitUntil(() => readFileSync(log, "utf8").includes("start"));
    const events = await readRunLog(log);
    const pid = events.starts[0]?.pid;
    assert.ok(pid !== undefined);
    assert.ok(isAlive(pid));

    download.removeJob(id);
    assert.equal(download.statusOf(id), null);
    await waitUntil(() => !isAlive(pid));
  } finally {
    delete process.env["FAKE_YTDLP_LOG"];
    delete process.env["FAKE_YTDLP_SLEEP"];
    await rm(root, { recursive: true, force: true });
  }
});

test("a job that exceeds its timeout is stopped and reported", async () => {
  const root = await mkdtemp(join(tmpdir(), "cdm-timeout-"));
  const log = join(root, "run.log");
  process.env["FAKE_YTDLP_LOG"] = log;
  process.env["FAKE_YTDLP_SLEEP"] = "10000";
  try {
    const id = download.startDownload(
      { url: "https://example.test/timeout", start: null, end: null },
      { root, timeoutMs: 1_500 },
    );
    const status = await waitFor(id, 10_000);
    assert.equal(status.state, "error");
    assert.match(status.message, /timed out/);

    const events = await readRunLog(log);
    const pid = events.starts[0]?.pid;
    if (pid !== undefined) await waitUntil(() => !isAlive(pid));
    download.removeJob(id);
  } finally {
    delete process.env["FAKE_YTDLP_LOG"];
    delete process.env["FAKE_YTDLP_SLEEP"];
    await rm(root, { recursive: true, force: true });
  }
});

test("cleaning a finished job removes its working directory", async () => {
  const root = await mkdtemp(join(tmpdir(), "cdm-cleanup-"));
  process.env["FAKE_YTDLP_SLEEP"] = "0";
  try {
    const id = download.startDownload(
      { url: "https://example.test/cleanup", start: null, end: null },
      { root, timeoutMs: 15_000 },
    );
    const status = await waitFor(id);
    assert.equal(status.state, "done");
    const file = download.fileOf(id);
    assert.ok(file !== null);
    const directory = dirname(file.path);
    assert.ok(existsSync(directory));

    download.removeJob(id);
    await waitUntil(() => !existsSync(directory));
  } finally {
    delete process.env["FAKE_YTDLP_SLEEP"];
    await rm(root, { recursive: true, force: true });
  }
});
