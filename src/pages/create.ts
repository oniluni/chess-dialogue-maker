import { activeMoveIndex } from "../analysis/move.ts";
import { buildSchedule } from "../analysis/schedule.ts";
import type { PauseSegment, Schedule } from "../analysis/schedule.ts";
import { pauseSfxEvents } from "../audio/events.ts";
import { SoundBoard } from "../audio/player.ts";
import { exportMp4 } from "../media/export.ts";
import { createScene } from "../render/scene.ts";
import { pauseState, playState } from "../render/state.ts";
import {
  getClip,
  getMoves,
  getNames,
  getScheduleOptions,
  openClip,
  setClipRange,
  setNames,
  setScheduleOptions,
} from "../session.ts";
import type { Clip } from "../session.ts";
import { el } from "../ui/dom.ts";
import { formatBytes, formatClock, formatTimestamp, messageOf } from "../ui/text.ts";
import { createTimeline } from "../ui/timeline.ts";
import type { Timeline } from "../ui/timeline.ts";
import { createAnalysisPanel } from "./analysis-panel.ts";
import { createAudioPanel } from "./audio-panel.ts";
import { createExportRow } from "./export-row.ts";
import type { Page } from "./page.ts";
import { createUrlPanel } from "./url-panel.ts";
import type { Trim } from "./url-panel.ts";

const PLAY_LABEL = "▶";
const PAUSE_LABEL = "❚❚";

interface QueuedPause {
  at: number;
  duration: number;
  done: boolean;
}

export function renderCreatePage(): Page {
  let clip: Clip | null = getClip();
  let timeline: Timeline | null = null;
  let scene = createScene(getMoves(), getNames());
  let pauseQueue: QueuedPause[] = [];
  let pauseTimer: number | null = null;
  let frameLoop: number | null = null;
  let holding = false;
  let holdStartedAt = 0;
  let holdIndex = -1;
  let lastMoveIndex = -1;

  const soundBoard = new SoundBoard();
  soundBoard.setVolume(getScheduleOptions().sfxVolume);
  soundBoard.setSets(getScheduleOptions().sfxTypingSet, getScheduleOptions().sfxPauseSet);

  const fileInput = el("input", {
    class: "visually-hidden",
    attrs: { type: "file", accept: "video/*" },
  });
  const status = el("p", { class: "status-line", text: "No file selected." });
  const meta = el("p", { class: "meta" });

  const video = el("video", { class: "visually-hidden", attrs: { playsinline: "", preload: "auto" } });
  const canvas = el("canvas", {
    class: "stage",
    attrs: { width: String(scene.width), height: String(scene.height) },
  });
  const context = canvas.getContext("2d");

  const whiteName = el("input", {
    class: "name-input",
    attrs: { type: "text", value: getNames().white, "aria-label": "White player" },
  });
  const blackName = el("input", {
    class: "name-input",
    attrs: { type: "text", value: getNames().black, "aria-label": "Black player" },
  });
  const names = el("div", {
    class: "names",
    children: [
      el("label", { class: "name-field", children: [el("span", { text: "White" }), whiteName] }),
      el("label", { class: "name-field", children: [el("span", { text: "Black" }), blackName] }),
    ],
  });

  const analysis = createAnalysisPanel({
    onMoves: () => {
      rebuildScene();
      updateSelection();
    },
  });

  const playButton = el("button", {
    class: "button",
    text: PLAY_LABEL,
    attrs: { type: "button", "aria-label": "Play and pause" },
  });
  const clock = el("span", { class: "clock", text: "0:00.0 / 0:00.0" });
  const pauseToggle = el("input", { attrs: { type: "checkbox" } });
  pauseToggle.checked = getScheduleOptions().pauseOnMove;
  const pauseOption = el("label", {
    class: "option",
    children: [pauseToggle, el("span", { text: "Pause on each move" })],
  });

  const timelineHost = el("div", { class: "timeline-host" });
  const selection = el("p", { class: "selection" });
  const player = el("div", {
    class: "player",
    children: [
      canvas,
      video,
      el("div", { class: "transport", children: [playButton, clock] }),
      pauseOption,
      createAudioPanel(soundBoard),
      timelineHost,
      selection,
    ],
  });
  player.hidden = true;

  const exportRow = createExportRow(() => {
    void runExport();
  });

  const dropZone = el("button", {
    class: "dropzone",
    text: "Choose a video file or drop it here",
    attrs: { type: "button" },
  });

  const urlPanel = createUrlPanel((file, trim) => {
    void openFile(file, trim);
  });

  const element = el("section", {
    class: "page",
    children: [
      el("h1", { class: "page-title", text: "Create video" }),
      el("p", {
        class: "page-hint",
        text: "A local file or a link: open it here, then trim, analyse and render.",
      }),
      dropZone,
      urlPanel.element,
      fileInput,
      status,
      meta,
      analysis.element,
      names,
      player,
      exportRow.element,
    ],
  });

  function setStatus(text: string): void {
    status.textContent = text;
  }

  function scheduleFor(next: Clip): Schedule {
    return buildSchedule(next.range, getMoves(), getScheduleOptions());
  }

  function updateSelection(): void {
    if (clip === null) return;
    const { start, end } = clip.range;
    const schedule = scheduleFor(clip);
    const extra = schedule.duration > end - start + 0.01 ? ` · output ${formatTimestamp(schedule.duration)}` : "";
    selection.textContent = `Selection ${formatTimestamp(start)} – ${formatTimestamp(end)} · ${(end - start).toFixed(1)} s${extra}`;
    timeline?.setRange({ start, end });
  }

  function drawPreview(): void {
    if (clip === null || context === null || video.readyState < 2) return;
    const options = getScheduleOptions();
    const state =
      holding && holdIndex >= 0
        ? pauseState(getMoves(), holdIndex, (performance.now() - holdStartedAt) / 1000, options)
        : playState(getMoves(), video.currentTime);
    scene.draw(context, video, state);
  }

  function rebuildScene(): void {
    scene = createScene(getMoves(), getNames());
    canvas.width = scene.width;
    canvas.height = scene.height;
    drawPreview();
  }

  function showClip(next: Clip): void {
    clip = next;
    const info = next.media.info;
    video.src = next.objectUrl;
    meta.textContent = [
      `${info.width}×${info.height}`,
      formatClock(info.duration),
      formatBytes(next.file.size),
      info.hasAudio ? "with audio" : "without audio",
    ].join(" · ");
    player.hidden = false;
    exportRow.element.hidden = false;
    exportRow.hideDownload();
    timeline = createTimeline({
      duration: info.duration,
      range: next.range,
      onChange: (range) => {
        const updated = setClipRange(range);
        if (updated === null) return;
        clip = updated;
        updateSelection();
        if (video.currentTime < range.start || video.currentTime > range.end) {
          video.currentTime = range.start;
        }
        drawPreview();
      },
      onSeek: (time) => {
        video.currentTime = time;
      },
    });
    timelineHost.replaceChildren(timeline.element);
    updateSelection();
    clock.textContent = `0:00.0 / ${formatTimestamp(info.duration)}`;
    analysis.refresh();
    setStatus(next.file.name);
  }

  async function openFile(file: File, trim: Trim | null = null): Promise<void> {
    setStatus(`Opening ${file.name}…`);
    try {
      const opened = await openClip(file);
      if (trim !== null) {
        const duration = opened.media.info.duration;
        setClipRange({ start: trim.start, end: Math.min(trim.end ?? duration, duration) });
      }
      showClip(opened);
    } catch (error) {
      setStatus(messageOf(error));
    }
  }

  async function runExport(): Promise<void> {
    if (clip === null) return;
    exportRow.setBusy(true);
    exportRow.hideDownload();
    exportRow.setProgress(0);
    setStatus("Rendering MP4…");
    try {
      const blob = await exportMp4({
        video: clip.media.video,
        audio: clip.media.audio,
        schedule: scheduleFor(clip),
        scene,
        moves: getMoves(),
        options: getScheduleOptions(),
        onProgress: (fraction) => {
          exportRow.setProgress(fraction);
        },
      });
      exportRow.showDownload(blob, `${clip.file.name.replace(/\.[^.]+$/, "") || "clip"}.mp4`);
      setStatus(`Done: ${formatBytes(blob.size)}`);
    } catch (error) {
      setStatus(messageOf(error));
    } finally {
      exportRow.setBusy(false);
    }
  }

  function rebuildQueue(): void {
    if (clip === null) {
      pauseQueue = [];
      return;
    }
    const now = video.currentTime;
    pauseQueue = scheduleFor(clip)
      .segments.filter((segment): segment is PauseSegment => segment.kind === "pause")
      .map((segment) => ({ at: segment.at, duration: segment.duration, done: segment.at <= now + 0.02 }));
  }

  function cancelHold(): void {
    holding = false;
    holdIndex = -1;
    soundBoard.stopAll();
    if (pauseTimer !== null) {
      clearTimeout(pauseTimer);
      pauseTimer = null;
    }
  }

  function enterHold(next: QueuedPause): void {
    next.done = true;
    holding = true;
    holdIndex = Math.max(0, activeMoveIndex(getMoves(), next.at));
    holdStartedAt = performance.now();
    const options = getScheduleOptions();
    if (options.sfxEnabled && options.sfxVolume > 0) {
      const move = getMoves()[holdIndex];
      void soundBoard.playSequence(pauseSfxEvents(move?.explanation.length ?? 0, next.duration, options));
    }
    video.pause();
    video.currentTime = next.at;
    pauseTimer = window.setTimeout(() => {
      pauseTimer = null;
      holding = false;
      holdIndex = -1;
      if (!video.ended) void video.play();
    }, next.duration * 1000);
  }

  function startPreviewLoop(): void {
    if (frameLoop !== null) return;
    const step = (): void => {
      frameLoop = requestAnimationFrame(step);
      if (holding) {
        drawPreview();
        return;
      }
      if (video.paused) return;

      const index = activeMoveIndex(getMoves(), video.currentTime);
      if (index !== lastMoveIndex) {
        const options = getScheduleOptions();
        if (index >= 0 && !options.pauseOnMove && options.sfxEnabled) void soundBoard.play("move");
        lastMoveIndex = index;
      }

      const next = pauseQueue.find((pause) => !pause.done && video.currentTime >= pause.at);
      if (next !== undefined) {
        enterHold(next);
        return;
      }
      drawPreview();
    };
    frameLoop = requestAnimationFrame(step);
  }

  function stopPreviewLoop(): void {
    if (frameLoop !== null) cancelAnimationFrame(frameLoop);
    frameLoop = null;
  }

  function togglePlay(): void {
    if (clip === null) return;
    if (!video.paused) {
      video.pause();
      return;
    }
    void soundBoard.unlock();
    const { start, end } = clip.range;
    if (video.currentTime < start || video.currentTime >= end) video.currentTime = start;
    void video.play();
  }

  dropZone.addEventListener("click", () => fileInput.click());
  dropZone.addEventListener("dragover", (event) => {
    event.preventDefault();
    dropZone.classList.add("is-over");
  });
  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("is-over"));
  dropZone.addEventListener("drop", (event) => {
    event.preventDefault();
    dropZone.classList.remove("is-over");
    const file = event.dataTransfer?.files[0];
    if (file !== undefined) void openFile(file);
  });

  fileInput.addEventListener("change", () => {
    const file = fileInput.files?.[0];
    if (file !== undefined) void openFile(file);
  });

  playButton.addEventListener("click", togglePlay);
  pauseToggle.addEventListener("change", () => {
    setScheduleOptions({ pauseOnMove: pauseToggle.checked });
    updateSelection();
  });
  whiteName.addEventListener("input", () => {
    setNames({ white: whiteName.value });
    rebuildScene();
  });
  blackName.addEventListener("input", () => {
    setNames({ black: blackName.value });
    rebuildScene();
  });

  video.addEventListener("loadedmetadata", () => {
    if (clip !== null) video.currentTime = clip.range.start;
  });
  video.addEventListener("loadeddata", drawPreview);
  video.addEventListener("play", () => {
    playButton.textContent = PAUSE_LABEL;
    rebuildQueue();
  });
  video.addEventListener("pause", () => {
    playButton.textContent = PLAY_LABEL;
    if (!holding) cancelHold();
  });
  video.addEventListener("seeked", () => {
    lastMoveIndex = -1;
    drawPreview();
    if (!video.paused) rebuildQueue();
  });
  video.addEventListener("timeupdate", () => {
    const time = video.currentTime;
    timeline?.setPlayhead(time);
    clock.textContent = `${formatTimestamp(time)} / ${formatTimestamp(video.duration)}`;
    if (clip !== null && !video.paused && time >= clip.range.end) {
      video.pause();
      video.currentTime = clip.range.end;
    }
  });

  startPreviewLoop();
  if (clip !== null) showClip(clip);
  // The webfont may arrive after the first frame; redraw once it is ready.
  void document.fonts.ready.then(() => {
    drawPreview();
  });

  return {
    element,
    dispose() {
      stopPreviewLoop();
      cancelHold();
      soundBoard.dispose();
      analysis.dispose();
      exportRow.dispose();
      urlPanel.dispose();
      video.pause();
      video.removeAttribute("src");
      video.load();
    },
  };
}
