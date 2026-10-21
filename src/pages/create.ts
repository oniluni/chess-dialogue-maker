import { buildSchedule } from "../analysis/schedule.ts";
import type { Schedule } from "../analysis/schedule.ts";
import { SoundBoard } from "../audio/player.ts";
import { exportMp4 } from "../media/export.ts";
import { createScene } from "../render/scene.ts";
import { loadSceneAvatars } from "../render/avatar.ts";
import { clearAvatars } from "../render/avatar-store.ts";
import {
  getClip,
  getMoves,
  getNames,
  getPeople,
  getScheduleOptions,
  getTranscript,
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
import { createCastPanel } from "./people-panel.ts";
import { createSummaryPanel } from "./summary-panel.ts";
import { PreviewPlayer } from "./preview-player.ts";
import { createUrlPanel } from "./url-panel.ts";
import type { Trim } from "./url-panel.ts";

const PLAY_LABEL = "▶";
const PAUSE_LABEL = "❚❚";

export function renderCreatePage(): Page {
  let clip: Clip | null = getClip();
  let timeline: Timeline | null = null;
  let scene = createScene(getMoves(), getNames(), null, null, getTranscript());

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

  const summaryPanel = createSummaryPanel();

  const cast = createCastPanel({
    onAvatars: () => {
      rebuildScene();
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

  const subtitleToggle = el("input", { attrs: { type: "checkbox" } });
  subtitleToggle.checked = getScheduleOptions().subtitles;
  const subtitleOption = el("label", {
    class: "option",
    children: [subtitleToggle, el("span", { text: "Subtitles" })],
  });

  const formatSelect = el("select", {
    class: "select",
    attrs: { "aria-label": "Output format" },
    children: [
      el("option", { text: "Vertical · crop to fill", attrs: { value: "vertical" } }),
      el("option", { text: "Original · whole frame", attrs: { value: "original" } }),
    ],
  });
  formatSelect.value = getScheduleOptions().format;
  const formatField = el("label", {
    class: "field",
    children: [el("span", { text: "Format" }), formatSelect],
  });

  const timelineHost = el("div", { class: "timeline-host" });
  const selection = el("p", { class: "selection" });
  const playerRow = el("div", {
    class: "player",
    children: [
      canvas,
      video,
      el("div", { class: "transport", children: [playButton, clock] }),
      pauseOption,
      subtitleOption,
      formatField,
      createAudioPanel(soundBoard),
      timelineHost,
      selection,
    ],
  });
  playerRow.hidden = true;

  const preview = new PreviewPlayer({
    video,
    canvas,
    scene: () => scene,
    moves: getMoves,
    options: getScheduleOptions,
    schedule: () => (clip === null ? null : buildSchedule(clip.range, getMoves(), getScheduleOptions())),
    range: () => (clip === null ? null : clip.range),
    sound: soundBoard,
    onTime: (time, duration) => {
      timeline?.setPlayhead(time);
      clock.textContent = `${formatTimestamp(time)} / ${formatTimestamp(duration)}`;
    },
    onPlayingChange: (playing) => {
      playButton.textContent = playing ? PAUSE_LABEL : PLAY_LABEL;
    },
  });

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
      cast.element,
      summaryPanel,
      names,
      playerRow,
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

  function rebuildScene(): void {
    void (async (): Promise<void> => {
      const avatars = await loadSceneAvatars();
      scene = createScene(getMoves(), getNames(), avatars, getPeople()?.frames ?? null, getTranscript());
      canvas.width = scene.width;
      canvas.height = scene.height;
      preview.draw();
    })();
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
    playerRow.hidden = false;
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
        preview.draw();
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
    clearAvatars();
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

  playButton.addEventListener("click", () => preview.toggle());
  pauseToggle.addEventListener("change", () => {
    setScheduleOptions({ pauseOnMove: pauseToggle.checked });
    updateSelection();
  });
  subtitleToggle.addEventListener("change", () => {
    setScheduleOptions({ subtitles: subtitleToggle.checked });
    rebuildScene();
  });
  formatSelect.addEventListener("change", () => {
    setScheduleOptions({ format: formatSelect.value === "original" ? "original" : "vertical" });
    rebuildScene();
  });
  whiteName.addEventListener("input", () => {
    setNames({ white: whiteName.value });
    rebuildScene();
  });
  blackName.addEventListener("input", () => {
    setNames({ black: blackName.value });
    rebuildScene();
  });

  preview.start();
  if (clip !== null) showClip(clip);
  // The webfont may arrive after the first frame; redraw once it is ready.
  void document.fonts.ready.then(() => {
    preview.draw();
  });

  return {
    element,
    dispose() {
      preview.dispose();
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
