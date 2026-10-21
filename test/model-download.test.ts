import assert from "node:assert/strict";
import { createServer } from "node:http";
import type { IncomingMessage, Server, ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { ensureModel } from "../server/model-download.ts";

interface Harness {
  url: string;
  close: () => Promise<void>;
}

async function serve(handler: (request: IncomingMessage, response: ServerResponse) => void): Promise<Harness> {
  const server: Server = createServer(handler);
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve());
  });
  const { port } = server.address() as AddressInfo;
  return {
    url: `http://127.0.0.1:${port}/model.onnx`,
    close: async () => {
      server.closeAllConnections();
      await new Promise<void>((resolve) => server.close(() => resolve()));
    },
  };
}

test("downloads a model once and reuses the existing file", async () => {
  const root = await mkdtemp(join(tmpdir(), "cdm-model-"));
  let requests = 0;
  const harness = await serve((_request, response) => {
    requests += 1;
    response.writeHead(200, { "content-type": "application/octet-stream" });
    response.end("model bytes");
  });
  try {
    const path = join(root, "nested", "model.onnx");
    await ensureModel(harness.url, path);
    assert.equal(await readFile(path, "utf8"), "model bytes");
    assert.equal(requests, 1);

    await ensureModel(harness.url, path);
    assert.equal(requests, 1);
  } finally {
    await harness.close();
    await rm(root, { recursive: true, force: true });
  }
});

test("replaces a zero-byte placeholder and reports HTTP failures", async () => {
  const root = await mkdtemp(join(tmpdir(), "cdm-model-empty-"));
  try {
    const path = join(root, "model.onnx");
    await writeFile(path, "");
    const good = await serve((_request, response) => {
      response.writeHead(200);
      response.end("replacement");
    });
    try {
      await ensureModel(good.url, path);
      assert.equal(await readFile(path, "utf8"), "replacement");
    } finally {
      await good.close();
    }

    const missing = await serve((_request, response) => {
      response.writeHead(404);
      response.end("no");
    });
    try {
      await assert.rejects(() => ensureModel(missing.url, join(root, "other.onnx")), /HTTP 404/);
    } finally {
      await missing.close();
    }
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("does not leave a truncated model when the stream fails", async () => {
  const root = await mkdtemp(join(tmpdir(), "cdm-model-broken-"));
  const harness = await serve((_request, response) => {
    response.writeHead(200, { "content-length": "1000" });
    response.write("partial");
    response.destroy();
  });
  try {
    await assert.rejects(() => ensureModel(harness.url, join(root, "model.onnx")));
    assert.deepEqual(await readdir(root), []);
  } finally {
    await harness.close();
    await rm(root, { recursive: true, force: true });
  }
});

test("rejects a declared size that is too large without writing a file", async () => {
  const root = await mkdtemp(join(tmpdir(), "cdm-model-large-"));
  const harness = await serve((_request, response) => {
    response.writeHead(200, { "content-length": String(300 * 1024 * 1024) });
    response.flushHeaders();
  });
  try {
    await assert.rejects(() => ensureModel(harness.url, join(root, "model.onnx")), /larger than/);
    assert.deepEqual(await readdir(root), []);
  } finally {
    await harness.close();
    await rm(root, { recursive: true, force: true });
  }
});
