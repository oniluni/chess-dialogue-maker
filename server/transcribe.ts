import { join } from "node:path";

import type { TranscriptLine } from "../shared/transcribe.ts";
import { decodeWav, SAMPLE_RATE } from "./wav.ts";

const MAX_GAP = 0.8;
const MIN_SENTENCE_WORDS = 3;
const SENTENCE_END = /[.!?…]["'»]?$/;

interface Word {
  start: number;
  end: number;
  text: string;
}

type Transcriber = (audio: Float32Array, options: Record<string, unknown>) => Promise<unknown>;

let transcriberPromise: Promise<Transcriber> | null = null;

export function modelName(): string {
  return process.env["WHISPER_MODEL"]?.trim() || "Xenova/whisper-small";
}

export function defaultLanguage(): string {
  return process.env["WHISPER_LANGUAGE"]?.trim() || "ru";
}

async function createTranscriber(): Promise<Transcriber> {
  const { env, pipeline } = await import("@huggingface/transformers");
  env.cacheDir = join(import.meta.dirname, "..", ".cache", "transformers");
  env.allowLocalModels = false;

  const instance = await pipeline("automatic-speech-recognition", modelName(), { dtype: "q8" });
  if (typeof instance !== "function") throw new Error("the speech model did not load");
  return (audio, options) => instance(audio, options);
}

function transcriber(): Promise<Transcriber> {
  transcriberPromise ??= createTranscriber().catch((error: unknown) => {
    transcriberPromise = null;
    throw error;
  });
  return transcriberPromise;
}

/** PCM16 mono WAV â†’ samples; the browser always produces 16 kHz mono. */
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Word timestamps out of the pipeline result. */
function wordsOf(output: unknown): Word[] {
  if (!isRecord(output)) throw new Error("the speech model returned an unexpected answer");
  const chunks = output["chunks"];
  if (!Array.isArray(chunks)) throw new Error("the speech model returned no word timestamps");

  return chunks.flatMap((chunk): Word[] => {
    if (!isRecord(chunk)) return [];
    const text = chunk["text"];
    const timestamp = chunk["timestamp"];
    if (typeof text !== "string" || !Array.isArray(timestamp)) return [];
    const start = timestamp[0];
    const end = timestamp[1];
    if (typeof start !== "number" || typeof end !== "number" || end <= start) return [];
    const word = text.replace(/\s+/g, " ").trim();
    return word === "" ? [] : [{ start, end, text: word }];
  });
}

/** Merges words into lines: a line breaks on a sentence end or a long pause. */
function linesOf(words: Word[]): TranscriptLine[] {
  const lines: TranscriptLine[] = [];
  let current: Word[] = [];

  const flush = (): void => {
    const first = current[0];
    const last = current[current.length - 1];
    if (first !== undefined && last !== undefined) {
      lines.push({
        start: Math.round(first.start * 100) / 100,
        end: Math.round(last.end * 100) / 100,
        text: current.map((word) => word.text).join(" "),
      });
    }
    current = [];
  };

  for (const word of words) {
    const previous = current[current.length - 1];
    if (previous !== undefined && word.start - previous.end > MAX_GAP) flush();
    current.push(word);
    if (SENTENCE_END.test(word.text) && current.length >= MIN_SENTENCE_WORDS) flush();
  }
  flush();
  return lines;
}

export async function transcribe(wav: Buffer, language: string): Promise<TranscriptLine[]> {
  const audio = decodeWav(wav);
  if (audio.length < SAMPLE_RATE * 0.2) throw new Error("the clip is too short to transcribe");

  const run = await transcriber();
  const output = await run(audio, {
    return_timestamps: "word",
    chunk_length_s: 30,
    stride_length_s: 5,
    language,
    task: "transcribe",
  });

  const lines = linesOf(wordsOf(output));
  if (lines.length === 0) throw new Error("no speech was recognised in the clip");
  return lines;
}

