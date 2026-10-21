import {
  AudioBufferSink,
  AudioBufferSource,
  BufferTarget,
  CanvasSink,
  CanvasSource,
  Mp4OutputFormat,
  Output,
  QUALITY_MEDIUM,
} from "mediabunny";
import type { InputAudioTrack, InputVideoTrack } from "mediabunny";

import type { TimeRange } from "./range.ts";

export interface ExportRequest {
  video: InputVideoTrack;
  audio: InputAudioTrack | null;
  width: number;
  height: number;
  range: TimeRange;
  onProgress?: (fraction: number) => void;
}

function evenDimension(value: number): number {
  return Math.max(2, Math.round(value / 2) * 2);
}

/** The kept part of a decoded audio chunk, as its own buffer. */
function sliceAudio(buffer: AudioBuffer, from: number, to: number): AudioBuffer {
  if (from <= 0 && to >= buffer.duration) return buffer;
  const rate = buffer.sampleRate;
  const first = Math.max(0, Math.round(from * rate));
  const last = Math.min(buffer.length, Math.round(to * rate));
  const length = Math.max(0, last - first);
  const slice = new AudioBuffer({
    numberOfChannels: buffer.numberOfChannels,
    length,
    sampleRate: rate,
  });
  for (let channel = 0; channel < buffer.numberOfChannels; channel += 1) {
    slice.copyToChannel(buffer.getChannelData(channel).subarray(first, first + length), channel);
  }
  return slice;
}

/**
 * Re-encodes one range into an MP4: decode frame by frame, draw it onto one
 * canvas, encode. The canvas is the single place overlays will be drawn later.
 */
export async function exportMp4(request: ExportRequest): Promise<Blob> {
  const width = evenDimension(request.width);
  const height = evenDimension(request.height);
  const { start, end } = request.range;
  const duration = end - start;
  if (!(duration > 0)) throw new Error("the export range is empty");

  const canvas = new OffscreenCanvas(width, height);
  const context = canvas.getContext("2d");
  if (context === null) throw new Error("the browser did not provide a 2D context for export");

  const frames = new CanvasSink(request.video, { width, height, fit: "fill", poolSize: 2 });
  const target = new BufferTarget();
  const output = new Output({ format: new Mp4OutputFormat(), target });
  const videoSource = new CanvasSource(canvas, { codec: "avc", quality: QUALITY_MEDIUM });
  output.addVideoTrack(videoSource);

  const audioSource =
    request.audio === null ? null : new AudioBufferSource({ codec: "aac", quality: QUALITY_MEDIUM });
  if (audioSource !== null) output.addAudioTrack(audioSource);

  await output.start();
  try {
    for await (const frame of frames.canvases(start, end)) {
      const from = Math.max(frame.timestamp, start);
      const to = Math.min(frame.timestamp + frame.duration, end);
      if (to <= from) continue;
      context.drawImage(frame.canvas, 0, 0, width, height);
      await videoSource.add(from - start, to - from);
      request.onProgress?.(Math.min(0.95, (from - start) / duration));
    }

    if (audioSource !== null && request.audio !== null) {
      for await (const chunk of new AudioBufferSink(request.audio).buffers(start, end)) {
        const from = Math.max(chunk.timestamp, start);
        const to = Math.min(chunk.timestamp + chunk.buffer.duration, end);
        if (to <= from) continue;
        await audioSource.add(sliceAudio(chunk.buffer, from - chunk.timestamp, to - chunk.timestamp));
      }
    }

    videoSource.close();
    audioSource?.close();
    await output.finalize();
  } catch (error) {
    // Cancel so the encoder does not keep running after the original failure.
    await output.cancel().catch(() => undefined);
    throw error;
  }

  const buffer = target.buffer;
  if (buffer === null) throw new Error("export finished without data");
  request.onProgress?.(1);
  return new Blob([buffer], { type: "video/mp4" });
}
