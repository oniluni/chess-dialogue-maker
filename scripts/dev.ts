import "../server/env.ts";

import { spawn } from "node:child_process";
import { join } from "node:path";
import { createServer } from "vite";

import { PORT, ROOT_DIR } from "../server/config.ts";

const backend = spawn(process.execPath, [join(ROOT_DIR, "server", "index.ts")], {
  cwd: ROOT_DIR,
  env: { ...process.env, PORT: String(PORT) },
  stdio: "inherit",
});

const vite = await createServer();
try {
  await vite.listen();
} catch (error) {
  backend.kill();
  throw error;
}
vite.printUrls();

let closing = false;

async function shutdown(code: number): Promise<void> {
  if (closing) return;
  closing = true;
  await vite.close();
  backend.kill();
  process.exit(code);
}

backend.on("exit", (code) => {
  if (closing) return;
  console.error(`backend stopped (code ${code ?? "signal"})`);
  void shutdown(code ?? 1);
});

for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.on(signal, () => void shutdown(0));
}
