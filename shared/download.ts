/** Download contracts between the UI and the local yt-dlp backend. */

export type DownloadState = "running" | "done" | "error";

export interface DownloadStatus {
  state: DownloadState;
  progress: number;
  message: string;
  file: { name: string; size: number } | null;
}

/** Extra seconds downloaded around a fragment; the exact range stays in the editor. */
export const SECTION_MARGIN = 3;
