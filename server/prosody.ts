import { SAMPLE_RATE } from "./wav.ts";

export interface ProsodySegment {
  start: number;
  end: number;
  /** Voice label from the diarization: `A`, `B` or `null` when unsure. */
  label: "A" | "B" | null;
}

export interface ProsodyEvent {
  kind: string;
  label: string;
  start: number;
  end: number;
  peak: number;
  reliable: boolean;
}

export interface ProsodyResult {
  events: ProsodyEvent[];
  /** How many segments each voice had; too few and the "usual manner" means nothing. */
  samples: Record<string, number>;
}

const METRICS = ["loudness", "pitch", "rate"] as const;
type Metric = (typeof METRICS)[number];

const LABELS: Record<Metric, { up: string; down: string | null }> = {
  loudness: { up: "Louder", down: "Quieter" },
  // The old project only knew "higher": a drop in pitch is not a separate event.
  pitch: { up: "Higher voice", down: null },
  rate: { up: "Faster", down: "Slower" },
};

const KIND_UP: Record<Metric, string> = { loudness: "louder", pitch: "higher", rate: "faster" };
const KIND_DOWN: Record<Metric, string | null> = { loudness: "quieter", pitch: null, rate: "slower" };

const MIN_SEGMENT = 0.25;
/** A pause longer than this between two lines is worth a tag. */
const PAUSE = 1.5;
const Z_THRESHOLD = 2;
const Z_RELIABLE = 2.5;
/** Fewer lines than this per voice and the baseline is noise. */
const MIN_SAMPLES = 3;

function median(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? ((sorted[middle - 1] ?? 0) + (sorted[middle] ?? 0)) / 2 : (sorted[middle] ?? 0);
}

function mad(values: number[], centre: number): number {
  return median(values.map((value) => Math.abs(value - centre)));
}

/** RMS loudness of a slice, in dB. */
function loudness(pcm: Float32Array, from: number, to: number): number {
  let sum = 0;
  const length = Math.max(1, to - from);
  for (let index = from; index < to; index += 1) {
    const value = pcm[index] ?? 0;
    sum += value * value;
  }
  return 20 * Math.log10(Math.sqrt(sum / length) + 1e-6);
}

/** Median fundamental frequency over voiced 40 ms frames. */
function pitch(pcm: Float32Array, from: number, to: number): number | null {
  const frame = Math.round(0.04 * SAMPLE_RATE);
  const hop = Math.round(0.01 * SAMPLE_RATE);
  const minLag = Math.round(SAMPLE_RATE / 400);
  const maxLag = Math.round(SAMPLE_RATE / 70);
  const found: number[] = [];

  for (let start = from; start + frame + maxLag <= to; start += hop) {
    let energy = 0;
    for (let index = 0; index < frame; index += 1) {
      const value = pcm[start + index] ?? 0;
      energy += value * value;
    }
    if (energy / frame < 1e-5) continue;

    let bestLag = 0;
    let bestScore = 0;
    const scores = new Float64Array(maxLag + 1);
    for (let lag = minLag; lag <= maxLag; lag += 1) {
      let score = 0;
      for (let index = 0; index < frame; index += 1) {
        score += (pcm[start + index] ?? 0) * (pcm[start + index + lag] ?? 0);
      }
      scores[lag] = score;
      if (score > bestScore) {
        bestScore = score;
        bestLag = lag;
      }
    }
    if (bestLag > 0 && bestScore > 0.3 * energy) {
      // A lower octave can score a hair higher when several periods align exactly; prefer the
      // shortest lag that is almost as good so the fundamental wins over its sub-harmonic.
      for (let lag = minLag; lag < bestLag; lag += 1) {
        if ((scores[lag] ?? 0) >= bestScore * 0.95) {
          bestLag = lag;
          break;
        }
      }
      found.push(SAMPLE_RATE / bestLag);
    }
  }

  return found.length === 0 ? null : median(found);
}

/** Energy peaks per second, a rough syllable rate. */
function rate(pcm: Float32Array, from: number, to: number): number {
  const hop = Math.round(0.01 * SAMPLE_RATE);
  const envelope: number[] = [];
  for (let start = from; start + hop <= to; start += hop) {
    let sum = 0;
    for (let index = 0; index < hop; index += 1) {
      const value = pcm[start + index] ?? 0;
      sum += value * value;
    }
    envelope.push(Math.sqrt(sum / hop));
  }
  if (envelope.length < 4) return 0;

  const peak = Math.max(...envelope);
  const gate = peak * 0.4;
  let count = 0;
  let inside = false;
  for (const value of envelope) {
    if (!inside && value >= gate) {
      count += 1;
      inside = true;
    } else if (inside && value < gate * 0.6) {
      inside = false;
    }
  }
  const seconds = (to - from) / SAMPLE_RATE;
  return seconds <= 0 ? 0 : count / seconds;
}

/** Compares each line with how the same voice usually sounds; pauses become their own events. */
export function detectProsody(pcm: Float32Array, segments: ProsodySegment[]): ProsodyResult {
  const values: Array<Record<Metric, number | null>> = segments.map((segment) => {
    const from = Math.max(0, Math.round(segment.start * SAMPLE_RATE));
    const to = Math.min(pcm.length, Math.round(segment.end * SAMPLE_RATE));
    if (to - from < MIN_SEGMENT * SAMPLE_RATE) return { loudness: null, pitch: null, rate: null };
    return {
      loudness: loudness(pcm, from, to),
      pitch: pitch(pcm, from, to),
      rate: rate(pcm, from, to),
    };
  });

  const groups = new Map<string, number[]>();
  segments.forEach((segment, index) => {
    const key = segment.label ?? "?";
    const list = groups.get(key) ?? [];
    list.push(index);
    groups.set(key, list);
  });

  const samples: Record<string, number> = {};
  const events: ProsodyEvent[] = [];

  for (const [key, indices] of groups) {
    samples[key] = indices.length;
    if (indices.length < MIN_SAMPLES) continue;

    for (const metric of METRICS) {
      const known = indices.flatMap((index) => {
        const value = values[index]?.[metric];
        return value === null || value === undefined ? [] : [value];
      });
      if (known.length < MIN_SAMPLES) continue;

      const centre = median(known);
      const spread = 1.4826 * mad(known, centre);
      if (spread < 1e-6) continue;

      for (const index of indices) {
        const value = values[index]?.[metric];
        const segment = segments[index];
        if (value === null || value === undefined || segment === undefined) continue;
        const z = (value - centre) / spread;
        if (Math.abs(z) < Z_THRESHOLD) continue;

        const up = z > 0;
        const labels = LABELS[metric];
        const label = up ? labels.up : labels.down;
        const kind = up ? KIND_UP[metric] : KIND_DOWN[metric];
        if (label === null || kind === null) continue;
        events.push({
          kind,
          label,
          start: Math.round(segment.start * 100) / 100,
          end: Math.round(segment.end * 100) / 100,
          peak: Math.round(Math.min(1, Math.abs(z) / 3) * 100) / 100,
          reliable: Math.abs(z) >= Z_RELIABLE,
        });
      }
    }
  }

  const ordered = [...segments].sort((left, right) => left.start - right.start);
  for (let index = 1; index < ordered.length; index += 1) {
    const previous = ordered[index - 1];
    const current = ordered[index];
    if (previous === undefined || current === undefined) continue;
    const gap = current.start - previous.end;
    if (gap >= PAUSE) {
      events.push({
        kind: "pause",
        label: "Pause",
        start: Math.round(previous.end * 100) / 100,
        end: Math.round(current.start * 100) / 100,
        peak: Math.round(Math.min(1, gap / 4) * 100) / 100,
        reliable: gap >= 2,
      });
    }
  }

  return {
    events: events.sort((left, right) => left.start - right.start),
    samples,
  };
}
