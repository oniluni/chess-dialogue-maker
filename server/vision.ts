import { basename, join } from "node:path";

import { ensureModel } from "./model-download.ts";

const MODELS = join(import.meta.dirname, "..", ".cache", "models");
const DETECTOR = join(MODELS, "face_detection_yunet_2023mar.onnx");
const RECOGNIZER = join(MODELS, "face_recognition_sface_2021dec.onnx");
const URLS: Record<string, string> = {
  "face_detection_yunet_2023mar.onnx":
    "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
  "face_recognition_sface_2021dec.onnx":
    "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
};

const INPUT = 640;
const STRIDES = [8, 16, 32] as const;
const THRESHOLD = 0.6;
const IOU = 0.3;
const MIN_FACE = 0.12;
const SAME = 0.45;
const MAX_PERSONS = 4;
const MAX_FACES = 6;
const TEMPLATE: Array<[number, number]> = [
  [38.2946, 51.6963],
  [73.5318, 51.5014],
  [56.0252, 71.7366],
  [41.5493, 92.3655],
  [70.7299, 92.2041],
];

export interface FrameInput {
  time: number;
  width: number;
  height: number;
  rgb: Uint8Array;
}
export interface FaceBox {
  x: number;
  y: number;
  width: number;
  height: number;
  person: number;
}
export interface FrameFaces {
  time: number;
  boxes: FaceBox[];
}
export interface PersonInfo {
  id: number;
  frames: number;
  share: number;
  preview: { time: number; x: number; y: number; width: number; height: number };
}
export interface FacesResult {
  persons: PersonInfo[];
  frames: FrameFaces[];
}
interface Detection {
  x: number;
  y: number;
  width: number;
  height: number;
  score: number;
  landmarks: number[];
  embedding: Float32Array | null;
}
interface Model {
  ort: typeof import("onnxruntime-node");
  session: import("onnxruntime-node").InferenceSession;
}

async function load(path: string): Promise<Model> {
  const url = URLS[basename(path)];
  if (url === undefined) throw new Error("no model url");
  await ensureModel(url, path);
  const ort = await import("onnxruntime-node");
  return { ort, session: await ort.InferenceSession.create(path) };
}

let detectorModel: Promise<Model> | null = null;
let recognizerModel: Promise<Model> | null = null;
function detector(): Promise<Model> {
  detectorModel ??= load(DETECTOR);
  detectorModel.catch(() => {
    detectorModel = null;
  });
  return detectorModel;
}
function recognizer(): Promise<Model> {
  recognizerModel ??= load(RECOGNIZER);
  recognizerModel.catch(() => {
    recognizerModel = null;
  });
  return recognizerModel;
}

async function run(model: Model, input: Float32Array, dims: number[]): Promise<Map<string, Float32Array>> {
  const name = model.session.inputNames[0] ?? "input";
  const output = await model.session.run({ [name]: new model.ort.Tensor("float32", input, dims) });
  const map = new Map<string, Float32Array>();
  for (const [key, tensor] of Object.entries(output)) {
    if (tensor.data instanceof Float32Array) map.set(key, tensor.data);
  }
  return map;
}

function sample(source: Uint8Array, width: number, height: number, x: number, y: number): [number, number, number] {
  const x0 = Math.max(0, Math.min(width - 1, Math.floor(x)));
  const y0 = Math.max(0, Math.min(height - 1, Math.floor(y)));
  const x1 = Math.min(width - 1, x0 + 1);
  const y1 = Math.min(height - 1, y0 + 1);
  const fx = Math.max(0, Math.min(1, x - x0));
  const fy = Math.max(0, Math.min(1, y - y0));
  const at = (px: number, py: number, channel: number): number => source[(py * width + px) * 3 + channel] ?? 0;
  const out: [number, number, number] = [0, 0, 0];
  for (let channel = 0; channel < 3; channel += 1) {
    const top = at(x0, y0, channel) * (1 - fx) + at(x1, y0, channel) * fx;
    const bottom = at(x0, y1, channel) * (1 - fx) + at(x1, y1, channel) * fx;
    out[channel] = top * (1 - fy) + bottom * fy;
  }
  return out;
}

/** YuNet wants BGR 0..255 at 640×640. */
function toInput(source: Uint8Array, width: number, height: number): Float32Array {
  const out = new Float32Array(INPUT * INPUT * 3);
  for (let y = 0; y < INPUT; y += 1) {
    for (let x = 0; x < INPUT; x += 1) {
      const [r, g, b] = sample(source, width, height, ((x + 0.5) * width) / INPUT - 0.5, ((y + 0.5) * height) / INPUT - 0.5);
      const index = (y * INPUT + x) * 3;
      out[index] = b;
      out[index + 1] = g;
      out[index + 2] = r;
    }
  }
  return out;
}

function iou(left: Detection, right: Detection): number {
  const x1 = Math.max(left.x, right.x);
  const y1 = Math.max(left.y, right.y);
  const x2 = Math.min(left.x + left.width, right.x + right.width);
  const y2 = Math.min(left.y + left.height, right.y + right.height);
  const inter = Math.max(0, x2 - x1) * Math.max(0, y2 - y1);
  const union = left.width * left.height + right.width * right.height - inter;
  return union <= 0 ? 0 : inter / union;
}

async function detect(frame: FrameInput): Promise<Detection[]> {
  const model = await detector();
  const outputs = await run(model, toInput(frame.rgb, frame.width, frame.height), [1, 3, INPUT, INPUT]);
  const found: Detection[] = [];

  for (const stride of STRIDES) {
    const grid = INPUT / stride;
    const cls = outputs.get(`cls_${stride}`);
    const bbox = outputs.get(`bbox_${stride}`);
    const kps = outputs.get(`kps_${stride}`);
    if (cls === undefined || bbox === undefined || kps === undefined) continue;

    for (let row = 0; row < grid; row += 1) {
      for (let column = 0; column < grid; column += 1) {
        const index = row * grid + column;
        // The objectness head of this export stays near zero; the class score is used instead.
        const score = Math.min(1, Math.max(0, cls[index] ?? 0));
        if (score < THRESHOLD) continue;

        const cx = (column + (bbox[index * 4] ?? 0)) * stride;
        const cy = (row + (bbox[index * 4 + 1] ?? 0)) * stride;
        const width = Math.exp(bbox[index * 4 + 2] ?? 0) * stride;
        const height = Math.exp(bbox[index * 4 + 3] ?? 0) * stride;
        if (width / INPUT < MIN_FACE || height / INPUT < MIN_FACE) continue;

        const landmarks: number[] = [];
        for (let point = 0; point < 5; point += 1) {
          landmarks.push(((column + (kps[index * 10 + point * 2] ?? 0)) * stride) / INPUT);
          landmarks.push(((row + (kps[index * 10 + point * 2 + 1] ?? 0)) * stride) / INPUT);
        }
        found.push({
          x: (cx - width / 2) / INPUT,
          y: (cy - height / 2) / INPUT,
          width: width / INPUT,
          height: height / INPUT,
          score,
          landmarks,
          embedding: null,
        });
      }
    }
  }

  const sorted = found.sort((left, right) => right.score - left.score);
  const kept: Detection[] = [];
  for (const detection of sorted) {
    if (kept.every((other) => iou(other, detection) < IOU)) kept.push(detection);
  }
  return kept.slice(0, MAX_FACES);
}

function transform(landmarks: number[], width: number, height: number): [number, number, number, number, number, number] {
  const src = landmarks.map((value, index) => (index % 2 === 0 ? value * width : value * height));
  let sx = 0;
  let sy = 0;
  let dx = 0;
  let dy = 0;
  for (let point = 0; point < 5; point += 1) {
    sx += (src[point * 2] ?? 0) / 5;
    sy += (src[point * 2 + 1] ?? 0) / 5;
    dx += (TEMPLATE[point]?.[0] ?? 0) / 5;
    dy += (TEMPLATE[point]?.[1] ?? 0) / 5;
  }
  let sum = 0;
  let a = 0;
  let b = 0;
  for (let point = 0; point < 5; point += 1) {
    const px = (src[point * 2] ?? 0) - sx;
    const py = (src[point * 2 + 1] ?? 0) - sy;
    const qx = (TEMPLATE[point]?.[0] ?? 0) - dx;
    const qy = (TEMPLATE[point]?.[1] ?? 0) - dy;
    sum += px * px + py * py;
    a += px * qx + py * qy;
    b += px * qy - py * qx;
  }
  if (sum <= 0) return [1, 0, 0, 0, 1, 0];
  const scale = a / sum;
  const rotation = b / sum;
  return [scale, -rotation, dx - (scale * sx - rotation * sy), rotation, scale, dy - (rotation * sx + scale * sy)];
}

function aligned(frame: FrameInput, detection: Detection): Float32Array {
  const [a, b, tx, c, d, ty] = transform(detection.landmarks, frame.width, frame.height);
  const det = a * d - b * c;
  const out = new Float32Array(112 * 112 * 3);
  for (let y = 0; y < 112; y += 1) {
    for (let x = 0; x < 112; x += 1) {
      const srcX = (d * (x - tx) - b * (y - ty)) / det;
      const srcY = (-c * (x - tx) + a * (y - ty)) / det;
      const [r, g, bl] = sample(frame.rgb, frame.width, frame.height, srcX, srcY);
      const index = (y * 112 + x) * 3;
      out[index] = (bl - 127.5) / 128;
      out[index + 1] = (g - 127.5) / 128;
      out[index + 2] = (r - 127.5) / 128;
    }
  }
  return out;
}

async function embed(frame: FrameInput, detection: Detection): Promise<Float32Array> {
  const model = await recognizer();
  const outputs = await run(model, aligned(frame, detection), [1, 3, 112, 112]);
  const vector = outputs.get("fc1") ?? [...outputs.values()][0];
  if (vector === undefined) throw new Error("the recognizer returned nothing");
  let norm = 0;
  for (const value of vector) norm += value * value;
  norm = Math.sqrt(norm) + 1e-9;
  const out = new Float32Array(vector.length);
  for (let index = 0; index < vector.length; index += 1) out[index] = (vector[index] ?? 0) / norm;
  return out;
}

function cosine(left: Float32Array, right: Float32Array): number {
  let sum = 0;
  for (let index = 0; index < left.length; index += 1) sum += (left[index] ?? 0) * (right[index] ?? 0);
  return sum;
}

export async function analyzeFrames(frames: FrameInput[]): Promise<FacesResult> {
  const clusters: Array<{ centre: Float32Array; members: number }> = [];
  const frameFaces: FrameFaces[] = [];
  const previews: Array<{ time: number; box: Detection; person: number; weight: number }> = [];

  for (const frame of frames) {
    const detections = await detect(frame);
    for (const detection of detections) {
      try {
        detection.embedding = await embed(frame, detection);
      } catch (error) {
        console.warn("face embedding failed:", error);
      }
    }

    const boxes: FaceBox[] = [];
    for (const detection of detections) {
      if (detection.embedding === null) continue;
      let best = -1;
      let bestScore = SAME;
      for (const [index, cluster] of clusters.entries()) {
        const score = cosine(cluster.centre, detection.embedding);
        if (score > bestScore) {
          bestScore = score;
          best = index;
        }
      }
      if (best < 0) {
        clusters.push({ centre: detection.embedding, members: 1 });
        best = clusters.length - 1;
      } else {
        const cluster = clusters[best];
        if (cluster !== undefined) {
          for (let index = 0; index < cluster.centre.length; index += 1) {
            cluster.centre[index] =
              ((cluster.centre[index] ?? 0) * cluster.members + (detection.embedding[index] ?? 0)) / (cluster.members + 1);
          }
          cluster.members += 1;
        }
      }
      previews.push({ time: frame.time, box: detection, person: best, weight: detection.score * detection.width * detection.height });
      boxes.push({ x: detection.x, y: detection.y, width: detection.width, height: detection.height, person: best });
    }
    frameFaces.push({ time: frame.time, boxes });
  }

  const ranked = [...clusters.keys()]
    .map((id) => ({ id, frames: new Set(previews.filter((item) => item.person === id).map((item) => item.time)).size }))
    .sort((left, right) => right.frames - left.frames)
    .slice(0, MAX_PERSONS);
  const renumber = new Map(ranked.map((person, index) => [person.id, index]));
  const total = Math.max(1, frames.length);

  const persons: PersonInfo[] = ranked.map((person, index) => {
    const best = previews
      .filter((item) => item.person === person.id)
      .sort((left, right) => right.weight - left.weight)[0];
    return {
      id: index,
      frames: person.frames,
      share: Math.round((person.frames / total) * 100) / 100,
      preview:
        best === undefined
          ? { time: 0, x: 0, y: 0, width: 0, height: 0 }
          : { time: best.time, x: best.box.x, y: best.box.y, width: best.box.width, height: best.box.height },
    };
  });

  return {
    persons,
    frames: frameFaces.map((frame) => ({
      time: frame.time,
      boxes: frame.boxes.flatMap((box): FaceBox[] => {
        const mapped = renumber.get(box.person);
        return mapped === undefined ? [] : [{ ...box, person: mapped }];
      }),
    })),
  };
}
