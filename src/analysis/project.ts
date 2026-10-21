import { getAvatars, setAvatars } from "../render/avatar-store.ts";
import { getStyle, setStyle } from "../render/style.ts";
import { getSoundEvents, setSoundEvents } from "../analysis/sound-events.ts";
import {
  clearMovesBackup,
  getCast,
  getLlmChoice,
  getMoves,
  getNames,
  getPeople,
  getScheduleOptions,
  getSpeakerHints,
  getTranscript,
  setCast,
  setDialogue,
  setLlmChoice,
  setMoves,
  setNames,
  setPeople,
  setRecovery,
  setScheduleOptions,
  setSpeakerHints,
  setTranscript,
} from "../session.ts";
import { isRecord, errorMessage } from "../net/http.ts";
import { parseProjectData } from "./project-format.ts";
import type { ProjectData } from "./project-format.ts";

export type { ProjectData } from "./project-format.ts";

export interface ProjectSummary {
  id: string;
  name: string;
  savedAt: string;
}

function summaryOf(value: unknown): ProjectSummary | null {
  if (!isRecord(value)) return null;
  const id = value["id"];
  const name = value["name"];
  const savedAt = value["savedAt"];
  if (typeof id !== "string" || typeof name !== "string" || typeof savedAt !== "string") return null;
  return { id, name, savedAt };
}

/** Everything the document holds; the clip itself is re-opened by hand. */
export function projectData(): ProjectData {
  return {
    version: 1,
    moves: getMoves(),
    transcript: getTranscript(),
    names: getNames(),
    options: getScheduleOptions(),
    speakers: getSpeakerHints(),
    people: getPeople(),
    cast: getCast(),
    avatars: getAvatars(),
    sounds: getSoundEvents(),
    styleId: getStyle().id,
    llm: getLlmChoice(),
  };
}

/** Replaces the document in one visible step, after the whole project has been validated. */
export function applyProject(value: unknown): void {
  const data = parseProjectData(value);
  setTranscript(data.transcript);
  setSpeakerHints(data.speakers);
  setMoves(data.moves);
  clearMovesBackup();
  setNames(data.names);
  setScheduleOptions(data.options);
  setPeople(data.people);
  setCast(data.cast);
  setAvatars(data.avatars);
  setSoundEvents(data.sounds);
  setStyle(data.styleId);
  setLlmChoice(data.llm);
  setRecovery(null);
  setDialogue(null);
}

export async function saveProjectOnServer(name: string): Promise<ProjectSummary> {
  const response = await fetch("/api/projects", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ name, data: projectData() }),
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  const summary = summaryOf(await response.json());
  if (summary === null) throw new Error("The server returned an unexpected project summary");
  return summary;
}

export async function listProjectsOnServer(): Promise<ProjectSummary[]> {
  const response = await fetch("/api/projects");
  if (!response.ok) throw new Error(await errorMessage(response));
  const body: unknown = await response.json();
  const projects = isRecord(body) ? body["projects"] : undefined;
  if (!Array.isArray(projects)) throw new Error("The server returned an unexpected project list");
  return projects.flatMap((project) => {
    const summary = summaryOf(project);
    return summary === null ? [] : [summary];
  });
}

export async function openProjectFromServer(id: string): Promise<ProjectData> {
  const response = await fetch(`/api/projects?id=${encodeURIComponent(id)}`);
  if (!response.ok) throw new Error(await errorMessage(response));
  const body: unknown = await response.json();
  if (!isRecord(body)) throw new Error("The server returned an unexpected project");
  return parseProjectData(body["data"]);
}

export async function deleteProjectOnServer(id: string): Promise<boolean> {
  const response = await fetch(`/api/projects?id=${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!response.ok) throw new Error(await errorMessage(response));
  const body: unknown = await response.json();
  return isRecord(body) && body["removed"] === true;
}
