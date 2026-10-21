import type { FacesResult } from "../../shared/people.ts";
import type { LlmChoice } from "../session.ts";
import type { Move, Side } from "../analysis/move.ts";
import type { ScheduleOptions } from "../analysis/schedule.ts";
import { getSoundEvents, setSoundEvents } from "../analysis/sound-events.ts";
import type { SoundEvent } from "../analysis/sound-events.ts";
import type { TranscriptSegment } from "../analysis/transcript.ts";
import type { Names } from "../names.ts";
import { getAvatars, setAvatars } from "../render/avatar-store.ts";
import { getStyle, setStyle } from "../render/style.ts";
import {
  getCast,
  getLlmChoice,
  getMoves,
  getNames,
  getPeople,
  getScheduleOptions,
  getSpeakerHints,
  getTranscript,
  setCast,
  setLlmChoice,
  setMoves,
  setNames,
  setPeople,
  setScheduleOptions,
  setSpeakerHints,
  setTranscript,
} from "../session.ts";
import type { CastEntry, SpeakerHints } from "../session.ts";

export interface ProjectData {
  version: 1;
  moves: Move[];
  transcript: TranscriptSegment[];
  names: Names;
  options: ScheduleOptions;
  speakers: SpeakerHints | null;
  people: FacesResult | null;
  cast: CastEntry[];
  avatars: Record<Side, string | null>;
  sounds: SoundEvent[];
  styleId: string;
  llm: LlmChoice;
}

export interface ProjectSummary {
  id: string;
  name: string;
  savedAt: string;
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

export function applyProject(data: ProjectData): void {
  setTranscript(data.transcript);
  setSpeakerHints(data.speakers);
  setMoves(data.moves);
  setNames(data.names);
  setScheduleOptions(data.options);
  setPeople(data.people);
  setCast(data.cast);
  setAvatars(data.avatars);
  setSoundEvents(data.sounds);
  setStyle(data.styleId);
  setLlmChoice(data.llm);
}

export async function saveProjectOnServer(name: string): Promise<ProjectSummary> {
  const response = await fetch("/api/projects", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ name, data: projectData() }),
  });
  if (!response.ok) throw new Error(`The project was not saved (HTTP ${response.status})`);
  return (await response.json()) as ProjectSummary;
}

export async function listProjectsOnServer(): Promise<ProjectSummary[]> {
  const response = await fetch("/api/projects");
  if (!response.ok) throw new Error(`The project list is unavailable (HTTP ${response.status})`);
  const body: unknown = await response.json();
  const projects = (body as { projects?: unknown }).projects;
  return Array.isArray(projects) ? (projects as ProjectSummary[]) : [];
}

export async function openProjectFromServer(id: string): Promise<ProjectData> {
  const response = await fetch(`/api/projects?id=${encodeURIComponent(id)}`);
  if (!response.ok) throw new Error(`The project could not be opened (HTTP ${response.status})`);
  const body = (await response.json()) as { data?: unknown };
  const data = body.data as ProjectData | undefined;
  if (data === undefined || data.version !== 1) throw new Error("The project file has an unknown version");
  return data;
}

export async function deleteProjectOnServer(id: string): Promise<boolean> {
  const response = await fetch(`/api/projects?id=${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!response.ok) throw new Error(`The project was not deleted (HTTP ${response.status})`);
  const body = (await response.json()) as { removed?: boolean };
  return body.removed === true;
}

