import { SECTION_MARGIN } from "../../shared/download.ts";
import { downloadStatus, fetchDownloadFile, forgetDownload, startDownload } from "../download/client.ts";
import { el } from "../ui/dom.ts";
import { messageOf, parseTimestamp } from "../ui/text.ts";

/** The part of the downloaded file the person asked for, relative to the file's start. */
export interface Trim {
  start: number;
  end: number | null;
}

export interface UrlPanel {
  element: HTMLElement;
  /** Puts a link found by the search panel into the field. */
  setUrl(url: string): void;
  dispose(): void;
}

const POLL_MS = 600;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function fileName(url: string): string {
  try {
    const host = new URL(url).hostname.replace(/^www\./, "");
    return `${host === "" ? "video" : host}.mp4`;
  } catch {
    return "video.mp4";
  }
}

/** Downloads a link (or a fragment of it) through the local yt-dlp and hands over the file. */
export function createUrlPanel(onFile: (file: File, trim: Trim | null) => void): UrlPanel {
  let jobId: string | null = null;
  let disposed = false;

  const urlInput = el("input", {
    class: "text-input",
    attrs: { type: "url", placeholder: "https://www.youtube.com/watch?v=â€¦", "aria-label": "Video link" },
  });
  const fromInput = el("input", {
    class: "text-input time-input",
    attrs: { type: "text", placeholder: "0:00", "aria-label": "Fragment start" },
  });
  const toInput = el("input", {
    class: "text-input time-input",
    attrs: { type: "text", placeholder: "end", "aria-label": "Fragment end" },
  });
  const downloadButton = el("button", { class: "button", text: "Download", attrs: { type: "submit" } });
  const cancelButton = el("button", { class: "button", text: "Cancel", attrs: { type: "button" } });
  cancelButton.hidden = true;
  const progress = el("progress", { class: "progress", attrs: { max: "1", value: "0" } });
  progress.hidden = true;
  const message = el("span", { class: "url-message" });

  const form = el("form", {
    class: "url-form",
    children: [
      el("label", { class: "field field-wide", children: [el("span", { text: "Link" }), urlInput] }),
      el("label", { class: "field", children: [el("span", { text: "From" }), fromInput] }),
      el("label", { class: "field", children: [el("span", { text: "To" }), toInput] }),
      downloadButton,
      cancelButton,
    ],
  });
  const element = el("div", {
    class: "url-panel",
    children: [form, el("div", { class: "url-status", children: [progress, message] })],
  });

  function show(text: string): void {
    message.textContent = text;
  }

  function setBusy(busy: boolean): void {
    downloadButton.disabled = busy;
    cancelButton.hidden = !busy;
    progress.hidden = !busy;
    if (!busy) progress.value = 0;
  }

  function trimFor(from: number | null, to: number | null): Trim | null {
    if (from === null && to === null) return null;
    const sectionStart = Math.max(0, (from ?? 0) - SECTION_MARGIN);
    const start = (from ?? 0) - sectionStart;
    const end = to === null ? null : start + (to - (from ?? 0));
    return { start, end };
  }

  async function poll(id: string, url: string, from: number | null, to: number | null): Promise<void> {
    while (!disposed && jobId === id) {
      const status = await downloadStatus(id);
      progress.value = status.progress;
      show(status.message);

      if (status.state === "error") {
        await forgetDownload(id);
        return;
      }
      if (status.state === "done") {
        const file = await fetchDownloadFile(id, fileName(url));
        await forgetDownload(id);
        progress.value = 1;
        show("Downloaded.");
        onFile(file, trimFor(from, to));
        return;
      }
      await sleep(POLL_MS);
    }
  }

  async function submit(): Promise<void> {
    if (jobId !== null) return;
    const url = urlInput.value.trim();
    if (url === "") {
      show("Paste a video link first.");
      return;
    }

    const from = parseTimestamp(fromInput.value);
    const to = parseTimestamp(toInput.value);
    if (Number.isNaN(from) || Number.isNaN(to)) {
      show("Times look wrong: write 1:02 or 62.");
      return;
    }
    if (from !== null && to !== null && to <= from) {
      show("The end must be after the start.");
      return;
    }

    setBusy(true);
    progress.value = 0;
    show("Starting the downloadâ€¦");
    try {
      const id = await startDownload(url, from, to);
      jobId = id;
      await poll(id, url, from, to);
    } catch (error) {
      if (!disposed) show(messageOf(error));
    } finally {
      if (!disposed) {
        jobId = null;
        setBusy(false);
      }
    }
  }

  async function cancel(): Promise<void> {
    if (jobId === null) return;
    const id = jobId;
    jobId = null;
    try {
      await forgetDownload(id);
      show("Cancelled.");
    } catch (error) {
      console.warn("could not drop the download:", error);
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    void submit();
  });
  cancelButton.addEventListener("click", () => {
    void cancel();
  });

  return {
    element,
    setUrl(url: string) {
      urlInput.value = url;
    },
    dispose() {
      disposed = true;
      if (jobId !== null) void forgetDownload(jobId).catch((error: unknown) => console.warn("could not drop the download:", error));
      jobId = null;
    },
  };
}

