import { exportMp4 } from "../media/export.ts";
import { getClip, openClip, setClipRange } from "../session.ts";
import type { Clip } from "../session.ts";
import { el } from "../ui/dom.ts";
import { formatBytes, formatClock, formatTimestamp, messageOf } from "../ui/text.ts";
import { createTimeline } from "../ui/timeline.ts";
import type { Timeline } from "../ui/timeline.ts";
import type { Page } from "./page.ts";

const PLAY_LABEL = "▶";
const PAUSE_LABEL = "❚❚";

export function renderCreatePage(): Page {
  let clip: Clip | null = getClip();
  let timeline: Timeline | null = null;
  let downloadUrl: string | null = null;

  const fileInput = el("input", {
    class: "visually-hidden",
    attrs: { type: "file", accept: "video/*" },
  });
  const status = el("p", { class: "status-line", text: "No file selected." });
  const meta = el("p", { class: "meta" });

  const video = el("video", { class: "stage-video", attrs: { playsinline: "", preload: "auto" } });
  const playButton = el("button", {
    class: "button",
    text: PLAY_LABEL,
    attrs: { type: "button", "aria-label": "Play and pause" },
  });
  const clock = el("span", { class: "clock", text: "0:00.0 / 0:00.0" });
  const timelineHost = el("div", { class: "timeline-host" });
  const selection = el("p", { class: "selection" });
  const player = el("div", {
    class: "player",
    children: [
      video,
      el("div", { class: "transport", children: [playButton, clock] }),
      timelineHost,
      selection,
    ],
  });
  player.hidden = true;

  const exportButton = el("button", {
    class: "button button-primary",
    text: "Render MP4",
    attrs: { type: "button" },
  });
  const progress = el("progress", { class: "progress", attrs: { max: "1", value: "0" } });
  const download = el("a", { class: "button", text: "Download MP4" });
  download.hidden = true;
  const exportRow = el("div", { class: "export-row", children: [exportButton, progress, download] });
  exportRow.hidden = true;

  const dropZone = el("button", {
    class: "dropzone",
    text: "Choose a video file or drop it here",
    attrs: { type: "button" },
  });

  const element = el("section", {
    class: "page",
    children: [
      el("h1", { class: "page-title", text: "Create video" }),
      el("p", { class: "page-hint", text: "A local file: pick one or drop it into the window." }),
      dropZone,
      fileInput,
      status,
      meta,
      player,
      exportRow,
    ],
  });

  function setStatus(text: string): void {
    status.textContent = text;
  }

  function revoke(url: string | null): void {
    if (url !== null) URL.revokeObjectURL(url);
  }

  function updateSelection(): void {
    if (clip === null) return;
    const { start, end } = clip.range;
    selection.textContent = `Selection ${formatTimestamp(start)} – ${formatTimestamp(end)} · ${(end - start).toFixed(1)} s`;
    timeline?.setRange({ start, end });
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
    exportRow.hidden = false;
    download.hidden = true;
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
      },
      onSeek: (time) => {
        video.currentTime = time;
      },
    });
    timelineHost.replaceChildren(timeline.element);
    updateSelection();
    clock.textContent = `0:00.0 / ${formatTimestamp(info.duration)}`;
    setStatus(next.file.name);
  }

  async function openFile(file: File): Promise<void> {
    setStatus(`Opening ${file.name}…`);
    try {
      showClip(await openClip(file));
    } catch (error) {
      setStatus(messageOf(error));
    }
  }

  async function runExport(): Promise<void> {
    if (clip === null) return;
    exportButton.disabled = true;
    download.hidden = true;
    progress.value = 0;
    setStatus("Rendering MP4…");
    try {
      const blob = await exportMp4({
        video: clip.media.video,
        audio: clip.media.audio,
        width: clip.media.info.width,
        height: clip.media.info.height,
        range: clip.range,
        onProgress: (fraction) => {
          progress.value = fraction;
        },
      });
      revoke(downloadUrl);
      downloadUrl = URL.createObjectURL(blob);
      download.href = downloadUrl;
      download.download = `${clip.file.name.replace(/\.[^.]+$/, "") || "clip"}.mp4`;
      download.hidden = false;
      setStatus(`Done: ${formatBytes(blob.size)}`);
    } catch (error) {
      setStatus(messageOf(error));
    } finally {
      exportButton.disabled = false;
    }
  }

  function togglePlay(): void {
    if (clip === null) return;
    if (!video.paused) {
      video.pause();
      return;
    }
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

  video.addEventListener("loadedmetadata", () => {
    if (clip !== null) video.currentTime = clip.range.start;
  });
  video.addEventListener("play", () => {
    playButton.textContent = PAUSE_LABEL;
  });
  video.addEventListener("pause", () => {
    playButton.textContent = PLAY_LABEL;
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

  exportButton.addEventListener("click", () => {
    void runExport();
  });

  if (clip !== null) showClip(clip);

  return {
    element,
    dispose() {
      // The session keeps the blob URL and the tracks: only this page's video goes away.
      video.pause();
      video.removeAttribute("src");
      video.load();
      revoke(downloadUrl);
      downloadUrl = null;
    },
  };
}
