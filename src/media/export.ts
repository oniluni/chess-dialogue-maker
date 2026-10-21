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

import type { Move } from "../analysis/move.ts";
import { activeMoveIndex } from "../analysis/move.ts";
import { FRAME_RATE } from "../analysis/schedule.ts";
import type { Schedule, ScheduleOptions } from "../analysis/schedule.ts";
import { buildSfxEvents } from "../audio/events.ts";
import type { SfxEvent } from "../audio/events.ts";
import { loadSfx, SFX_GAIN } from "../audio/sfx.ts";
import type { SfxLibrary } from "../audio/sfx.ts";
import type { Scene } from "../render/scene.ts";
import { pauseState, playState } from "../render/state.ts";

export interface ExportRequest {
  video: InputVideoTrack;
  audio: InputAudioTrack | null;
  schedule: Schedule;
  scene: Scene;
  moves: Move[];
  options: ScheduleOptions;
  onProgress?: (fraction: number) => void;
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

/** Adds every sound effect that starts inside this buffer, with the old per-sound gains. */
function mixSfx(
  buffer: AudioBuffer,
  outStart: number,
  events: SfxEvent[],
  library: SfxLibrary,
  volume: number,
): AudioBuffer {
  const rate = buffer.sampleRate;
  const inside = events.filter(
    (event) => event.at >= outStart && event.at < outStart + buffer.duration,
  );
  if (inside.length === 0) return buffer;

  const mixed = new AudioBuffer({
    numberOfChannels: buffer.numberOfChannels,
    length: buffer.length,
    sampleRate: rate,
  });
  for (let channel = 0; channel < buffer.numberOfChannels; channel += 1) {
    mixed.copyToChannel(buffer.getChannelData(channel), channel);
  }

  for (const event of inside) {
    const sound = library[event.name];
    const gain = SFX_GAIN[event.name] * volume;
    const offset = Math.round((event.at - outStart) * rate);
    for (let channel = 0; channel < mixed.numberOfChannels; channel += 1) {
      const target = mixed.getChannelData(channel);
      const source = sound.getChannelData(channel % sound.numberOfChannels);
      const length = Math.min(sound.length, target.length - offset);
      for (let index = 0; index < length; index += 1) {
        const value = (target[offset + index] ?? 0) + (source[index] ?? 0) * gain;
        target[offset + index] = Math.max(-1, Math.min(1, value));
      }
    }
  }
  return mixed;
}

/**
 * Renders the schedule into an MP4: play segments are decoded frame by frame, pauses hold
 * one frame while the card is typed, and the audio gets silence there plus the sound
 * effects. Every frame goes through the same `Scene` the preview uses.
 */
export async function exportMp4(request: ExportRequest): Promise<Blob> {
  const total = request.schedule.duration;
  if (!(total > 0)) throw new Error("the export is empty");

  const { scene, options } = request;
  // Card text is drawn with a webfont: never encode a frame before it is available.
  await document.fonts.ready;

  const canvas = new OffscreenCanvas(scene.width, scene.height);
  const context = canvas.getContext("2d");
  if (context === null) throw new Error("the browser did not provide a 2D context for export");

  const frames = new CanvasSink(request.video, { poolSize: 2 });
  const target = new BufferTarget();
  const output = new Output({ format: new Mp4OutputFormat(), target });
  const videoSource = new CanvasSource(canvas, { codec: "avc", quality: QUALITY_MEDIUM });
  output.addVideoTrack(videoSource);

  const events = buildSfxEvents(request.schedule, request.moves, options);
  const audioSource =
    request.audio === null && events.length === 0
      ? null
      : new AudioBufferSource({ codec: "aac", quality: QUALITY_MEDIUM });
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
          scene.draw(context, frame.canvas, playState(request.moves, from), from);
          await videoSource.add(outTime, to - from);
          outTime += to - from;
          request.onProgress?.(Math.min(0.95, outTime / total));
        }
        continue;
      }

      const still = await frames.getCanvas(segment.at);
      const pausedIndex = Math.max(0, activeMoveIndex(request.moves, segment.at));
      const count = Math.max(1, Math.round(segment.duration * FRAME_RATE));
      for (let index = 0; index < count; index += 1) {
        if (still !== null) {
          scene.draw(context, still.canvas, pauseState(request.moves, pausedIndex, index / FRAME_RATE, options), segment.at);
        }
        await videoSource.add(outTime, 1 / FRAME_RATE);
        outTime += 1 / FRAME_RATE;
        request.onProgress?.(Math.min(0.95, outTime / total));
      }
    }

    if (audioSource !== null) {
      const audioSink = request.audio === null ? null : new AudioBufferSink(request.audio);
      const libraries = new Map<number, SfxLibrary>();
      const failed = new Set<number>();
      const libraryFor = async (rate: number): Promise<SfxLibrary | null> => {
        if (events.length === 0 || failed.has(rate)) return null;
        const cached = libraries.get(rate);
        if (cached !== undefined) return cached;
        try {
          const library = await loadSfx(
            new OfflineAudioContext(2, 1, rate),
            options.sfxTypingSet,
            options.sfxPauseSet,
          );
          libraries.set(rate, library);
          return library;
        } catch (error) {
          console.warn("sound set failed:", error);
          failed.add(rate);
          return null;
        }
      };

      let channels = 2;
      let sampleRate = 48_000;
      let audioOut = 0;

      for (const segment of request.schedule.segments) {
        if (segment.kind === "pause" || audioSink === null) {
          const seconds = segment.kind === "pause" ? segment.duration : segment.end - segment.start;
          const silence = silentBuffer(seconds, channels, sampleRate);
          const library = await libraryFor(sampleRate);
          await audioSource.add(
            library === null ? silence : mixSfx(silence, audioOut, events, library, options.sfxVolume),
          );
          audioOut += silence.duration;
          continue;
        }

        for await (const chunk of audioSink.buffers(segment.start, segment.end)) {
          const from = Math.max(chunk.timestamp, segment.start);
          const to = Math.min(chunk.timestamp + chunk.buffer.duration, segment.end);
          if (to <= from) continue;
          const slice = sliceAudio(chunk.buffer, from - chunk.timestamp, to - chunk.timestamp);
          channels = slice.numberOfChannels;
          sampleRate = slice.sampleRate;
          const library = await libraryFor(sampleRate);
          await audioSource.add(
            library === null ? slice : mixSfx(slice, audioOut, events, library, options.sfxVolume),
          );
          audioOut += slice.duration;
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
