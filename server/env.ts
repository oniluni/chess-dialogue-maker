import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

const ROOT_DIR = join(import.meta.dirname, "..");

/**
 * Reads `.env` next to the repository root. Real environment variables win, so a shell
 * value can always override the file.
 */
export function loadEnv(): void {
  const file = join(ROOT_DIR, ".env");
  if (!existsSync(file)) return;
  for (const line of readFileSync(file, "utf8").split(/\r?\n/)) {
    const match = /^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$/.exec(line);
    if (match === null) continue;
    const name = match[1];
    const value = match[2];
    if (name === undefined || value === undefined) continue;
    if (process.env[name] !== undefined) continue;
    process.env[name] = value.replace(/^["']|["']$/g, "");
  }
}

loadEnv();
