import { join } from "node:path";

import { ensureModel } from "./model-download.ts";
import { SAMPLE_RATE } from "./wav.ts";

const MODEL_URL =
  "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/wespeaker_en_voxceleb_resnet34.onnx";
const MODELS_DIR = join(import.meta.dirname, "..", ".cache", "models");
const MODEL_PATH = join(MODELS_DIR, "wespeaker_en_voxceleb_resnet34.onnx");

const N_FFT = 512;
const WIN = 400;
const HOP = 160;
const N_MELS = 80;
const MEL_LO = 20;
const MEL_HI = 8000;
/** Cosine similarity above which two segments are treated as the same voice. */
const SAME_VOICE = 0.6;

export interface SpeakerSegment {
  start: number;
  end: number;
}

export interface SpeakerHint {
  label: "A" | "B" | null;
  confidence: number;
}

export interface Diarization {
  hints: SpeakerHint[];
  /** How much the split can be trusted, 0..1. */
  reliability: number;
}

type Runner = (features: Float32Array, frames: number) => Promise<Float32Array>;

let runnerPromise: Promise<Runner> | null = null;

async function runner(): Promise<Runner> {
  if (runnerPromise !== null) return runnerPromise;

  runnerPromise = (async () => {
    await ensureModel(MODEL_URL, MODEL_PATH);

    const ort = await import("onnxruntime-node");
    const session = await ort.InferenceSession.create(MODEL_PATH);

    return async (features, frames) => {
      const output = await session.run({ feats: new ort.Tensor("float32", features, [1, frames, N_MELS]) });
      const tensor = output["output"] ?? Object.values(output)[0];
      if (tensor === undefined) throw new Error("the voice model returned nothing");
      if (!(tensor.data instanceof Float32Array)) throw new Error("the voice model returned an unexpected tensor");
      return tensor.data;
    };
  })();

  runnerPromise.catch(() => {
    runnerPromise = null;
  });
  return runnerPromise;
}

// ---------------------------------------------------------------- fbank
function mel(frequency: number): number {
  return 1127 * Math.log(1 + frequency / 700);
}

function melFilterbank(): Float32Array[] {
  const bins = N_FFT / 2 + 1;
  const frequencies = new Float32Array(bins);
  for (let index = 0; index < bins; index += 1) frequencies[index] = (index * SAMPLE_RATE) / N_FFT;

  const low = mel(MEL_LO);
  const high = mel(MEL_HI);
  const step = (high - low) / (N_MELS + 1);
  const filters: Float32Array[] = [];

  for (let band = 0; band < N_MELS; band += 1) {
    const left = low + band * step;
    const centre = low + (band + 1) * step;
    const right = low + (band + 2) * step;
    const filter = new Float32Array(bins);
    for (let index = 0; index < bins; index += 1) {
      const value = mel(frequencies[index] ?? 0);
      const rising = (value - left) / (centre - left);
      const falling = (right - value) / (right - centre);
      const weight = Math.max(0, Math.min(rising, falling));
      const inRange = (frequencies[index] ?? 0) > MEL_LO && (frequencies[index] ?? 0) < MEL_HI;
      filter[index] = inRange ? weight : 0;
    }
    filters.push(filter);
  }
  return filters;
}

const FILTERBANK = melFilterbank();
const POVEY = Float32Array.from({ length: WIN }, (_, index) => (0.5 - 0.5 * Math.cos((2 * Math.PI * index) / (WIN - 1))) ** 0.85);

/** In-place iterative radix-2 FFT. */
function fft(real: Float32Array, imaginary: Float32Array): void {
  const size = real.length;
  for (let index = 1, reversed = 0; index < size; index += 1) {
    let bit = size >> 1;
    for (; (reversed & bit) !== 0; bit >>= 1) reversed ^= bit;
    reversed ^= bit;
    if (index < reversed) {
      [real[index], real[reversed]] = [real[reversed] ?? 0, real[index] ?? 0];
      [imaginary[index], imaginary[reversed]] = [imaginary[reversed] ?? 0, imaginary[index] ?? 0];
    }
  }

  for (let length = 2; length <= size; length <<= 1) {
    const angle = (-2 * Math.PI) / length;
    const stepReal = Math.cos(angle);
    const stepImaginary = Math.sin(angle);
    for (let start = 0; start < size; start += length) {
      let currentReal = 1;
      let currentImaginary = 0;
      for (let offset = 0; offset < length / 2; offset += 1) {
        const left = start + offset;
        const right = left + length / 2;
        const rightReal = (real[right] ?? 0) * currentReal - (imaginary[right] ?? 0) * currentImaginary;
        const rightImaginary = (real[right] ?? 0) * currentImaginary + (imaginary[right] ?? 0) * currentReal;
        real[right] = (real[left] ?? 0) - rightReal;
        imaginary[right] = (imaginary[left] ?? 0) - rightImaginary;
        real[left] = (real[left] ?? 0) + rightReal;
        imaginary[left] = (imaginary[left] ?? 0) + rightImaginary;
        const nextReal = currentReal * stepReal - currentImaginary * stepImaginary;
        currentImaginary = currentReal * stepImaginary + currentImaginary * stepReal;
        currentReal = nextReal;
      }
    }
  }
}

/** Kaldi-compatible 80-dim log-mel filterbank with per-band mean normalization. */
export function fbank(samples: Float32Array): { data: Float32Array; frames: number } {
  const frames = 1 + Math.floor((samples.length - WIN) / HOP);
  if (frames <= 0) throw new Error("the segment is too short for the voice model");

  const features = new Float32Array(frames * N_MELS);
  const real = new Float32Array(N_FFT);
  const imaginary = new Float32Array(N_FFT);
  const power = new Float32Array(N_FFT / 2 + 1);
  const mel = new Float32Array(N_MELS);

  for (let frame = 0; frame < frames; frame += 1) {
    const offset = frame * HOP;
    let mean = 0;
    for (let index = 0; index < WIN; index += 1) mean += (samples[offset + index] ?? 0) * 32768;
    mean /= WIN;

    for (let index = 0; index < WIN; index += 1) {
      const current = (samples[offset + index] ?? 0) * 32768 - mean;
      const previous = index === 0 ? current * 0.03 : current - 0.97 * ((samples[offset + index - 1] ?? 0) * 32768 - mean);
      real[index] = previous * (POVEY[index] ?? 1);
      imaginary[index] = 0;
    }
    for (let index = WIN; index < N_FFT; index += 1) {
      real[index] = 0;
      imaginary[index] = 0;
    }

    fft(real, imaginary);
    for (let index = 0; index < N_FFT / 2 + 1; index += 1) {
      power[index] = (real[index] ?? 0) ** 2 + (imaginary[index] ?? 0) ** 2;
    }

    for (let band = 0; band < N_MELS; band += 1) {
      const filter = FILTERBANK[band];
      let sum = 0;
      if (filter !== undefined) {
        for (let index = 0; index < power.length; index += 1) {
          sum += (power[index] ?? 0) * (filter[index] ?? 0);
        }
      }
      mel[band] = Math.log(Math.max(sum, 1.19e-7));
    }

    for (let band = 0; band < N_MELS; band += 1) {
      features[frame * N_MELS + band] = mel[band] ?? 0;
    }
  }

  for (let band = 0; band < N_MELS; band += 1) {
    let mean = 0;
    for (let frame = 0; frame < frames; frame += 1) mean += features[frame * N_MELS + band] ?? 0;
    mean /= frames;
    for (let frame = 0; frame < frames; frame += 1) {
      const index = frame * N_MELS + band;
      features[index] = (features[index] ?? 0) - mean;
    }
  }
  return { data: features, frames };
}

function normalize(vector: Float32Array): Float32Array {
  let norm = 0;
  for (const value of vector) norm += value * value;
  norm = Math.sqrt(norm) + 1e-9;
  const out = new Float32Array(vector.length);
  for (let index = 0; index < vector.length; index += 1) out[index] = (vector[index] ?? 0) / norm;
  return out;
}

async function embeddings(audio: Float32Array, segments: SpeakerSegment[]): Promise<Array<Float32Array | null>> {
  const embed = await runner();
  const result: Array<Float32Array | null> = [];

  for (const segment of segments) {
    const from = Math.max(0, Math.round(segment.start * SAMPLE_RATE));
    const to = Math.min(audio.length, Math.round(segment.end * SAMPLE_RATE));
    if (to - from < SAMPLE_RATE * 0.4) {
      result.push(null);
      continue;
    }
    try {
      const features = fbank(audio.subarray(from, to));
      result.push(normalize(await embed(features.data, features.frames)));
    } catch (error) {
      console.warn("voice embedding failed:", error);
      result.push(null);
    }
  }
  return result;
}

function dot(left: Float32Array, right: Float32Array): number {
  let sum = 0;
  for (let index = 0; index < left.length; index += 1) sum += (left[index] ?? 0) * (right[index] ?? 0);
  return sum;
}

/** Average-linkage agglomerative clustering on cosine similarity. */
function cluster(vectors: Float32Array[], threshold: number): number[][] {
  const clusters = vectors.map((_, index) => [index]);
  const similarity = (left: number, right: number): number => dot(vectors[left] ?? new Float32Array(), vectors[right] ?? new Float32Array());

  while (clusters.length > 1) {
    let best = threshold;
    let pair: [number, number] | null = null;
    for (let a = 0; a < clusters.length; a += 1) {
      for (let b = a + 1; b < clusters.length; b += 1) {
        let sum = 0;
        let count = 0;
        for (const left of clusters[a] ?? []) {
          for (const right of clusters[b] ?? []) {
            sum += similarity(left, right);
            count += 1;
          }
        }
        const value = count === 0 ? 0 : sum / count;
        if (value > best) {
          best = value;
          pair = [a, b];
        }
      }
    }
    if (pair === null) break;
    const [first, second] = pair;
    clusters[first] = [...(clusters[first] ?? []), ...(clusters[second] ?? [])];
    clusters.splice(second, 1);
  }
  return clusters;
}

function logit(probability: number): number {
  const clamped = Math.min(0.999, Math.max(0.001, probability));
  return Math.log(clamped / (1 - clamped));
}

function sigmoid(value: number): number {
  return 1 / (1 + Math.exp(-value));
}

/** Two voices at most; A is the voice with the most speech. */
export async function diarize(audio: Float32Array, segments: SpeakerSegment[]): Promise<Diarization> {
  const vectors = await embeddings(audio, segments);
  const usable = vectors.flatMap((vector, index) => (vector === null ? [] : [{ vector, index }]));
  if (usable.length < 2) return { hints: segments.map(() => ({ label: null, confidence: 0 })), reliability: 0 };

  const matrix = usable.map((item) => item.vector);
  const duration = usable.map((item) => {
    const segment = segments[item.index];
    return segment === undefined ? 0 : segment.end - segment.start;
  });
  const clusters = cluster(matrix, SAME_VOICE).sort((left, right) => {
    const sum = (items: number[]): number => items.reduce((total, item) => total + (duration[item] ?? 0), 0);
    return sum(right) - sum(left);
  });

  const main = clusters.slice(0, 2);
  let reliability: number;
  let probabilities: number[];

  if (main.length === 1) {
    reliability = 0.8;
    probabilities = matrix.map(() => 0.95);
  } else {
    const [first, second] = main as [number[], number[]];
    const centroid = (items: number[]): Float32Array => {
      const vector = new Float32Array(matrix[0]?.length ?? 0);
      for (const item of items) {
        const source = matrix[item];
        if (source === undefined) continue;
        for (let index = 0; index < vector.length; index += 1) vector[index] = (vector[index] ?? 0) + (source[index] ?? 0);
      }
      return normalize(vector);
    };
    const cross = dot(centroid(first), centroid(second));
    // Calibrated for this model: different voices land near 0.5, the same voice near 0.8.
    reliability = Math.max(0, Math.min(1, (0.75 - cross) / 0.2));

    const meanSimilarity = (row: number, members: number[]): number => {
      const others = members.filter((item) => item !== row);
      const list = others.length > 0 ? others : members;
      let sum = 0;
      for (const item of list) sum += dot(matrix[row] ?? new Float32Array(), matrix[item] ?? new Float32Array());
      return list.length === 0 ? 0 : sum / list.length;
    };

    probabilities = matrix.map(
      (_, row) => sigmoid((meanSimilarity(row, first) - meanSimilarity(row, second)) / 0.1),
    );
  }

  const hints: SpeakerHint[] = segments.map(() => ({ label: null, confidence: 0 }));
  for (const [row, item] of usable.entries()) {
    const segment = segments[item.index];
    if (segment === undefined) continue;
    const length = Math.min(1, (segment.end - segment.start) / 1);
    const probability = 0.5 + ((probabilities[row] ?? 0.5) - 0.5) * reliability * length;
    const value = logit(probability);
    hints[item.index] = {
      label: Math.abs(value) < 0.05 ? null : value > 0 ? "A" : "B",
      confidence: Math.round(sigmoid(Math.abs(value)) * 1000) / 1000,
    };
  }

  return { hints, reliability: Math.round(reliability * 1000) / 1000 };
}
