import { exportMp4 } from "../media/export.ts";
import { openMedia } from "../media/probe.ts";
import type { OpenedMedia } from "../media/probe.ts";
import { el } from "../ui/dom.ts";
import { formatBytes, formatClock, messageOf } from "../ui/text.ts";
import type { Page } from "./page.ts";

const PLAY_LABEL = "▶";
const PAUSE_LABEL = "❚❚";

export function renderCreatePage(): Page {
  let media: OpenedMedia | null = null;
  let objectUrl: string | null = null;
  let downloadUrl: string | null = null;
  let fileName = "";

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
  const seek = el("input", {
    class: "seek",
    attrs: { type: "range", min: "0", max: "0", step: "0.01", value: "0", "aria-label": "Position" },
  });
  const clock = el("span", { class: "clock", text: "0:00 / 0:00" });
  const player = el("div", {
    class: "player",
    children: [video, el("div", { class: "transport", children: [playButton, seek, clock] })],
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

  function closeMedia(): void {
    video.pause();
    video.removeAttribute("src");
    video.load();
    revoke(objectUrl);
    revoke(downloadUrl);
    objectUrl = null;
    downloadUrl = null;
    media?.input.dispose();
    media = null;
    fileName = "";
    player.hidden = true;
    exportRow.hidden = true;
    download.hidden = true;
    meta.textContent = "";
    seek.value = "0";
    clock.textContent = "0:00 / 0:00";
  }

  function outputName(): string {
    const base = fileName.replace(/\.[^.]+$/, "") || "clip";
    return `${base}.mp4`;
  }

  async function openFile(file: File): Promise<void> {
    setStatus(`Opening ${file.name}…`);
    closeMedia();
    try {
      const opened = await openMedia(file);
      media = opened;
      fileName = file.name;
      objectUrl = URL.createObjectURL(file);
      video.src = objectUrl;
      seek.max = String(opened.info.duration);
      clock.textContent = `0:00 / ${formatClock(opened.info.duration)}`;
      meta.textContent = [
        `${opened.info.width}×${opened.info.height}`,
        formatClock(opened.info.duration),
        formatBytes(file.size),
        opened.info.hasAudio ? "with audio" : "without audio",
      ].join(" · ");
      player.hidden = false;
      exportRow.hidden = false;
      setStatus(file.name);
    } catch (error) {
      setStatus(messageOf(error));
    }
  }

  async function runExport(): Promise<void> {
    if (media === null) return;
    exportButton.disabled = true;
    download.hidden = true;
    progress.value = 0;
    setStatus("Rendering MP4…");
    try {
      const blob = await exportMp4({
        video: media.video,
        audio: media.audio,
        width: media.info.width,
        height: media.info.height,
        duration: media.info.duration,
        onProgress: (fraction) => {
          progress.value = fraction;
        },
      });
      revoke(downloadUrl);
      downloadUrl = URL.createObjectURL(blob);
      download.href = downloadUrl;
      download.download = outputName();
      download.hidden = false;
      setStatus(`Done: ${formatBytes(blob.size)}`);
    } catch (error) {
      setStatus(messageOf(error));
    } finally {
      exportButton.disabled = false;
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

  playButton.addEventListener("click", () => {
    if (video.paused) void video.play();
    else video.pause();
  });

  video.addEventListener("play", () => {
    playButton.textContent = PAUSE_LABEL;
  });
  video.addEventListener("pause", () => {
    playButton.textContent = PLAY_LABEL;
  });
  video.addEventListener("timeupdate", () => {
    seek.value = String(video.currentTime);
    clock.textContent = `${formatClock(video.currentTime)} / ${formatClock(video.duration)}`;
  });
  seek.addEventListener("input", () => {
    video.currentTime = Number(seek.value);
  });

  exportButton.addEventListener("click", () => {
    void runExport();
  });

  return { element, dispose: closeMedia };
}
