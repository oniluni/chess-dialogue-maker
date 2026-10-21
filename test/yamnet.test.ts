import assert from "node:assert/strict";
import { test } from "node:test";

import { indicesOf, parseClassMap } from "../server/yamnet.ts";

const CSV = [
  "index,mid,display_name",
  "0,/m/09x0r,Speech",
  '19,/m/0463cq4,"Crying, sobbing"',
  '20,/t/dd00002,"Baby cry, infant cry"',
  '48,/m/07pbtc8,"Walk, footsteps"',
  '65,/m/07qfr4h,"Hubbub, speech noise, speech babble"',
  "132,/m/04rlf,Music",
].join("\r\n");

test("parses quoted display names instead of splitting them at commas", () => {
  assert.deepEqual(parseClassMap(CSV), [
    "Speech",
    "Crying, sobbing",
    "Baby cry, infant cry",
    "Walk, footsteps",
    "Hubbub, speech noise, speech babble",
    "Music",
  ]);
});

test("matches class prefixes against the full quoted names", () => {
  const classes = parseClassMap(CSV);
  assert.deepEqual(indicesOf(classes, ["Crying"]), [1]);
  assert.deepEqual(indicesOf(classes, ["Baby cry"]), [2]);
  assert.deepEqual(indicesOf(classes, ["Walk, footsteps"]), [3]);
  assert.deepEqual(indicesOf(classes, ["Hubbub"]), [4]);
  assert.deepEqual(indicesOf(classes, ["Speech"]), [0]);
  assert.deepEqual(indicesOf(classes, ["Not a class"]), []);
});

test("keeps escaped quotes and empty fields intact", () => {
  const parsed = parseClassMap('index,mid,display_name\n0,/m/x,"He said ""hi"", then left"\n');
  assert.deepEqual(parsed, ['He said "hi", then left']);
});
