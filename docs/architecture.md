# architecture

two halves, one session.

- the **browser** owns the media: `mediabunny` demuxes, decodes and encodes on WebCodecs, so trim, preview and export need no ffmpeg
- the **backend** owns binaries and models: downloads, whisper, diarization, faces, sound events, LLM calls, projects. node 26 runs the `.ts` files directly

there is no framework. pages are builder functions, state is one module, the router is a hash listener.

## the session

`src/session.ts` holds the one mutable document: the clip and its kept range, the transcript, the moves (plus the backup from the last analysis), the schedule options, the player names, the LLM choice, the recovery plan, the dialogue, the speaker hints, the people and the cast.

pages read it, update it, and never own the blob URL or the tracks. `closeClip()` resets the per-clip parts.

## the analysis pipeline

```
segments ──► prompt (lines + voice tags + target count + sound events)
        ──► provider (ollama | openai-compatible | gemini | anthropic | …)
        ──► JSON {moves: [...]}
        ──► reviewCandidates()  ← coverage, count, verdict↔strength, clichés, monotony, evals
        ──► ok: Move[] | failed: attempts[]
```

up to three attempts. every rejected attempt keeps its raw reply and its issues, which is what makes the recovery window possible:

- `planRecovery(attempts)` picks the answer with the fewest problems, takes its step boundaries and lists every overlapping move from every attempt as a variant
- `resolveRecovery(plan, choices, …, edits)` builds the moves, applying the in-place title/explanation edits
- `extraAttempt(options, previous)` sends the previous reply back with the issue list and returns one more attempt

## the render pipeline

```
moves + options ──► buildSchedule()  ← play/pause segments, freezes, SFX times
                ──► Scene.draw(ctx, source, state, time)
                ──► CanvasSource('avc') → Mp4OutputFormat → Blob
```

`Scene` draws in a fixed UI space of 1080×1920 and a canvas that can differ (`CANVAS_W/H`): in `landscape` the UI is scaled into a left panel (42%) and the video goes to the right, in the other formats the UI sits on top and the video fills the rest.

the video is not stretched: `cropRect()` picks a source rectangle around the detected faces at that time (interpolating by nearest sample), `original` fits the whole frame instead.

`drawCard` types the explanation out, `drawPlayer` draws the avatar crop in a circle, `drawEval` animates the eval bar, `drawSubtitle` wraps the current line, `drawSoundTag` draws a pill for a reliable event no longer than three seconds.

## the backend

| module | job |
| --- | --- |
| `server/api.ts` | routing, body limits, validation, JSON errors |
| `server/llm.ts` | provider registry, chat, models, provider error classification |
| `server/transcribe.ts` | whisper through `@huggingface/transformers` |
| `server/speakers.ts` | fbank (Kaldi-style), WeSpeaker embeddings, average-linkage clustering |
| `server/vision.ts` | YuNet detection, 5-point alignment, SFace embeddings, person clustering |
| `server/yamnet.ts` | YAMNet scores per 0.48 s frame, class prefixes, thresholds, music moods |
| `server/prosody.ts` | RMS loudness, median F0, syllable rate, robust z-score per voice, pauses |
| `server/download.ts` | yt-dlp jobs, sections, cookies, cache, full-video fallback, update |
| `server/search.ts` | YouTube search through yt-dlp |
| `server/projects.ts` | project folders under `.cache/projects` |

models are downloaded on first use into `.cache/` and never committed.

## API

| route | method | body |
| --- | --- | --- |
| `/api/health` | GET | — |
| `/api/llm/providers` | GET | — |
| `/api/llm/models?provider=` | GET | — |
| `/api/llm/chat` | POST | JSON chat request |
| `/api/transcribe?language=` | POST | WAV binary |
| `/api/speakers` | POST | JSON: WAV base64 + segments |
| `/api/faces` | POST | JSON: frames (base64 RGB, 320×320) |
| `/api/sounds` | POST | WAV binary |
| `/api/prosody` | POST | JSON: WAV base64 + segments with voice labels |
| `/api/search?q=` | GET | — |
| `/api/download` | POST | JSON: url, start, end |
| `/api/download/:id` | GET | — (status) |
| `/api/download/:id/file` | GET | — (the file) |
| `/api/download/info` | GET | — (yt-dlp version, cookies presence) |
| `/api/download/cookies` | POST | JSON: cookies text |
| `/api/download/update` | POST | — |
| `/api/projects` | GET / POST / DELETE | JSON document |

## storage

| path | contents |
| --- | --- |
| `.cache/transformers` | whisper model |
| `.cache/models` | WeSpeaker, YuNet, SFace, YAMNet + class map |
| `.cache/downloads` | yt-dlp jobs and `index.json` (source cache) |
| `.cache/projects` | one folder per saved project |
| `.cache/yt-cookies.txt` | cookies saved from the UI |

all of `.cache/` is gitignored.
