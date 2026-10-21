import { existsSync } from "node:fs";
import { mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

const ROOT = join(import.meta.dirname, "..", ".cache", "projects");
const ID = /^[a-z0-9-]{4,80}$/;

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

function folder(id: string): string {
  if (!ID.test(id)) throw new Error("bad project id");
  return join(ROOT, id);
}

function slug(name: string): string {
  const clean = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
  return `${clean === "" ? "project" : clean}-${Date.now().toString(36)}`;
}

export async function saveProject(name: string, data: unknown): Promise<ProjectSummary> {
  const id = slug(name);
  const dir = folder(id);
  await mkdir(dir, { recursive: true });
  const meta: Meta = { name: name.trim() === "" ? "Untitled" : name.trim(), savedAt: new Date().toISOString() };
  await writeFile(join(dir, "meta.json"), JSON.stringify(meta), "utf8");
  await writeFile(join(dir, "project.json"), JSON.stringify(data), "utf8");
  return { id, name: meta.name, savedAt: meta.savedAt };
}

export async function listProjects(): Promise<ProjectSummary[]> {
  if (!existsSync(ROOT)) return [];
  const entries = await readdir(ROOT, { withFileTypes: true });
  const projects: ProjectSummary[] = [];
  for (const entry of entries) {
    if (!entry.isDirectory() || !ID.test(entry.name)) continue;
    try {
      const meta = JSON.parse(await readFile(join(ROOT, entry.name, "meta.json"), "utf8")) as Meta;
      projects.push({ id: entry.name, name: meta.name, savedAt: meta.savedAt });
    } catch {
      continue;
    }
  }
  return projects.sort((left, right) => right.savedAt.localeCompare(left.savedAt));
}

export async function loadProject(id: string): Promise<ProjectRecord | null> {
  const dir = folder(id);
  try {
    const meta = JSON.parse(await readFile(join(dir, "meta.json"), "utf8")) as Meta;
    const data: unknown = JSON.parse(await readFile(join(dir, "project.json"), "utf8"));
    return { id, name: meta.name, savedAt: meta.savedAt, data };
  } catch {
    return null;
  }
}

export async function deleteProject(id: string): Promise<boolean> {
  const dir = folder(id);
  if (!existsSync(dir)) return false;
  await rm(dir, { recursive: true, force: true });
  return true;
}
