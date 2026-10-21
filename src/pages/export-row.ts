import { el } from "../ui/dom.ts";

export interface ExportRow {
  element: HTMLElement;
  setBusy(busy: boolean): void;
  setProgress(fraction: number): void;
  showDownload(blob: Blob, name: string): void;
  hideDownload(): void;
  dispose(): void;
}

/** The render button, its progress and the download link. */
export function createExportRow(onRender: () => void): ExportRow {
  const button = el("button", { class: "button button-primary", text: "Render MP4", attrs: { type: "button" } });
  const progress = el("progress", { class: "progress", attrs: { max: "1", value: "0" } });
  const download = el("a", { class: "button", text: "Download MP4" });
  download.hidden = true;

  const element = el("div", { class: "export-row", children: [button, progress, download] });
  element.hidden = true;

  let url: string | null = null;

  button.addEventListener("click", onRender);

  return {
    element,
    setBusy(busy) {
      button.disabled = busy;
    },
    setProgress(fraction) {
      progress.value = fraction;
    },
    showDownload(blob, name) {
      if (url !== null) URL.revokeObjectURL(url);
      url = URL.createObjectURL(blob);
      download.href = url;
      download.download = name;
      download.hidden = false;
    },
    hideDownload() {
      download.hidden = true;
    },
    dispose() {
      if (url !== null) URL.revokeObjectURL(url);
      url = null;
    },
  };
}
