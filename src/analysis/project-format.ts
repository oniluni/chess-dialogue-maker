import type { FacesResult } from "../../shared/people.ts";
import type { SpeakerHint } from "../../shared/speakers.ts";
import { DEFAULT_NAMES } from "../names.ts";
import type { Names } from "../names.ts";
import { PAUSE_SETS, TYPING_SETS } from "../audio/sfx.ts";
import { STYLE_PRESETS } from "../render/style.ts";
import type { CastEntry, LlmChoice, SpeakerHints } from "../session.ts";
import { MOVE_TYPES } from "./move.ts";
import type { Move, MoveType, Side } from "./move.ts";
import { DEFAULT_SCHEDULE_OPTIONS } from "./schedule.ts";
import type { ScheduleOptions } from "./schedule.ts";
import { isSoundKind } from "./sound-events.ts";
import type { SoundEvent } from "./sound-events.ts";
import type { TranscriptSegment } from "./transcript.ts";

/** Everything the document holds; the clip itself is re-opened by hand. */
export interface ProjectData {
  version: 1;
  moves: Move[];
  transcript: TranscriptSegment[];
  names: Names;
  options: ScheduleOptions;
  speakers: SpeakerHints | null;
  people: FacesResult | null;
  cast: CastEntry[];
  avatars: Record<Side, string | null>;
  sounds: SoundEvent[];
  styleId: string;
  llm: LlmChoice;
}

const MOVE_TYPES_SET: ReadonlySet<string> = new Set(MOVE_TYPES);
const SIDES: ReadonlySet<string> = new Set(["white", "black"]);
const FORMATS: ReadonlySet<string> = new Set(["vertical", "original", "landscape"]);
const TYPING_IDS: ReadonlySet<string> = new Set(TYPING_SETS.map((set) => set.id));
const PAUSE_IDS: ReadonlySet<string> = new Set(PAUSE_SETS.map((set) => set.id));
const STYLE_IDS: ReadonlySet<string> = new Set(STYLE_PRESETS.map((preset) => preset.id));
const AVATAR = /^data:image\/(?:png|jpeg|webp);base64,[A-Za-z0-9+/]+={0,2}$/;
const MAX_AVATAR = 3_000_000;
const MAX_TEXT = 20_000;

function invalid(path: string, expected: string): never {
  throw new Error(`The project is invalid: ${path} ${expected}.`);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asRecord(value: unknown, path: string): Record<string, unknown> {
  if (!isRecord(value)) invalid(path, "must be an object");
  return value;
}

function asArray(value: unknown, path: string): unknown[] {
  if (!Array.isArray(value)) invalid(path, "must be an array");
  return value;
}

function asText(value: unknown, path: string, max = MAX_TEXT): string {
  if (typeof value !== "string") invalid(path, "must be a string");
  if (value.length > max) invalid(path, `must be at most ${max} characters`);
  return value;
}

function asNumber(value: unknown, path: string, min: number, max: number): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < min || value > max) {
    invalid(path, `must be a number from ${min} to ${max}`);
  }
  return value;
}

function asInteger(value: unknown, path: string, min: number, max: number): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < min || value > max) {
    invalid(path, `must be an integer from ${min} to ${max}`);
  }
  return value;
}

function asBoolean(value: unknown, path: string, fallback: boolean): boolean {
  if (value === undefined) return fallback;
  if (typeof value !== "boolean") invalid(path, "must be true or false");
  return value;
}

function asSide(value: unknown, path: string): Side {
  if (typeof value !== "string" || !SIDES.has(value)) invalid(path, 'must be "white" or "black"');
  return value as Side;
}

function parseMove(value: unknown, path: string): Move {
  const move = asRecord(value, path);
  const moveType = asText(move["moveType"], `${path}.moveType`, 40);
  if (!MOVE_TYPES_SET.has(moveType)) invalid(`${path}.moveType`, "is not a known verdict");
  const start = asNumber(move["start"], `${path}.start`, 0, Number.MAX_SAFE_INTEGER);
  const end = asNumber(move["end"], `${path}.end`, 0, Number.MAX_SAFE_INTEGER);
  if (end <= start) invalid(`${path}.end`, "must be after the start");
  return {
    start,
    end,
    speaker: asSide(move["speaker"], `${path}.speaker`),
    moveNumber: asText(move["moveNumber"], `${path}.moveNumber`, 500),
    moveType: moveType as MoveType,
    title: asText(move["title"], `${path}.title`, 2_000),
    explanation: asText(move["explanation"], `${path}.explanation`, 10_000),
    evalScore: asNumber(move["evalScore"], `${path}.evalScore`, -10, 10),
  };
}

function parseTranscript(value: unknown): TranscriptSegment[] {
  const list = asArray(value, "transcript");
  const segments = list.map((item, index): TranscriptSegment => {
    const path = `transcript[${index}]`;
    const segment = asRecord(item, path);
    const id = asInteger(segment["id"], `${path}.id`, 0, Number.MAX_SAFE_INTEGER);
    if (id !== index) invalid(`${path}.id`, `must be ${index}`);
    const start = asNumber(segment["start"], `${path}.start`, 0, Number.MAX_SAFE_INTEGER);
    const end = asNumber(segment["end"], `${path}.end`, 0, Number.MAX_SAFE_INTEGER);
    if (end <= start) invalid(`${path}.end`, "must be after the start");
    return { id, start, end, text: asText(segment["text"], `${path}.text`) };
  });
  return segments;
}

function parseNames(value: unknown): Names {
  if (value === undefined) return { ...DEFAULT_NAMES };
  const names = asRecord(value, "names");
  return {
    white: names["white"] === undefined ? DEFAULT_NAMES.white : asText(names["white"], "names.white", 200),
    black: names["black"] === undefined ? DEFAULT_NAMES.black : asText(names["black"], "names.black", 200),
  };
}

function parseOptions(value: unknown): ScheduleOptions {
  const options: ScheduleOptions = { ...DEFAULT_SCHEDULE_OPTIONS };
  if (value === undefined) return options;
  const record = asRecord(value, "options");

  options.pauseOnMove = asBoolean(record["pauseOnMove"], "options.pauseOnMove", options.pauseOnMove);
  options.sfxEnabled = asBoolean(record["sfxEnabled"], "options.sfxEnabled", options.sfxEnabled);
  options.sfxPause = asBoolean(record["sfxPause"], "options.sfxPause", options.sfxPause);
  options.sfxUnpause = asBoolean(record["sfxUnpause"], "options.sfxUnpause", options.sfxUnpause);
  options.sfxTyping = asBoolean(record["sfxTyping"], "options.sfxTyping", options.sfxTyping);
  options.subtitles = asBoolean(record["subtitles"], "options.subtitles", options.subtitles);

  if (record["typingCps"] !== undefined) {
    options.typingCps = asNumber(record["typingCps"], "options.typingCps", 0.1, 1_000);
  }
  if (record["readHold"] !== undefined) {
    options.readHold = asNumber(record["readHold"], "options.readHold", 0, 600);
  }
  if (record["sfxVolume"] !== undefined) {
    options.sfxVolume = asNumber(record["sfxVolume"], "options.sfxVolume", 0, 1);
  }
  if (record["sfxTypingSet"] !== undefined) {
    const id = asText(record["sfxTypingSet"], "options.sfxTypingSet", 40);
    options.sfxTypingSet = TYPING_IDS.has(id) ? id : DEFAULT_SCHEDULE_OPTIONS.sfxTypingSet;
  }
  if (record["sfxPauseSet"] !== undefined) {
    const id = asText(record["sfxPauseSet"], "options.sfxPauseSet", 40);
    options.sfxPauseSet = PAUSE_IDS.has(id) ? id : DEFAULT_SCHEDULE_OPTIONS.sfxPauseSet;
  }
  if (record["format"] !== undefined) {
    const format = asText(record["format"], "options.format", 20);
    if (!FORMATS.has(format)) invalid("options.format", "must be vertical, original or landscape");
    options.format = format as ScheduleOptions["format"];
  }
  return options;
}

function parseSpeakers(value: unknown, transcript: TranscriptSegment[]): SpeakerHints | null {
  if (value === undefined || value === null) return null;
  const speakers = asRecord(value, "speakers");
  const rawHints = asArray(speakers["hints"], "speakers.hints");
  if (rawHints.length !== transcript.length) {
    invalid("speakers.hints", "must have exactly one hint per transcript line");
  }
  const hints = rawHints.map((item, index): SpeakerHint => {
    const path = `speakers.hints[${index}]`;
    const hint = asRecord(item, path);
    const label = hint["label"];
    if (label !== null && label !== "A" && label !== "B") invalid(`${path}.label`, 'must be "A", "B" or null');
    return {
      label: label === "A" ? "A" : label === "B" ? "B" : null,
      confidence: asNumber(hint["confidence"], `${path}.confidence`, 0, 1),
    };
  });
  const sides = asRecord(speakers["sides"], "speakers.sides");
  return {
    hints,
    sides: {
      A: asSide(sides["A"], "speakers.sides.A"),
      B: asSide(sides["B"], "speakers.sides.B"),
    },
    reliability: asNumber(speakers["reliability"], "speakers.reliability", 0, 1),
  };
}

function parsePeople(value: unknown): FacesResult | null {
  if (value === undefined || value === null) return null;
  const people = asRecord(value, "people");
  const rawPersons = asArray(people["persons"], "people.persons");
  const persons = rawPersons.map((item, index) => {
    const path = `people.persons[${index}]`;
    const person = asRecord(item, path);
    const preview = asRecord(person["preview"], `${path}.preview`);
    return {
      id: asInteger(person["id"], `${path}.id`, 0, Number.MAX_SAFE_INTEGER),
      frames: asInteger(person["frames"], `${path}.frames`, 0, Number.MAX_SAFE_INTEGER),
      share: asNumber(person["share"], `${path}.share`, 0, 1),
      preview: {
        time: asNumber(preview["time"], `${path}.preview.time`, 0, Number.MAX_SAFE_INTEGER),
        x: asNumber(preview["x"], `${path}.preview.x`, -10, 10),
        y: asNumber(preview["y"], `${path}.preview.y`, -10, 10),
        width: asNumber(preview["width"], `${path}.preview.width`, 0, 10),
        height: asNumber(preview["height"], `${path}.preview.height`, 0, 10),
      },
    };
  });

  const rawFrames = asArray(people["frames"], "people.frames");
  const frames = rawFrames.map((item, index) => {
    const path = `people.frames[${index}]`;
    const frame = asRecord(item, path);
    const boxes = asArray(frame["boxes"], `${path}.boxes`).map((rawBox, boxIndex) => {
      const boxPath = `${path}.boxes[${boxIndex}]`;
      const box = asRecord(rawBox, boxPath);
      return {
        x: asNumber(box["x"], `${boxPath}.x`, -10, 10),
        y: asNumber(box["y"], `${boxPath}.y`, -10, 10),
        width: asNumber(box["width"], `${boxPath}.width`, 0, 10),
        height: asNumber(box["height"], `${boxPath}.height`, 0, 10),
        person: asInteger(box["person"], `${boxPath}.person`, 0, Number.MAX_SAFE_INTEGER),
      };
    });
    return { time: asNumber(frame["time"], `${path}.time`, 0, Number.MAX_SAFE_INTEGER), boxes };
  });
  return { persons, frames };
}

function parseCast(value: unknown): CastEntry[] {
  if (value === undefined) return [];
  const list = asArray(value, "cast");
  return list.map((item, index) => {
    const path = `cast[${index}]`;
    const entry = asRecord(item, path);
    const side = entry["side"];
    return {
      person: asInteger(entry["person"], `${path}.person`, 0, Number.MAX_SAFE_INTEGER),
      name: asText(entry["name"], `${path}.name`, 200),
      side: side === null || side === undefined ? null : asSide(side, `${path}.side`),
    };
  });
}

function parseAvatars(value: unknown): Record<Side, string | null> {
  const avatars: Record<Side, string | null> = { white: null, black: null };
  if (value === undefined) return avatars;
  const record = asRecord(value, "avatars");
  for (const side of ["white", "black"] as const) {
    const avatar = record[side];
    if (avatar === undefined || avatar === null) continue;
    if (typeof avatar !== "string" || avatar.length > MAX_AVATAR || !AVATAR.test(avatar)) {
      invalid(`avatars.${side}`, "must be a PNG, JPEG or WebP data URL");
    }
    avatars[side] = avatar;
  }
  return avatars;
}

function parseSounds(value: unknown): SoundEvent[] {
  if (value === undefined) return [];
  const list = asArray(value, "sounds");
  return list.map((item, index) => {
    const path = `sounds[${index}]`;
    const event = asRecord(item, path);
    const at = asNumber(event["at"], `${path}.at`, 0, Number.MAX_SAFE_INTEGER);
    const rawEnd = event["end"];
    let end: number | null = null;
    if (rawEnd !== null && rawEnd !== undefined) {
      end = asNumber(rawEnd, `${path}.end`, at, Number.MAX_SAFE_INTEGER);
    }
    const kind = typeof event["kind"] === "string" && isSoundKind(event["kind"]) ? event["kind"] : "other";
    const source = event["source"] === "model" ? "model" : "user";
    return {
      id: event["id"] === undefined ? index + 1 : asInteger(event["id"], `${path}.id`, 0, Number.MAX_SAFE_INTEGER),
      at,
      end,
      kind,
      label: event["label"] === undefined ? "" : asText(event["label"], `${path}.label`, 500),
      source,
      peak: event["peak"] === undefined ? 1 : asNumber(event["peak"], `${path}.peak`, 0, 1),
      reliable: asBoolean(event["reliable"], `${path}.reliable`, source === "user"),
    };
  });
}

function parseLlm(value: unknown): LlmChoice {
  if (value === undefined) return { provider: "", model: "" };
  const record = asRecord(value, "llm");
  return {
    provider: record["provider"] === undefined ? "" : asText(record["provider"], "llm.provider", 100),
    model: record["model"] === undefined ? "" : asText(record["model"], "llm.model", 200),
  };
}

/**
 * Validates a project read from the backend or a file. TypeScript types stop at the JSON
 * boundary, so every field the editor later trusts is checked here; absent fields that were
 * added after the first release fall back to their defaults.
 */
export function parseProjectData(value: unknown): ProjectData {
  const project = asRecord(value, "project");
  if (project["version"] !== 1) invalid("version", "must be 1");

  const transcript = parseTranscript(project["transcript"]);
  const moves = asArray(project["moves"], "moves").map((move, index) => parseMove(move, `moves[${index}]`));
  const style = project["styleId"] === undefined ? "chess" : asText(project["styleId"], "styleId", 40);

  return {
    version: 1,
    moves,
    transcript,
    names: parseNames(project["names"]),
    options: parseOptions(project["options"]),
    speakers: parseSpeakers(project["speakers"], transcript),
    people: parsePeople(project["people"]),
    cast: parseCast(project["cast"]),
    avatars: parseAvatars(project["avatars"]),
    sounds: parseSounds(project["sounds"]),
    styleId: STYLE_IDS.has(style) ? style : "chess",
    llm: parseLlm(project["llm"]),
  };
}
