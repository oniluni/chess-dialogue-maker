import { activeMoveIndex } from "../analysis/move.ts";
import type { Move } from "../analysis/move.ts";
import type { PauseSegment, Schedule, ScheduleOptions } from "../analysis/schedule.ts";
import { pauseSfxEvents } from "../audio/events.ts";
import type { SoundBoard } from "../audio/player.ts";
import type { TimeRange } from "../media/range.ts";
import type { Scene } from "../render/scene.ts";
import { pauseState, playState } from "../render/state.ts";

interface QueuedPause {
  at: number;
  duration: number;
  done: boolean;
}

export interface PreviewHost {
  video: HTMLVideoElement;
  canvas: HTMLCanvasElement;
  scene: () => Scene;
  moves: () => Move[];
  options: () => ScheduleOptions;
  /** `null` while no clip is open. */
  schedule: () => Schedule | null;
  range: () => TimeRange | null;
  sound: SoundBoard;
  onTime: (time: number, duration: number) => void;
  onPlayingChange: (playing: boolean) => void;
}

/**
 * The live preview: draws the current frame, freezes on every move while the card is typed,
 * plays the sound cues and keeps the transport and the timeline in sync.
 */
export class PreviewPlayer {
  private readonly host: PreviewHost;
  private readonly context: CanvasRenderingContext2D | null;

  private queue: QueuedPause[] = [];
  private timer: number | null = null;
  private loop: number | null = null;
  private holding = false;
  private holdStartedAt = 0;
  private holdIndex = -1;
  private lastMoveIndex = -1;

  constructor(host: PreviewHost) {
    this.host = host;
    this.context = host.canvas.getContext("2d");

    host.video.addEventListener("loadedmetadata", this.onMetadata);
    host.video.addEventListener("loadeddata", this.onLoaded);
    host.video.addEventListener("play", this.onPlay);
    host.video.addEventListener("pause", this.onPause);
    host.video.addEventListener("seeked", this.onSeeked);
    host.video.addEventListener("timeupdate", this.onTimeUpdate);
  }

  start(): void {
    if (this.loop !== null) return;
    const step = (): void => {
      this.loop = requestAnimationFrame(step);
      this.tick();
    };
    this.loop = requestAnimationFrame(step);
  }

  toggle(): void {
    const { video } = this.host;
    const range = this.host.range();
    if (range === null) return;

    if (!video.paused) {
      video.pause();
      return;
    }
    void this.host.sound.unlock();
    if (video.currentTime < range.start || video.currentTime >= range.end) {
      video.currentTime = range.start;
    }
    void video.play();
  }

  draw(): void {
    const { video, moves } = this.host;
    const range = this.host.range();
    if (range === null || this.context === null || video.readyState < 2) return;

    const options = this.host.options();
    const state =
      this.holding && this.holdIndex >= 0
        ? pauseState(moves(), this.holdIndex, (performance.now() - this.holdStartedAt) / 1000, options)
        : playState(moves(), video.currentTime);
    this.host.scene().draw(this.context, video, state);
  }

  dispose(): void {
    this.stopLoop();
    this.cancelHold();
    const { video } = this.host;
    video.removeEventListener("loadedmetadata", this.onMetadata);
    video.removeEventListener("loadeddata", this.onLoaded);
    video.removeEventListener("play", this.onPlay);
    video.removeEventListener("pause", this.onPause);
    video.removeEventListener("seeked", this.onSeeked);
    video.removeEventListener("timeupdate", this.onTimeUpdate);
  }

  private stopLoop(): void {
    if (this.loop !== null) cancelAnimationFrame(this.loop);
    this.loop = null;
  }

  private tick(): void {
    const { video } = this.host;
    if (this.holding) {
      this.draw();
      return;
    }
    if (video.paused) return;

    this.playMoveSound();
    const next = this.queue.find((pause) => !pause.done && video.currentTime >= pause.at);
    if (next !== undefined) {
      this.enterHold(next);
      return;
    }
    this.draw();
  }

  /** Without pauses every move still gets a click when the card changes. */
  private playMoveSound(): void {
    const index = activeMoveIndex(this.host.moves(), this.host.video.currentTime);
    if (index === this.lastMoveIndex) return;
    const options = this.host.options();
    if (index >= 0 && !options.pauseOnMove && options.sfxEnabled) void this.host.sound.play("move");
    this.lastMoveIndex = index;
  }

  private rebuildQueue(): void {
    const schedule = this.host.schedule();
    if (schedule === null) {
      this.queue = [];
      return;
    }
    const now = this.host.video.currentTime;
    this.queue = schedule.segments
      .filter((segment): segment is PauseSegment => segment.kind === "pause")
      .map((segment) => ({ at: segment.at, duration: segment.duration, done: segment.at <= now + 0.02 }));
  }

  private cancelHold(): void {
    this.holding = false;
    this.holdIndex = -1;
    this.host.sound.stopAll();
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }

  private enterHold(next: QueuedPause): void {
    const { video, sound } = this.host;
    next.done = true;
    this.holding = true;
    this.holdIndex = Math.max(0, activeMoveIndex(this.host.moves(), next.at));
    this.holdStartedAt = performance.now();

    const options = this.host.options();
    if (options.sfxEnabled && options.sfxVolume > 0) {
      const move = this.host.moves()[this.holdIndex];
      void sound.playSequence(pauseSfxEvents(move?.explanation.length ?? 0, next.duration, options));
    }

    video.pause();
    video.currentTime = next.at;
    this.timer = window.setTimeout(() => {
      this.timer = null;
      this.holding = false;
      this.holdIndex = -1;
      if (!video.ended) void video.play();
    }, next.duration * 1000);
  }

  private readonly onMetadata = (): void => {
    const range = this.host.range();
    if (range !== null) this.host.video.currentTime = range.start;
  };

  private readonly onLoaded = (): void => {
    this.draw();
  };

  private readonly onPlay = (): void => {
    this.host.onPlayingChange(true);
    this.rebuildQueue();
  };

  private readonly onPause = (): void => {
    this.host.onPlayingChange(false);
    if (!this.holding) this.cancelHold();
  };

  private readonly onSeeked = (): void => {
    this.lastMoveIndex = -1;
    this.draw();
    if (!this.host.video.paused) this.rebuildQueue();
  };

  private readonly onTimeUpdate = (): void => {
    const { video } = this.host;
    this.host.onTime(video.currentTime, video.duration);

    const range = this.host.range();
    if (range !== null && !video.paused && video.currentTime >= range.end) {
      video.pause();
      video.currentTime = range.end;
    }
  };
}
