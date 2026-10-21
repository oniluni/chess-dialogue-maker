import type { DownloadStatus } from "../../shared/download.ts";
import { errorMessage, isRecord } from "../net/http.ts";

export async function startDownload(url: string, start: number | null, end: number | null): Promise<string> {
  const response = await fetch("/api/download", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ url, start, end }),
  });
  if (!response.ok) throw new Error(await errorMessage(response));

  const data: unknown = await response.json();
  const id = isRecord(data) ? data["id"] : undefined;
  if (typeof id !== "string" || id === "") throw new Error("the server did not return a download id");
  return id;
}

export async function downloadStatus(id: string): Promise<DownloadStatus> {
  const response = await fetch(`/api/download/${id}`);
  if (!response.ok) throw new Error(await errorMessage(response));

  const data: unknown = await response.json();
  if (!isRecord(data)) throw new Error("unexpected download status");
  const state = data["state"];
  if (state !== "running" && state !== "done" && state !== "error") {
    throw new Error("unexpected download state");
  }

  const rawFile = data["file"];
  const file = isRecord(rawFile) && typeof rawFile["name"] === "string" && typeof rawFile["size"] === "number"
    ? { name: rawFile["name"], size: rawFile["size"] }
    : null;

  return {
    state,
    progress: typeof data["progress"] === "number" ? data["progress"] : 0,
    message: typeof data["message"] === "string" ? data["message"] : "",
    file,
  };
}

export async function fetchDownloadFile(id: string, name: string): Promise<File> {
  const response = await fetch(`/api/download/${id}/file`);
  if (!response.ok) throw new Error(await errorMessage(response));
  const blob = await response.blob();
  if (blob.size === 0) throw new Error("the download is empty");
  return new File([blob], name, { type: blob.type || "video/mp4" });
}

/** Drops the temporary file on the server; the clip already lives in the browser. */
export async function forgetDownload(id: string): Promise<void> {
  await fetch(`/api/download/${id}`, { method: "DELETE" });
}
