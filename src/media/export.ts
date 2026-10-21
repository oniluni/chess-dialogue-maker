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

import { FRAME_RATE } from "../analysis/schedule.ts";
import type { Schedule } from "../analysis/schedule.ts";

export interface ExportRequest {
  video: InputVideoTrack;
  audio: InputAudioTrack | null;
  width: number;
  height: number;
  schedule: Schedule;
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

/** Silence of the same shape as the decoded audio, for the frozen stretches. */
function silentBuffer(seconds: number, channels: number, sampleRate: number): AudioBuffer {
  return new AudioBuffer({
    numberOfChannels: channels,
    length: Math.max(1, Math.round(seconds * sampleRate)),
    sampleRate,
  });
}

/**
 * Renders the schedule into an MP4: every play segment is decoded and re-encoded frame by
 * frame, every pause holds one frame for its duration, and the audio gets silence there.
 * The single canvas is also where the move card will be drawn.
 */
export async function exportMp4(request: ExportRequest): Promise<Blob> {
  const width = evenDimension(request.width);
  const height = evenDimension(request.height);
  const total = request.schedule.duration;
  if (!(total > 0)) throw new Error("the export is empty");

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
    let outTime = 0;
    for (const segment of request.schedule.segments) {
      if (segment.kind === "play") {
        for await (const frame of frames.canvases(segment.start, segment.end)) {
          const from = Math.max(frame.timestamp, segment.start);
          const to = Math.min(frame.timestamp + frame.duration, segment.end);
          if (to <= from) continue;
          context.drawImage(frame.canvas, 0, 0, width, height);
          await videoSource.add(outTime, to - from);
          outTime += to - from;
          request.onProgress?.(Math.min(0.95, outTime / total));
        }
        continue;
      }

      const still = await frames.getCanvas(segment.at);
      const count = Math.max(1, Math.round(segment.duration * FRAME_RATE));
      for (let index = 0; index < count; index += 1) {
        if (still !== null) context.drawImage(still.canvas, 0, 0, width, height);
        await videoSource.add(outTime, 1 / FRAME_RATE);
        outTime += 1 / FRAME_RATE;
        request.onProgress?.(Math.min(0.95, outTime / total));
      }
    }

    if (audioSource !== null && request.audio !== null) {
      const audioSink = new AudioBufferSink(request.audio);
      let channels = 2;
      let sampleRate = 48_000;
      for (const segment of request.schedule.segments) {
        if (segment.kind === "pause") {
          await audioSource.add(silentBuffer(segment.duration, channels, sampleRate));
          continue;
        }
        for await (const chunk of audioSink.buffers(segment.start, segment.end)) {
          const buffer = chunk.buffer;
          channels = buffer.numberOfChannels;
          sampleRate = buffer.sampleRate;
          const from = Math.max(chunk.timestamp, segment.start);
          const to = Math.min(chunk.timestamp + buffer.duration, segment.end);
          if (to <= from) continue;
          await audioSource.add(sliceAudio(buffer, from - chunk.timestamp, to - chunk.timestamp));
        }
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
