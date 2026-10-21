import assert from "node:assert/strict";
import { test } from "node:test";

import { parseProjectData } from "../src/analysis/project-format.ts";
import { DEFAULT_SCHEDULE_OPTIONS } from "../src/analysis/schedule.ts";
import { DEFAULT_NAMES } from "../src/names.ts";

interface Fixture {
  [key: string]: unknown;
}

function project(patch: Fixture = {}): Fixture {
  return {
    version: 1,
    moves: [
      {
        start: 0,
        end: 2,
        speaker: "white",
        moveNumber: "1 Alice · A sharp opening",
        moveType: "Best",
        title: "Alice opens sharply",
        explanation: "She takes the centre at once.",
        evalScore: 1.2,
      },
    ],
    transcript: [
      { id: 0, start: 0, end: 1, text: "Hello" },
      { id: 1, start: 1, end: 2, text: "Hi" },
    ],
    names: { white: "Alice", black: "Bob" },
    options: { ...DEFAULT_SCHEDULE_OPTIONS },
    speakers: null,
    people: null,
    cast: [],
    avatars: { white: null, black: null },
    sounds: [],
    styleId: "chess",
    llm: { provider: "ollama", model: "qwen" },
    ...patch,
  };
}

test("accepts a complete project document", () => {
  const data = parseProjectData(project());
  assert.equal(data.version, 1);
  assert.equal(data.moves.length, 1);
  assert.equal(data.moves[0]?.moveType, "Best");
  assert.equal(data.transcript[1]?.id, 1);
  assert.equal(data.names.white, "Alice");
  assert.equal(data.options.pauseOnMove, DEFAULT_SCHEDULE_OPTIONS.pauseOnMove);
  assert.equal(data.llm.provider, "ollama");
});

test("fills in fields that older project files do not have", () => {
  const data = parseProjectData({
    version: 1,
    moves: [],
    transcript: [],
    names: { white: "Alice", black: "Bob" },
  });
  assert.deepEqual(data.options, DEFAULT_SCHEDULE_OPTIONS);
  assert.equal(data.speakers, null);
  assert.equal(data.people, null);
  assert.deepEqual(data.cast, []);
  assert.deepEqual(data.avatars, { white: null, black: null });
  assert.deepEqual(data.sounds, []);
  assert.equal(data.styleId, "chess");
  assert.deepEqual(data.llm, { provider: "", model: "" });
});

test("rejects an unknown version and non-object documents", () => {
  assert.throws(() => parseProjectData(project({ version: 2 })), /version/);
  assert.throws(() => parseProjectData(null), /must be an object/);
  assert.throws(() => parseProjectData([]), /must be an object/);
});

test("rejects invalid transcript lines", () => {
  const wrongId = project({ transcript: [{ id: 4, start: 0, end: 1, text: "x" }] });
  assert.throws(() => parseProjectData(wrongId), /transcript\[0\]\.id must be 0/);

  const backwards = project({ transcript: [{ id: 0, start: 2, end: 1, text: "x" }] });
  assert.throws(() => parseProjectData(backwards), /must be after the start/);

  const missingText = project({ transcript: [{ id: 0, start: 0, end: 1 }] });
  assert.throws(() => parseProjectData(missingText), /text must be a string/);
});

test("rejects invalid moves", () => {
  const base = project();
  const first = (base["moves"] as Array<Record<string, unknown>>)[0] ?? {};
  assert.throws(
    () => parseProjectData(project({ moves: [{ ...first, moveType: "Excellent" }] })),
    /moveType/,
  );
  assert.throws(
    () => parseProjectData(project({ moves: [{ ...first, start: 3, end: 1 }] })),
    /must be after the start/,
  );
  assert.throws(
    () => parseProjectData(project({ moves: [{ ...first, evalScore: 12 }] })),
    /evalScore/,
  );
  assert.throws(
    () => parseProjectData(project({ moves: [{ ...first, start: Number.POSITIVE_INFINITY }] })),
    /start/,
  );
});

test("validates speaker hints against the transcript", () => {
  const data = parseProjectData(
    project({
      speakers: {
        hints: [
          { label: "A", confidence: 0.9 },
          { label: "B", confidence: 0.8 },
        ],
        sides: { A: "white", B: "black" },
        reliability: 0.7,
      },
    }),
  );
  assert.equal(data.speakers?.hints.length, 2);

  assert.throws(
    () =>
      parseProjectData(
        project({
          speakers: { hints: [{ label: "A", confidence: 0.9 }], sides: { A: "white", B: "black" }, reliability: 0.7 },
        }),
      ),
    /one hint per transcript line/,
  );
  assert.throws(
    () =>
      parseProjectData(
        project({
          speakers: {
            hints: [
              { label: "C", confidence: 0.9 },
              { label: "B", confidence: 0.8 },
            ],
            sides: { A: "white", B: "black" },
            reliability: 0.7,
          },
        }),
      ),
    /label/,
  );
});

test("keeps avatars local and rejects external image URLs", () => {
  const avatar = "data:image/png;base64,iVBORw0KGgo=";
  const data = parseProjectData(project({ avatars: { white: avatar, black: null } }));
  assert.equal(data.avatars.white, avatar);

  assert.throws(
    () => parseProjectData(project({ avatars: { white: "https://evil.example/a.png", black: null } })),
    /data URL/,
  );
});

test("normalises unknown sound kinds and rejects bad event times", () => {
  const data = parseProjectData(
    project({
      sounds: [
        { id: 1, at: 1, end: null, kind: "explosion", label: "", source: "model", peak: 0.4, reliable: true },
        { at: 2, end: 3, kind: "laughter", label: "laugh", source: "user" },
      ],
    }),
  );
  assert.equal(data.sounds[0]?.kind, "other");
  assert.equal(data.sounds[1]?.id, 2);
  assert.equal(data.sounds[1]?.end, 3);

  assert.throws(
    () => parseProjectData(project({ sounds: [{ at: 2, end: 1 }] })),
    /sounds\[0\]\.end/,
  );
});

test("validates options and falls back to known style presets", () => {
  const data = parseProjectData(project({ styleId: "future-style" }));
  assert.equal(data.styleId, "chess");

  assert.throws(
    () => parseProjectData(project({ options: { ...DEFAULT_SCHEDULE_OPTIONS, format: "square" } })),
    /options\.format/,
  );
  assert.throws(
    () => parseProjectData(project({ options: { ...DEFAULT_SCHEDULE_OPTIONS, sfxVolume: 4 } })),
    /sfxVolume/,
  );
  assert.throws(
    () => parseProjectData(project({ options: { ...DEFAULT_SCHEDULE_OPTIONS, pauseOnMove: "yes" } })),
    /pauseOnMove/,
  );
});

test("applies defaults for absent names and unknown options fields", () => {
  const data = parseProjectData(
    project({ names: undefined, options: { ...DEFAULT_SCHEDULE_OPTIONS, futureOption: true } }),
  );
  assert.deepEqual(data.names, DEFAULT_NAMES);
  assert.equal(data.options.pauseOnMove, DEFAULT_SCHEDULE_OPTIONS.pauseOnMove);
});
