import assert from "node:assert/strict";
import { test } from "node:test";

import { decodeWav, SAMPLE_RATE } from "../server/wav.ts";

interface FormatOptions {
  encoding?: number;
  channels?: number;
  rate?: number;
  bits?: number;
  blockAlign?: number;
  byteRate?: number;
}

function formatChunk(options: FormatOptions = {}): Buffer {
  const channels = options.channels ?? 1;
  const rate = options.rate ?? SAMPLE_RATE;
  const bits = options.bits ?? 16;
  const blockAlign = options.blockAlign ?? (channels * bits) / 8;
  const body = Buffer.alloc(16);
  body.writeUInt16LE(options.encoding ?? 1, 0);
  body.writeUInt16LE(channels, 2);
  body.writeUInt32LE(rate, 4);
  body.writeUInt32LE(options.byteRate ?? rate * blockAlign, 8);
  body.writeUInt16LE(blockAlign, 12);
  body.writeUInt16LE(bits, 14);
  return body;
}

function chunk(tag: string, body: Buffer, declaredSize = body.length): Buffer {
  const header = Buffer.alloc(8);
  header.write(tag, 0, "ascii");
  header.writeUInt32LE(declaredSize, 4);
  return Buffer.concat([header, body, Buffer.alloc(body.length % 2)]);
}

interface WavOptions {
  format?: Buffer | null;
  data?: Buffer;
  extraChunks?: Buffer[];
  declaredDataSize?: number;
  declaredFormatSize?: number;
  riffSizeDelta?: number;
}

function wav(options: WavOptions = {}): Buffer {
  const format = options.format === undefined ? formatChunk() : options.format;
  const data = options.data ?? Buffer.alloc(4);
  const chunks: Buffer[] = [];
  if (format !== null) chunks.push(chunk("fmt ", format, options.declaredFormatSize));
  chunks.push(...(options.extraChunks ?? []));
  chunks.push(chunk("data", data, options.declaredDataSize));
  const body = Buffer.concat(chunks);
  const header = Buffer.alloc(12);
  header.write("RIFF", 0, "ascii");
  header.writeUInt32LE(body.length + 4 + (options.riffSizeDelta ?? 0), 4);
  header.write("WAVE", 8, "ascii");
  return Buffer.concat([header, body]);
}

function pcm(...values: number[]): Buffer {
  const data = Buffer.alloc(values.length * 2);
  values.forEach((value, index) => data.writeInt16LE(value, index * 2));
  return data;
}

test("decodes 16-bit mono PCM at 16 kHz", () => {
  const samples = decodeWav(wav({ data: pcm(-32768, -16384, 0, 16384, 32767) }));
  assert.equal(samples.length, 5);
  assert.equal(samples[0], -1);
  assert.equal(samples[1], -0.5);
  assert.equal(samples[2], 0);
  assert.equal(samples[3], 0.5);
  assert.ok(Math.abs((samples[4] ?? 0) - 32767 / 32768) < 1e-7);
});

test("accepts a PCM header written in the extensible layout", () => {
  const body = Buffer.alloc(40);
  formatChunk().copy(body, 0);
  body.writeUInt16LE(0xfffe, 0);
  body.writeUInt16LE(22, 16);
  body.writeUInt16LE(16, 18);
  body.writeUInt32LE(0, 20);
  Buffer.from([0x01, 0, 0, 0, 0, 0, 0x10, 0, 0x80, 0, 0, 0xaa, 0, 0x38, 0x9b, 0x71]).copy(body, 24);
  const samples = decodeWav(wav({ format: body, data: pcm(16384) }));
  assert.deepEqual([...samples], [0.5]);
});

test("skips unrelated chunks before the audio data", () => {
  const extra = chunk("LIST", Buffer.from("INFOxxxx"));
  const samples = decodeWav(wav({ data: pcm(8192), extraChunks: [extra] }));
  assert.deepEqual([...samples], [0.25]);
});

test("rejects non-WAV input", () => {
  assert.throws(() => decodeWav(Buffer.from("not a wave file at all")), /not a WAV file/);
  assert.throws(() => decodeWav(Buffer.alloc(0)), /not a WAV file/);
});

test("rejects a RIFF size that promises more bytes than are present", () => {
  assert.throws(() => decodeWav(wav({ data: pcm(1), riffSizeDelta: 100 })), /RIFF size/);
});

test("rejects an unsupported encoding instead of reinterpreting it as PCM", () => {
  assert.throws(() => decodeWav(wav({ format: formatChunk({ encoding: 3 }) })), /unsupported WAV encoding/);
});

test("rejects a non-PCM extensible subformat", () => {
  const body = Buffer.alloc(40);
  formatChunk().copy(body, 0);
  body.writeUInt16LE(0xfffe, 0);
  body.writeUInt16LE(22, 16);
  body.writeUInt16LE(16, 18);
  body.writeUInt32LE(0, 20);
  // IEEE float subformat GUID.
  Buffer.from([0x03, 0, 0, 0, 0, 0, 0x10, 0, 0x80, 0, 0, 0xaa, 0, 0x38, 0x9b, 0x71]).copy(body, 24);
  assert.throws(() => decodeWav(wav({ format: body })), /not integer PCM/);
});

test("rejects unsupported bit depth, channels and sample rate", () => {
  assert.throws(() => decodeWav(wav({ format: formatChunk({ bits: 24 }) })), /bit depth/);
  assert.throws(() => decodeWav(wav({ format: formatChunk({ channels: 2 }) })), /mono/);
  assert.throws(() => decodeWav(wav({ format: formatChunk({ rate: 44_100 }) })), /16 kHz/);
});

test("rejects inconsistent format fields", () => {
  assert.throws(() => decodeWav(wav({ format: formatChunk({ blockAlign: 4 }) })), /block alignment/);
  assert.throws(() => decodeWav(wav({ format: formatChunk({ byteRate: 1234 }) })), /byte rate/);
});

test("rejects truncated and inconsistent chunks", () => {
  assert.throws(() => decodeWav(wav({ data: pcm(1), declaredDataSize: 100 })), /truncated/);
  assert.throws(() => decodeWav(wav({ data: Buffer.from([1, 2, 3]) })), /whole number of samples/);
  assert.throws(() => decodeWav(wav({ format: null })), /no format chunk/);
  assert.throws(() => decodeWav(wav({ data: Buffer.alloc(0) })), /no audio data/);
});
