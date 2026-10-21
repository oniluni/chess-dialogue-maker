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

export interface ExportRequest {
  video: InputVideoTrack;
  audio: InputAudioTrack | null;
  width: number;
  height: number;
  duration: number;
  onProgress?: (fraction: number) => void;
}

function evenDimension(value: number): number {
  return Math.max(2, Math.round(value / 2) * 2);
}

/**
 * Re-encodes the whole track into an MP4: decode frame by frame, draw it onto one
 * canvas, encode. The canvas is the single place overlays will be drawn later.
 */
export async function exportMp4(request: ExportRequest): Promise<Blob> {
  const width = evenDimension(request.width);
  const height = evenDimension(request.height);
  const { duration } = request;

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
    for await (const frame of frames.canvases(0, duration)) {
      context.drawImage(frame.canvas, 0, 0, width, height);
      await videoSource.add(frame.timestamp, frame.duration);
      request.onProgress?.(Math.min(0.95, frame.timestamp / duration));
    }

    if (audioSource !== null && request.audio !== null) {
      for await (const chunk of new AudioBufferSink(request.audio).buffers(0, duration)) {
        await audioSource.add(chunk.buffer);
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
