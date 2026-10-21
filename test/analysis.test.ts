import assert from "node:assert/strict";
import { test } from "node:test";

import { fromMoves } from "../src/analysis/dialogue.ts";
import { sanitizeMoves } from "../src/analysis/move.ts";
import type { Move } from "../src/analysis/move.ts";
import { checkDraft } from "../src/analysis/move-draft.ts";
import { detectProsody } from "../server/prosody.ts";
import { SAMPLE_RATE } from "../server/wav.ts";

function move(patch: Partial<Move> = {}): Move {
  return {
    start: 0,
    end: 1,
    speaker: "white",
    moveNumber: "1 Alice · A sharp exchange",
    moveType: "Best",
    title: "Alice takes the centre",
    explanation: "A calm, precise opening.",
    evalScore: 1,
    ...patch,
  };
}

test("reads the gist back out of a move number", () => {
  const dialogue = fromMoves([move()]);
  assert.equal(dialogue.moments[0]?.gist, "A sharp exchange");
  assert.equal(dialogue.moments[0]?.title, "Alice takes the centre");
});

test("keeps moves inside the clip when the times are at or past its end", () => {
  const clipped = sanitizeMoves([move({ start: 11, end: 12 })], 10);
  assert.equal(clipped.length, 1);
  assert.ok((clipped[0]?.start ?? 0) < (clipped[0]?.end ?? 0));
  assert.ok((clipped[0]?.end ?? 0) <= 10);
  assert.equal(clipped[0]?.start, 9);
  assert.equal(clipped[0]?.end, 10);

  const backwards = sanitizeMoves([move({ start: 4, end: 2 })], 10);
  assert.ok((backwards[0]?.end ?? 0) <= 10);
  assert.ok((backwards[0]?.end ?? 0) > (backwards[0]?.start ?? 0));
});

test("checkDraft refuses a move that cannot fit before the clip end", () => {
  const draft = {
    start: "9.8",
    end: "12",
    speaker: "white" as const,
    moveType: "Best" as const,
    evalScore: 0,
    gist: "",
    title: "A title",
    explanation: "An explanation.",
  };
  const result = checkDraft(draft, [], null, { white: "Alice", black: "Bob" }, 10);
  assert.equal(result.move, null);
  assert.ok(result.errors.some((message) => message.includes("does not fit")));
});

test("checkDraft accepts a move that fits and clips the end", () => {
  const draft = {
    start: "8",
    end: "12",
    speaker: "white" as const,
    moveType: "Best" as const,
    evalScore: 0,
    gist: "",
    title: "A title",
    explanation: "An explanation.",
  };
  const result = checkDraft(draft, [], null, { white: "Alice", black: "Bob" }, 10);
  assert.equal(result.move?.end, 10);
});

function tone(frequency: number, seconds: number): Float32Array {
  const length = Math.round(seconds * SAMPLE_RATE);
  const samples = new Float32Array(length);
  for (let index = 0; index < length; index += 1) {
    samples[index] = Math.sin((2 * Math.PI * frequency * index) / SAMPLE_RATE) * 0.4;
  }
  return samples;
}

function concat(parts: Float32Array[]): Float32Array {
  const length = parts.reduce((total, part) => total + part.length, 0);
  const out = new Float32Array(length);
  let offset = 0;
  for (const part of parts) {
    out.set(part, offset);
    offset += part.length;
  }
  return out;
}

const SEGMENT_SECONDS = 0.3;

test("detects a rising voice against the same voice's baseline", () => {
  const baseline = [190, 200, 210, 190, 200, 210, 190, 200, 210, 200];
  const high = [300, 300, 300];
  const parts = [...baseline, ...high].map((frequency) => tone(frequency, SEGMENT_SECONDS));
  const segments = parts.map((_, index) => ({
    start: index * SEGMENT_SECONDS,
    end: (index + 1) * SEGMENT_SECONDS,
    label: "A" as const,
  }));

  const result = detectProsody(concat(parts), segments);
  assert.equal(result.samples["A"], 13);
  const higher = result.events.filter((event) => event.kind === "higher");
  assert.ok(higher.length >= 3);
  assert.ok(higher.some((event) => Math.abs(event.start - 10 * SEGMENT_SECONDS) < 0.01));
});

test("does not emit an event for falling pitch (the old semantics)", () => {
  const baseline = [290, 300, 310, 290, 300, 310, 290, 300, 310, 300];
  const low = [130, 130, 130];
  const parts = [...baseline, ...low].map((frequency) => tone(frequency, SEGMENT_SECONDS));
  const segments = parts.map((_, index) => ({
    start: index * SEGMENT_SECONDS,
    end: (index + 1) * SEGMENT_SECONDS,
    label: "A" as const,
  }));

  const result = detectProsody(concat(parts), segments);
  assert.equal(result.events.filter((event) => event.kind === "higher").length, 0);
});

test("emits a pause event for a long gap between two lines", () => {
  const first = tone(200, SEGMENT_SECONDS);
  const second = tone(200, SEGMENT_SECONDS);
  const pcm = concat([first, new Float32Array(Math.round(1.7 * SAMPLE_RATE)), second]);
  const result = detectProsody(pcm, [
    { start: 0, end: SEGMENT_SECONDS, label: "A" },
    { start: 2, end: 2 + SEGMENT_SECONDS, label: "B" },
  ]);
  const pause = result.events.find((event) => event.kind === "pause");
  assert.ok(pause !== undefined);
  assert.equal(pause.start, 0.3);
  assert.equal(pause.end, 2);
});
