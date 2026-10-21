# Chess Dialogue Maker agent guide

## Repository

A tool that turns a dialogue clip into a short vertical video in the style of a Chess.com
game review: every exchange becomes a move with a verdict, a card and an animated evaluation
bar, the video freezes while the move is explained, and the result can be edited before export.

The stack is fixed: Vite 8, TypeScript 7 under `strict`, no UI framework, `mediabunny`
for media I/O, `lucide` for icons. `pnpm` is the package manager and `pnpm-lock.yaml` is
committed. The entry is `index.html` → `src/main.ts`, which imports `src/style.css`.

The backend is a small Node process in `server/`, started next to Vite. It is not bundled:
Node 26 runs the `.ts` files directly, so every import keeps its explicit `.ts` extension.

| Path | Purpose |
| --- | --- |
| `src/` | UI: shell, hash router, pages, state, styles |
| `server/` | Local backend: `/api` routes and serving the built `dist/` |
| `shared/` | Types used by both the UI and the backend |
| `scripts/` | Dev tooling; `dev.ts` starts Vite and the backend together |
| `public/` | Static files served as-is |
| `index.html` | Vite entry |

`src/main.ts` mounts the shell (`app/shell.ts`), starts the hash router (`app/router.ts`)
and health polling (`api.ts`). Pages live in `pages/` — one module each, and `dispose()`
releases its media — while decoding, encoding and probing sit in `media/`.

The opened clip and its kept range live in `src/session.ts`, together with the parsed
moves: pages read the session, update it, and never own the blob URL or the tracks.

## Rules

Explicit user instructions override these defaults.

- Do not run `git` unless explicitly asked. The user commits and pushes; staging,
  committing and pushing are theirs.
- State assumptions and ask when uncertain. Name conflicting readings, pick one
  deliberately and say why. Push back when a simpler approach exists; stop and name
  what is unclear.
- Write the minimum that solves the problem. No speculative features, no abstraction for
  a single use site. If a senior engineer would call it overcomplicated, simplify.
- Touch only what the task needs, and clean up only your own trail. Do not "improve"
  adjacent code, comments or formatting, and do not refactor what is not broken.
- Define the success criterion first, then iterate until it holds. Done means verified,
  not written.
- Use a model only for judgement calls — classification, drafting, summarising,
  extraction — never for routing, retries or deterministic transforms. If code can
  answer, code answers.
- Surface conflicts instead of averaging them: pick one pattern (newer or better tested),
  explain why, flag the other for cleanup.
- Read before you write: exports, immediate callers, shared helpers. If you cannot
  explain why the code is shaped this way, ask instead of guessing.
- Tests encode intent, not just behaviour. A test that cannot fail when the logic changes
  is not a test.
- Checkpoint after every significant step: what is done, what is verified, what is left.
  Do not continue from a state you cannot describe back.
- Match the codebase's conventions even when you disagree. If one is genuinely harmful,
  say so; do not fork silently.
- Fail loud. "Done" is wrong if something was skipped silently; "tests pass" is wrong if
  tests were skipped.

## TypeScript

- Comments explain why, never what. Keep only non-obvious trade-offs, workarounds and
  external-system quirks.
- Types describe reality: explicit types plus runtime validation at the boundary. No
  `as any`, no `as unknown as X`, no `@ts-ignore`; `@ts-expect-error` only with a stated
  reason.
- Do not weaken the compiler. The project builds under `strict` and
  `noUncheckedIndexedAccess`; loosening `tsconfig` to silence an error is not a fix.
- Type-only imports use `import type`, since the build runs with `isolatedModules`.
- No swallowed errors: an empty `catch` or a silent `return null` hides failures. Log it,
  or wrap it in the project's error type and rethrow; fire-and-forget work needs a log
  line.
- Delete a `catch` that only rethrows what it caught.
- Never `eval()` user input; avoid `innerHTML` or sanitise it first; secrets come from the
  environment; no hardcoded URLs or ids.
- Grep before writing: reuse and parameterise an existing implementation instead of
  copying it.
- No dead code: unused imports and declarations, commented-out code, unreachable branches,
  constant conditions.
- Every imported module and named export must exist; duplicate imports get merged.
- Logging goes through the project's logger; debug output is never committed.
- A `TODO` is finished in the same change or links a real issue.
- No empty functions or handlers that compile and do nothing.
- Names use domain words — `invoices`, `confirmDeletion` — not `data`, `temp` or
  `handleClick`.
- Functions stay under 80 lines, files under 400 (800 for `.tsx`). Prefer early returns to
  nesting; past six parameters, take an options object.
- No factory, registry or single-caller wrapper for imagined callers; extract when the
  second real caller appears.
- Declare a type once and export it from where it belongs; do not redeclare the same shape.
- Check `package.json` before adding a dependency, and never add one for a single function.
- No fake completeness: hardcoded success paths, silent fallbacks and happy-path-only
  handlers are not done. Wire the real dependency or fail loudly.
- Finish the path: the entry point reaches the new code, state transitions are coherent
  and failures are observable.
- Project conventions first: `tsconfig.json`, the `package.json` scripts and the
  neighbouring code win over generic advice.

## Media

Two halves. The browser owns media: `mediabunny` demuxes, decodes and encodes on WebCodecs,
so trim, preview and export need no ffmpeg. The Node backend owns whatever needs a binary or
a native model — downloads, speech, inference — because a page cannot load those.

Speech runs on the backend with `@huggingface/transformers` (whisper); the page extracts a
16 kHz mono WAV with `mediabunny` and posts it to `/api/transcribe`. The model is chosen by
`WHISPER_MODEL`, the default language by `WHISPER_LANGUAGE`, and the download is cached in
`.cache/transformers`.

## UI

No framework: components, state and routing are hand-rolled and typed. UI copy is English.
Split pages by feature and keep each builder small. `el()` in `ui/dom.ts` builds nodes; user
text never goes through `innerHTML`.

State is one `AppState` in `src/state.ts` with `get`/`set`/`subscribe`; a subscriber
re-renders only the part it owns. Routes are `#/<id>` hashes listed in `app/routes.ts`.

## Checks

Run the checks that match the change; documentation-only edits need no build.

| Change | Command |
| --- | --- |
| TypeScript | `pnpm typecheck` |
| Production bundle | `pnpm build` |
| UI in the browser | `pnpm dev` → `http://127.0.0.1:5173` |
| Backend only | `pnpm dev:server` → `http://127.0.0.1:8787` |
| Production run | `pnpm build && pnpm start` |

Keep this guide to durable rules, ownership boundaries and commands. Update it when those
change; keep implementation walkthroughs and history out.
