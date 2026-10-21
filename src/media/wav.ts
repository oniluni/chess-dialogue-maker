import { ALL_FORMATS, BlobSource, BufferTarget, Conversion, Input, Output, WavOutputFormat } from "mediabunny";

/** The clip's audio as 16 kHz mono PCM16 WAV, the input the speech model expects. */
export async function extractWav(file: File): Promise<Blob> {
  const input = new Input({ formats: ALL_FORMATS, source: new BlobSource(file) });
  try {
    const target = new BufferTarget();
    const output = new Output({ format: new WavOutputFormat(), target });
    const conversion = await Conversion.init({
      input,
      output,
      video: { discard: true },
      audio: { sampleRate: 16_000, numberOfChannels: 1 },
    });
    if (!conversion.isValid) throw new Error("the clip has no audio track to transcribe");
    await conversion.execute();

    const buffer = target.buffer;
    if (buffer === null) throw new Error("audio extraction produced no data");
    return new Blob([buffer], { type: "audio/wav" });
  } finally {
    input.dispose();
  }
}
