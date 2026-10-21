import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, test } from "node:test";

const scratch = mkdtempSync(join(tmpdir(), "cdm-search-"));
const fakeBin = join(scratch, "fake-yt-dlp.mjs");
writeFileSync(
  fakeBin,
  `import { appendFileSync } from "node:fs";

const args = process.argv.slice(2);
if (args.includes("--version")) {
  console.log("fake yt-dlp 1.0");
  process.exit(0);
}
const log = process.env.FAKE_ARGS_LOG;
if (log) appendFileSync(log, args.join("\\n") + "\\n");
if (args.some((arg) => arg.includes("unsupported"))) {
  console.error("ERROR: Unsupported URL: https://example.test/unsupported");
  process.exit(2);
}
console.log(JSON.stringify({ id: "abc123", title: "A chess video", channel: "A channel", duration: 61, thumbnail: "https://img.test/a.jpg" }));
console.log(JSON.stringify({ id: "def456", title: "Another chess video", uploader: "Uploader", duration: null }));
`,
  "utf8",
);
process.env["YTDLP_BIN"] = fakeBin;

const searchModule = await import("../server/search.ts");

after(async () => {
  await rm(scratch, { recursive: true, force: true });
});

test("parses yt-dlp JSON hits and builds watch URLs", async () => {
  const hits = await searchModule.search("chess blunder");
  assert.equal(hits.length, 2);
  assert.equal(hits[0]?.id, "abc123");
  assert.equal(hits[0]?.url, "https://www.youtube.com/watch?v=abc123");
  assert.equal(hits[0]?.channel, "A channel");
  assert.equal(hits[0]?.duration, 61);
  assert.equal(hits[1]?.channel, "Uploader");
  assert.equal(hits[1]?.duration, null);
});

test("passes a channel search as a YouTube search URL", async () => {
  const log = join(scratch, "args.log");
  process.env["FAKE_ARGS_LOG"] = log;
  try {
    await searchModule.search("@handle words here");
    const args = (await readFile(log, "utf8")).split(/\r?\n/);
    assert.ok(args.includes("https://www.youtube.com/@handle/search?query=words%20here"));
    assert.ok(args.includes("--playlist-end"));
    assert.ok(args.includes("--dump-json"));
  } finally {
    delete process.env["FAKE_ARGS_LOG"];
  }
});

test("reports yt-dlp's own error line and rejects a too-long query", async () => {
  await assert.rejects(() => searchModule.search("unsupported"), /Unsupported URL/);
  await assert.rejects(() => searchModule.search("x".repeat(300)), /longer than 200/);
});
