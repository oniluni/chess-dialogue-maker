import { join } from "node:path";

/** Where the local backend listens. The Vite dev proxy targets the same host and port. */
export const HOST = "127.0.0.1";

const requestedPort = Number(process.env["PORT"] ?? 8787);
export const PORT =
  Number.isInteger(requestedPort) && requestedPort > 0 && requestedPort < 65_536 ? requestedPort : 8787;

/** Repository root, shared by the server and the dev runner. */
export const ROOT_DIR = join(import.meta.dirname, "..");

/** Built UI; served by the backend in production. */
export const DIST_DIR = join(ROOT_DIR, "dist");
