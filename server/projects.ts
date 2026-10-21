import { randomUUID } from "node:crypto";
import { existsSync } from "node:fs";
import { mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

const ROOT = join(import.meta.dirname, "..", ".cache", "projects");
const ID = /^[a-z0-9-]{4,80}$/;
const MAX_NAME = 200;

export interface ProjectSummary {
  id: string;
  name: string;
  savedAt: string;
}

export interface ProjectRecord extends ProjectSummary {
  data: unknown;
}

interface Meta {
  name: string;
  savedAt: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isMissing(error: unknown): boolean {
  return typeof error === "object" && error !== null && "code" in error && error.code === "ENOENT";
}

function folder(id: string): string {
  if (!ID.test(id)) throw new Error("the project id is invalid");
  return join(ROOT, id);
}

function slug(name: string): string {
  const clean = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
  return `${clean === "" ? "project" : clean}-${Date.now().toString(36)}-${randomUUID().slice(0, 6)}`;
}

function metaOf(value: unknown): Meta | null {
  if (!isRecord(value)) return null;
  const name = value["name"];
  const savedAt = value["savedAt"];
  if (typeof name !== "string" || name.trim() === "") return null;
  if (typeof savedAt !== "string" || Number.isNaN(Date.parse(savedAt))) return null;
  return { name, savedAt };
}

function parseMeta(text: string): Meta {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Error("the project metadata is not valid JSON");
  }
  const meta = metaOf(value);
  if (meta === null) throw new Error("the project metadata is incomplete");
  return meta;
}

export async function saveProject(name: string, data: unknown): Promise<ProjectSummary> {
  const clean = name.replace(/\s+/g, " ").trim().slice(0, MAX_NAME) || "Untitled";
  const id = slug(clean);
  const dir = folder(id);
  const serialized = JSON.stringify(data);
  if (serialized === undefined) throw new Error("the project data cannot be stored");
  const meta: Meta = { name: clean, savedAt: new Date().toISOString() };
  await mkdir(dir, { recursive: true });
  await writeFile(join(dir, "project.json"), serialized, "utf8");
  await writeFile(join(dir, "meta.json"), JSON.stringify(meta), "utf8");
  return { id, name: meta.name, savedAt: meta.savedAt };
}

export async function listProjects(): Promise<ProjectSummary[]> {
  if (!existsSync(ROOT)) return [];
  const entries = await readdir(ROOT, { withFileTypes: true });
  const projects: ProjectSummary[] = [];
  for (const entry of entries) {
    if (!entry.isDirectory() || !ID.test(entry.name)) continue;
    try {
      const meta = parseMeta(await readFile(join(ROOT, entry.name, "meta.json"), "utf8"));
      projects.push({ id: entry.name, name: meta.name, savedAt: meta.savedAt });
    } catch (error) {
      console.warn(`skipping project "${entry.name}":`, error instanceof Error ? error.message : error);
    }
  }
  return projects.sort((left, right) => right.savedAt.localeCompare(left.savedAt));
}

/** `null` means the project folder does not exist; a damaged folder throws so the UI can say so. */
export async function loadProject(id: string): Promise<ProjectRecord | null> {
  if (!ID.test(id)) return null;
  const dir = folder(id);
  let metaText: string;
  let projectText: string;
  try {
    [metaText, projectText] = await Promise.all([
      readFile(join(dir, "meta.json"), "utf8"),
      readFile(join(dir, "project.json"), "utf8"),
    ]);
  } catch (error) {
    if (isMissing(error)) return null;
    throw new Error("the project could not be read");
  }

  const meta = parseMeta(metaText);
  let data: unknown;
  try {
    data = JSON.parse(projectText);
  } catch {
    throw new Error("the project file is not valid JSON");
  }
  return { id, name: meta.name, savedAt: meta.savedAt, data };
}

export async function deleteProject(id: string): Promise<boolean> {
  if (!ID.test(id)) return false;
  const dir = folder(id);
  if (!existsSync(dir)) return false;
  await rm(dir, { recursive: true, force: true });
  return true;
}
