const SAMPLE_RATE = 16_000;
/** Longest clip the local models accept; 30 minutes of 16 kHz PCM16 is 57 MB. */
const MAX_SECONDS = 30 * 60;
const MAX_SAMPLES = SAMPLE_RATE * MAX_SECONDS;
const PCM = 1;
const EXTENSIBLE = 0xfffe;
/** KSDATAFORMAT_SUBTYPE_PCM, little-endian. */
const PCM_GUID = Buffer.from([0x01, 0, 0, 0, 0, 0, 0x10, 0, 0x80, 0, 0, 0xaa, 0, 0x38, 0x9b, 0x71]);

interface Format {
  channels: number;
  sampleRate: number;
  bits: number;
  blockAlign: number;
  byteRate: number;
}

function malformed(reason: string): never {
  throw new Error(`the WAV file is malformed: ${reason}`);
}

function readFormat(chunk: Buffer, body: number, size: number): Format {
  if (size < 16) malformed("the format chunk is too short");
  const encoding = chunk.readUInt16LE(body);
  if (encoding === EXTENSIBLE) {
    if (size < 40) malformed("the extended format chunk is too short");
    if (!chunk.subarray(body + 24, body + 40).equals(PCM_GUID)) {
      throw new Error("unsupported WAV encoding: the audio is not integer PCM");
    }
  } else if (encoding !== PCM) {
    throw new Error(`unsupported WAV encoding: format ${encoding}; only 16-bit PCM is accepted`);
  }

  return {
    channels: chunk.readUInt16LE(body + 2),
    sampleRate: chunk.readUInt32LE(body + 4),
    byteRate: chunk.readUInt32LE(body + 8),
    blockAlign: chunk.readUInt16LE(body + 12),
    bits: chunk.readUInt16LE(body + 14),
  };
}

/**
 * PCM16 mono WAV at 16 kHz → samples; that is what the browser always produces.
 * Rejects non-PCM, odd channel counts and truncated or inconsistent chunks instead of
 * reinterpreting their bytes as audio.
 */
export function decodeWav(buffer: Buffer): Float32Array {
  if (buffer.length < 12) throw new Error("the audio is not a WAV file");
  if (buffer.toString("ascii", 0, 4) !== "RIFF" || buffer.toString("ascii", 8, 12) !== "WAVE") {
    throw new Error("the audio is not a WAV file");
  }

  const riffSize = buffer.readUInt32LE(4);
  if (riffSize !== 0 && riffSize + 8 > buffer.length) malformed("the RIFF size is larger than the file");

  let format: Format | null = null;
  let dataOffset = -1;
  let dataLength = 0;

  let offset = 12;
  while (offset + 8 <= buffer.length) {
    const tag = buffer.toString("ascii", offset, offset + 4);
    const size = buffer.readUInt32LE(offset + 4);
    const body = offset + 8;
    if (body + size > buffer.length) malformed(`the "${tag.trim()}" chunk is truncated`);
    if (tag === "fmt ") format = readFormat(buffer, body, size);
    else if (tag === "data") {
      if (dataOffset >= 0) malformed("there is more than one data chunk");
      dataOffset = body;
      dataLength = size;
    }
    offset = body + size + (size % 2);
  }

  if (format === null) throw new Error("the WAV file has no format chunk");
  if (dataOffset < 0) throw new Error("the WAV file has no audio data");
  if (format.channels !== 1) {
    throw new Error(`expected mono audio, got ${format.channels} channels`);
  }
  if (format.sampleRate !== SAMPLE_RATE) {
    throw new Error(`expected 16 kHz audio, got ${format.sampleRate} Hz`);
  }
  if (format.bits !== 16) throw new Error(`unsupported WAV bit depth: ${format.bits}`);
  if (format.blockAlign !== (format.channels * format.bits) / 8) {
    throw new Error(`invalid WAV block alignment: ${format.blockAlign}`);
  }
  if (format.byteRate !== format.sampleRate * format.blockAlign) {
    throw new Error(`invalid WAV byte rate: ${format.byteRate}`);
  }
  if (dataLength === 0) throw new Error("the WAV file has no audio data");
  if (dataLength % format.blockAlign !== 0) malformed("the audio data is not a whole number of samples");

  const frames = dataLength / format.blockAlign;
  if (frames > MAX_SAMPLES) {
    throw new Error(`the audio is too long: at most ${MAX_SECONDS / 60} minutes are accepted`);
  }

  const samples = new Float32Array(frames);
  for (let frame = 0; frame < frames; frame += 1) {
    samples[frame] = buffer.readInt16LE(dataOffset + frame * format.blockAlign) / 32_768;
  }
  return samples;
}

export { SAMPLE_RATE };
