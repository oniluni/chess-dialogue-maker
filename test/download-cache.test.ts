import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, stat, symlink, unlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import {
  appendCacheEntry,
  cacheHit,
  readCache,
  saveCookiesTo,
} from "../server/download-cache.ts";
import type { DownloadCacheEntry } from "../server/download-cache.ts";

async function temporaryRoot(): Promise<string> {
  return mkdtemp(join(tmpdir(), "cdm-cache-"));
}

function entry(root: string, index: number, options: Partial<DownloadCacheEntry> = {}): DownloadCacheEntry {
  return {
    url: `https://example.test/video/${index}`,
    start: null,
    end: null,
    dir: join(root, `job-${index}`),
    name: "source.mp4",
    size: 10,
    ...options,
  };
}

test("appends entries without losing concurrent writes", async () => {
  const root = await temporaryRoot();
  try {
    await Promise.all(Array.from({ length: 20 }, (_, index) => appendCacheEntry(root, entry(root, index))));
    const entries = await readCache(root);
    assert.equal(entries.length, 20);
    assert.deepEqual(
      entries.map((item) => item.url).sort(),
      Array.from({ length: 20 }, (_, index) => `https://example.test/video/${index}`).sort(),
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("ignores cache entries that point outside the cache or use a path as a name", async () => {
  const root = await temporaryRoot();
  try {
    const index = [
      entry(root, 1),
      entry(root, 2, { dir: join(root, "..", "outside") }),
      entry(root, 3, { name: "../secret.mp4" }),
      entry(root, 4, { name: ".." }),
      entry(root, 5, { size: -1 }),
      { url: "https://example.test/no-dir", name: "source.mp4", size: 1 },
    ];
    await mkdir(root, { recursive: true });
    await writeFile(join(root, "index.json"), JSON.stringify(index), "utf8");

    const entries = await readCache(root);
    assert.equal(entries.length, 1);
    assert.equal(entries[0]?.url, "https://example.test/video/1");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("a cache hit requires the file to still exist", async () => {
  const root = await temporaryRoot();
  try {
    const first = entry(root, 1);
    await mkdir(first.dir, { recursive: true });
    await writeFile(join(first.dir, first.name), "video", "utf8");
    const entries = [first];
    assert.equal(cacheHit(entries, first.url, null, null)?.name, "source.mp4");

    await unlink(join(first.dir, first.name));
    assert.equal(cacheHit(entries, first.url, null, null), null);
    assert.equal(cacheHit(entries, first.url, 1, null), null);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("writes cookies with owner-only permissions where the platform supports them", async () => {
  const root = await temporaryRoot();
  try {
    const file = join(root, "nested", "yt-cookies.txt");
    await saveCookiesTo(file, "# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tFALSE\t0\tsid\tsecret");

    const text = await readFile(file, "utf8");
    assert.match(text, /\tsid\tsecret\n$/);

    if (process.platform !== "win32") {
      const fileMode = (await stat(file)).mode & 0o777;
      const directoryMode = (await stat(join(root, "nested"))).mode & 0o777;
      assert.equal(fileMode, 0o600);
      assert.equal(directoryMode, 0o700);
    }
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("replaces a symlink instead of following it", { skip: process.platform === "win32" ? "symlinks need extra privileges" : false }, async () => {
  const root = await temporaryRoot();
  try {
    const target = join(root, "target.txt");
    const file = join(root, "yt-cookies.txt");
    await writeFile(target, "original", "utf8");
    await symlink(target, file);
    await saveCookiesTo(file, "# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tFALSE\t0\tsid\tnew");

    assert.equal(await readFile(target, "utf8"), "original");
    assert.equal((await stat(file)).isSymbolicLink(), false);
    assert.match(await readFile(file, "utf8"), /\tsid\tnew\n$/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("rejects text that is not a Netscape cookie file", async () => {
  const root = await temporaryRoot();
  try {
    await assert.rejects(() => saveCookiesTo(join(root, "cookies.txt"), "just a sentence"), /Netscape format/);
    await assert.rejects(() => saveCookiesTo(join(root, "cookies.txt"), "   "), /empty/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
