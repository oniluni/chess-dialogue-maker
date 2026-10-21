import { createRequire } from "node:module";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { join } from "node:path";

const require = createRequire(import.meta.url);
const ort = require("onnxruntime-node") as typeof import("onnxruntime-node");

const MODELS = join(import.meta.dirname, "..", ".cache", "models");
const MODEL = join(MODELS, "yamnet.onnx");
const CLASSES = join(MODELS, "yamnet_class_map.csv");

/** The model was trained on 16 kHz mono audio and one patch covers 0.96 s. */
const SAMPLE_RATE = 16_000;
const HOP = 0.48;
const WINDOW = 0.96;

interface Kind {
  label: string;
  classes: string[];
  threshold: number;
  certainAt: number;
  minFrames: number;
}

/** Ported from the old `sounds.py`: class-name prefixes, thresholds and minimum lengths. */
const KINDS: Record<string, Kind> = {
  laughter: {
    label: "Laughter",
    classes: ["Laughter", "Baby laughter", "Giggle", "Snicker", "Belly laugh", "Chuckle"],
    threshold: 0.25,
    certainAt: 0.55,
    minFrames: 1,
  },
  crying: {
    label: "Crying",
    classes: ["Crying", "Whimper", "Wail", "Baby cry"],
    threshold: 0.25,
    certainAt: 0.5,
    minFrames: 1,
  },
  scream: {
    label: "Scream",
    classes: ["Screaming", "Shout", "Yell", "Bellow", "Whoop", "Children shouting"],
    threshold: 0.22,
    certainAt: 0.5,
    minFrames: 1,
  },
  whisper: { label: "Whisper", classes: ["Whispering"], threshold: 0.3, certainAt: 0.6, minFrames: 1 },
  sigh: { label: "Sigh", classes: ["Sigh"], threshold: 0.25, certainAt: 0.5, minFrames: 1 },
  gasp: { label: "Gasp", classes: ["Gasp", "Pant"], threshold: 0.25, certainAt: 0.5, minFrames: 1 },
  groan: { label: "Groan", classes: ["Groan", "Grunt"], threshold: 0.3, certainAt: 0.55, minFrames: 1 },
  cough: {
    label: "Cough",
    classes: ["Cough", "Throat clearing", "Sneeze", "Sniff"],
    threshold: 0.35,
    certainAt: 0.6,
    minFrames: 1,
  },
  singing: {
    label: "Singing",
    classes: ["Singing", "Humming", "Whistling", "Rapping", "Choir"],
    threshold: 0.3,
    certainAt: 0.55,
    minFrames: 1,
  },
  music: {
    label: "Music",
    classes: [
      "Music",
      "Musical instrument",
      "Background music",
      "Soundtrack",
      "Theme music",
      "Song",
      "Orchestra",
      "Piano",
      "Guitar",
      "Drum",
      "Synthesizer",
      "Violin",
      "String section",
    ],
    threshold: 0.35,
    certainAt: 0.55,
    minFrames: 3,
  },
  applause: {
    label: "Applause",
    classes: ["Applause", "Clapping", "Cheering"],
    threshold: 0.3,
    certainAt: 0.55,
    minFrames: 1,
  },
  crowd: {
    label: "Crowd",
    classes: ["Crowd", "Chatter", "Hubbub", "Children playing"],
    threshold: 0.3,
    certainAt: 0.6,
    minFrames: 2,
  },
  door: { label: "Door", classes: ["Door", "Slam", "Knock", "Sliding door"], threshold: 0.3, certainAt: 0.55, minFrames: 1 },
  phone: { label: "Phone", classes: ["Telephone", "Ringtone"], threshold: 0.3, certainAt: 0.55, minFrames: 1 },
  siren: {
    label: "Siren",
    classes: [
      "Siren",
      "Civil defense siren",
      "Police car",
      "Ambulance",
      "Fire engine",
      "Alarm",
      "Buzzer",
      "Car alarm",
    ],
    threshold: 0.3,
    certainAt: 0.55,
    minFrames: 1,
  },
  shot: {
    label: "Shot",
    classes: ["Gunshot", "Machine gun", "Explosion", "Boom", "Fusillade", "Artillery fire", "Bang"],
    threshold: 0.25,
    certainAt: 0.5,
    minFrames: 1,
  },
  glass: {
    label: "Glass",
    classes: ["Glass", "Shatter", "Breaking", "Smash", "Chink"],
    threshold: 0.3,
    certainAt: 0.55,
    minFrames: 1,
  },
  footsteps: { label: "Footsteps", classes: ["Walk, footsteps", "Run"], threshold: 0.35, certainAt: 0.6, minFrames: 1 },
  vehicle: {
    label: "Vehicle",
    classes: ["Vehicle", "Car", "Motor vehicle", "Engine", "Traffic noise", "Truck", "Motorcycle"],
    threshold: 0.35,
    certainAt: 0.6,
    minFrames: 2,
  },
  weather: { label: "Weather", classes: ["Rain", "Thunder", "Wind"], threshold: 0.35, certainAt: 0.6, minFrames: 2 },
  animal: {
    label: "Animal",
    classes: ["Dog", "Bark", "Cat", "Meow", "Bird", "Growling", "Roar"],
    threshold: 0.35,
    certainAt: 0.6,
    minFrames: 1,
  },
  tv: { label: "TV", classes: ["Television", "Radio"], threshold: 0.4, certainAt: 0.6, minFrames: 2 },
};

export interface SoundDetection {
  kind: string;
  label: string;
  start: number;
  end: number;
  peak: number;
  reliable: boolean;
}

/** The mood classes the old project appended to a music event. */
const MUSIC_MOODS: Record<string, string> = {
  "Sad music": "sad",
  "Happy music": "happy",
  "Tender music": "tender",
  "Exciting music": "tense",
  "Angry music": "angry",
  "Scary music": "eerie",
};

interface Loaded {
  session: import("onnxruntime-node").InferenceSession;
  classes: string[];
}

let loaded: Promise<Loaded> | null = null;

/** Minimal CSV field reader: the class map quotes names that contain commas. */
function csvFields(line: string): string[] {
  const fields: string[] = [];
  let current = "";
  let quoted = false;
  for (let index = 0; index < line.length; index += 1) {
    const character = line[index] ?? "";
    if (quoted) {
      if (character === '"') {
        if (line[index + 1] === '"') {
          current += '"';
          index += 1;
        } else {
          quoted = false;
        }
      } else {
        current += character;
      }
    } else if (character === '"') {
      quoted = true;
    } else if (character === ",") {
      fields.push(current);
      current = "";
    } else {
      current += character;
    }
  }
  fields.push(current);
  return fields;
}

export function parseClassMap(csv: string): string[] {
  return csv
    .split(/\r?\n/)
    .slice(1)
    .filter((line) => line.trim() !== "")
    .map((line) => csvFields(line)[2] ?? "");
}

async function load(): Promise<Loaded> {
  if (!existsSync(MODEL) || !existsSync(CLASSES)) {
    throw new Error("the YAMNet model or its class map is missing from .cache/models");
  }
  const classes = parseClassMap(await readFile(CLASSES, "utf8"));
  const session = await ort.InferenceSession.create(MODEL);
  return { session, classes };
}

function model(): Promise<Loaded> {
  loaded ??= load();
  loaded.catch(() => {
    loaded = null;
  });
  return loaded;
}

export function indicesOf(classes: string[], prefixes: string[]): number[] {
  const indices: number[] = [];
  for (const [index, name] of classes.entries()) {
    if (prefixes.some((prefix) => name === prefix || name.startsWith(`${prefix},`) || name.startsWith(`${prefix} `))) {
      indices.push(index);
    }
  }
  return indices;
}
/** YAMNet scores for each 0.48 s frame; the ONNX export takes the raw waveform. */
export async function classify(waveform: Float32Array, duration: number): Promise<SoundDetection[]> {
  const { session, classes } = await model();
  const input = new Float32Array(waveform);
  const minimal = Math.round(WINDOW * SAMPLE_RATE);
  const padded = input.length >= minimal ? input : new Float32Array(minimal);

  if (padded !== input) padded.set(input);

  const name = session.inputNames[0] ?? "waveform";
  const output = await session.run({ [name]: new ort.Tensor("float32", padded, [padded.length]) });
  const scores = Object.values(output)
    .map((tensor) => (tensor.data instanceof Float32Array ? tensor.data : null))
    .find((data): data is Float32Array => data !== null && data.length % 521 === 0);
  if (scores === undefined) throw new Error("the sound model returned no score matrix");

  const frames = Math.floor(scores.length / 521);
  const detections: SoundDetection[] = [];

  const value = (frame: number, index: number): number => scores[frame * 521 + index] ?? 0;

  for (const [key, kind] of Object.entries(KINDS)) {
    const indices = indicesOf(classes, kind.classes);
    if (indices.length === 0) continue;

    const active: boolean[] = [];
    const peaks: number[] = [];
    for (let frame = 0; frame < frames; frame += 1) {
      let best = 0;
      for (const index of indices) best = Math.max(best, value(frame, index));
      active.push(best >= kind.threshold);
      peaks.push(best);
    }

    let start = 0;
    while (start < frames) {
      if (active[start] !== true) {
        start += 1;
        continue;
      }
      let end = start;
      while (end + 1 < frames && (active[end + 1] === true || (end + 2 < frames && active[end + 2] === true))) end += 1;

      if (end - start + 1 >= kind.minFrames) {
        const startTime = Math.min(duration, start * HOP);
        const endTime = Math.min(duration, end * HOP + WINDOW);
        if (endTime > startTime) {
          const peak = Math.max(...peaks.slice(start, end + 1));
          let label = kind.label;
          if (key === "music") {
            let mood = "";
            let best = 0.15;
            for (const [name, moodLabel] of Object.entries(MUSIC_MOODS)) {
              const moodIndex = indicesOf(classes, [name])[0];
              if (moodIndex === undefined) continue;
              for (let frame = start; frame <= end; frame += 1) {
                const score = value(frame, moodIndex);
                if (score > best) {
                  best = score;
                  mood = moodLabel;
                }
              }
            }
            if (mood !== "") label = `${kind.label} · ${mood}`;
          }
          detections.push({
            kind: key,
            label,
            start: Math.round(startTime * 100) / 100,
            end: Math.round(endTime * 100) / 100,
            peak: Math.round(peak * 100) / 100,
            reliable: peak >= kind.certainAt,
          });
        }
      }
      start = end + 1;
    }
  }

  return detections.sort((left, right) => left.start - right.start);
}

export const SOUND_MODEL = "yamnet.onnx";
