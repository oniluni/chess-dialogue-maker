const SAMPLE_RATE = 16_000;

/** PCM16 mono WAV at 16 kHz → samples; that is what the browser always produces. */
export function decodeWav(buffer: Buffer): Float32Array {
  if (buffer.length < 44) throw new Error("the audio is not a WAV file");
  if (buffer.toString("ascii", 0, 4) !== "RIFF" || buffer.toString("ascii", 8, 12) !== "WAVE") {
    throw new Error("the audio is not a WAV file");
  }

  let offset = 12;
  let channels = 1;
  let sampleRate = SAMPLE_RATE;
  let bits = 16;
  let dataOffset = -1;
  let dataLength = 0;

  while (offset + 8 <= buffer.length) {
    const tag = buffer.toString("ascii", offset, offset + 4);
    const size = buffer.readUInt32LE(offset + 4);
    if (tag === "fmt ") {
      channels = buffer.readUInt16LE(offset + 10);
      sampleRate = buffer.readUInt32LE(offset + 12);
      bits = buffer.readUInt16LE(offset + 22);
    } else if (tag === "data") {
      dataOffset = offset + 8;
      dataLength = Math.min(size, buffer.length - dataOffset);
    }
    offset += 8 + size + (size % 2);
  }

  if (dataOffset < 0) throw new Error("the WAV file has no audio data");
  if (bits !== 16) throw new Error(`unsupported WAV bit depth: ${bits}`);
  if (sampleRate !== SAMPLE_RATE) throw new Error(`expected 16 kHz audio, got ${sampleRate} Hz`);

  const frames = Math.floor(dataLength / (2 * channels));
  const samples = new Float32Array(frames);
  for (let frame = 0; frame < frames; frame += 1) {
    samples[frame] = buffer.readInt16LE(dataOffset + frame * channels * 2) / 32_768;
  }
  return samples;
}

export { SAMPLE_RATE };
