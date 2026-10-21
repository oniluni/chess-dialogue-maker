# chess-dialogue-maker

> a dialogue clip goes in, a chess-review-style vertical video comes out

turn any dialogue — an interview, a scene, a heated argument — into a short video that looks like a Chess.com game review: every exchange becomes a "move" with a verdict, a card with an explanation, an animated eval bar, and the video freezes while the move is explained.

## what it does

the whole loop is local-first. the browser does the media (demux, decode, encode), a small node backend does what a page can't: speech, diarization, faces, sound events, model downloads.

- open a local file or paste a link (`yt-dlp` fetches it, optionally just a fragment)
- transcribe the dialogue (whisper, on the backend)
- find who speaks (WeSpeaker) and who is on screen (YuNet + SFace), then name the players
- ask an LLM to turn the dialogue into moves (verdict, strength, eval, title, explanation), with strict checks and a recovery window when the answer is bad
- pick a look: theme, subtitles, sound tags, output format
- render 1080×1920 (or 1920×1080) with `mediabunny` and export an MP4

## requirements

- **node 26+** — the backend runs the `.ts` files directly, no build step
- **pnpm 11+**
- **yt-dlp** in `PATH` (or `YTDLP_BIN`) — only for link downloads and search
- **ffmpeg** — only so yt-dlp can merge the streams
- optional: **Ollama** or a cloud key for the analysis. without either you can still use the manual analysis page

## quick start

```sh
pnpm install
cp .env.example .env    # fill in what you use; everything has a default
pnpm dev                # http://127.0.0.1:5173, backend on 8787
```

production:

```sh
pnpm build              # tsc --noEmit + vite build into dist/
pnpm start              # the backend serves dist/
```

checks:

```sh
pnpm typecheck          # tsc --noEmit, tests included
pnpm test               # node:test regression suite
pnpm build              # typecheck + production bundle
```

the first run downloads the models into `.cache/` (gitignored): whisper into `.cache/transformers`, the rest into `.cache/models`. expect a few hundred MB in total and a slow first transcription, face pass and sound pass.

## repo layout

- `src/` — the app: shell, hash router, pages, media, render
- `src/pages/` — one module per page; `create.ts` wires the whole editor together
- `src/analysis/` — moves, schedule, quality checks, prompt, transcription, speakers, faces, sound detection
- `src/render/` — the compositor: card, players, eval bar, video crop, subtitles, sound tags
- `src/media/` — probing, range extraction, WAV, MP4 export
- `server/` — `/api` routes, LLM providers, whisper, WeSpeaker, YuNet/SFace, YAMNet, prosody, yt-dlp, projects
- `shared/` — types used by both sides
- `scripts/dev.ts` — starts Vite and the backend together
- `test/` — `node:test` regression tests for parsers, downloads, API guards and project import
- `docs/` — [usage](docs/usage.md), [architecture](docs/architecture.md), [configuration](docs/configuration.md), [troubleshooting](docs/troubleshooting.md)

## features

| area | what you get |
| --- | --- |
| source | local file, drag and drop, YouTube link, YouTube search (with `@handle words`), fragment download |
| speech | whisper transcription, language override, manual transcript import, dialogue editing |
| voices | two-voice diarization, A/B → player mapping, voice tags fused into the prompt and the checks |
| faces | YuNet detection, SFace grouping, per-person names and sides, avatar crops, face-aware reframing |
| analysis | Ollama, any OpenAI-compatible server, Pollinations, Groq, OpenRouter, Mistral, Gemini, Anthropic |
| quality | target move count, verdict↔strength agreement, Brilliant limits, monotony, clichés, coverage, eval sanity |
| editing | moves editor, manual analysis (strength, motif, moments), recovery with in-place edits and one more answer |
| look | 3 style presets, subtitles, sound tags, vertical / original / landscape |
| sound | YAMNet events (22 kinds, music moods), delivery events (louder, quieter, higher, faster, slower, pauses) |
| text | model summary, rules-only caption, copy to clipboard |
| projects | save / open / delete the document on the backend |

## notes

- the app is local. nothing leaves your machine unless you point the analysis at a cloud provider.
- the backend listens on `127.0.0.1` only and rejects API requests whose `Host`/`Origin` is not loopback; state-changing JSON endpoints require `application/json`. there is no authentication: any local process can call the API, so the machine is the trust boundary.
- model downloads use HTTPS and an atomic write, but no pinned checksum; a model file that already exists is trusted.
- at most two link downloads run at a time; a job that runs longer than ten minutes is stopped.
- projects are validated when opened; a malformed file is rejected instead of being half-loaded.
- projects store the document (moves, transcript, names, cast, avatars, sounds, options), **not the video**. open the same clip again after loading.
- diarization is calibrated on two synthetic voices. one voice or overlapping speech will be unsure, and the UI says so.
- the `landscape` format puts the UI in a side panel; the geometry is checked by types and builds, not by eye yet.

## docs

- [usage](docs/usage.md) — the flow from a clip to the exported video
- [architecture](docs/architecture.md) — who owns what, the pipeline, the API
- [configuration](docs/configuration.md) — every environment variable and model
- [troubleshooting](docs/troubleshooting.md) — the failures you will actually hit

## acknowledgements

- [mediabunny](https://mediabunny.dev/) — demux, decode, encode on WebCodecs
- [OpenCV Zoo](https://github.com/opencv/opencv_zoo) — YuNet and SFace (Apache-2.0)
- [WeSpeaker](https://github.com/wenet-e2e/wespeaker) via [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) — speaker embeddings (Apache-2.0)
- [YAMNet](https://tfhub.dev/google/yamnet/1) — AudioSet sound events (Apache-2.0), ONNX export by [audiomagic](https://huggingface.co/audiomagic/yamnet-onnx)
- [whisper](https://github.com/openai/whisper) through [transformers.js](https://github.com/huggingface/transformers.js) (MIT)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) (Unlicense)
- the Chess.com review look, obviously

this project is llm-assisted: a lot of the code was written with AI and reviewed by hand. ai-assisted contributions are welcome as long as you say so.

## license

MIT.
