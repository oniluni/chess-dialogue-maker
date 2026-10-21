# usage

the flow is one page, top to bottom: open a clip → transcribe → name the voices and the faces → analyse → fix → render. everything is optional except the clip and the moves.

## 1. open a clip

`#/create` starts with a drop zone, a link field and a search block.

- **local file** — drop it or pick it; anything the browser can decode (mp4, mkv, webm, mov) works
- **link** — paste a YouTube (or yt-dlp-supported) URL, optionally set from/to to fetch just a fragment. yt-dlp is spawned with `--download-sections`; if the site refuses the fragment the backend retries the whole video and says so
- **search** — `chess blunder` searches YouTube, `@handle words` searches inside that channel. «Use» puts the link into the URL field

below the player:

| control | what it does |
| --- | --- |
| trim | the kept range; everything outside is cut on export |
| pause on each move | freeze while the card is explained (otherwise the video plays through) |
| subtitles | draw the current dialogue line at the bottom of the video |
| format | `vertical` (crop to fill), `original` (whole frame with bars), `landscape` (UI in a side panel) |

pressing play with no moves just plays the clip; with moves it plays the schedule with freezes.

## 2. transcribe

the analysis block has one «Transcribe» button with a language override. the page extracts a 16 kHz mono WAV with `mediabunny` and posts it to `/api/transcribe`; whisper runs on the backend.

you can also import a transcript JSON, or write the dialogue by hand on `#/manual`.

## 3. voices

«Detect speakers» compares the voices: it extracts the same WAV, sends the transcript times to `/api/speakers`, and gets back an `A`/`B` label per line with a confidence and an overall reliability.

- `Voice A` / `Voice B` pick which player speaks which voice. switching one flips the other
- the labels go into the prompt («one move never mixes labels») and into the checks
- low confidence lines are marked unsure and are not forced onto a player

## 4. faces

«Find people» samples about one frame per second, sends them to `/api/faces`, and gets back per-frame boxes plus people. the best frame of each person becomes its avatar, drawn in the players bar instead of initials.

- name each person and pick `White` / `Black` / `Not a player`
- «Apply names» copies the names onto the players

the same boxes drive the reframing: the export crops around the faces instead of the centre.

## 5. analysis

pick a provider and a model, then «Analyze dialogue». the backend asks for a JSON list of moves and checks it: target count, verdict ↔ strength agreement, Brilliant caps, monotony, clichés, coverage, eval sanity, voice consistency.

- a good answer lands on `#/moves`
- a bad one opens the recovery window: step boundaries from the best answer, every other answer as a variant, the title and explanation editable in place, and «One more answer» asks the model to fix its own mistakes with the list of problems

## 6. moves

`#/moves` is the editor: reorder, change verdict, side, title, explanation, eval; add and delete moves; validation errors and warnings are listed and block export while they are errors. «Restore analysis» puts back the backup from the last model run.

## 7. manual analysis

`#/manual` builds moves without any model: dialogue lines with speaker, strength (−3…+3) and motif, then moments that group consecutive lines. the verdict is derived from the average strength when the moment does not set one; the first motif becomes the gist.

## 8. look

`#/style` has three presets that change the page, the card and the players bar. subtitles and sound tags are toggled on `#/create`.

## 9. sounds

`#/sounds` has two detectors and a manual list:

- **sounds** — YAMNet on the clip audio: laughter, crying, scream, whisper, sigh, gasp, groan, cough, singing, music (+ mood), applause, crowd, door, phone, siren, shot, glass, footsteps, vehicle, weather, animal, TV
- **delivery** — each line against the same voice's usual manner: louder, quieter, higher, faster, slower, pauses
- manual events stay when you re-detect

only model events marked reliable are drawn in the video, and a tag never lives longer than 3 seconds. reliable events are also passed to the analysis prompt as context.

## 10. text

- **summary** — 2–3 sentences from the model, using the transcript and, when present, the moves
- **description** — rules only: cleans the title, adds the players, the move count and hashtags, copies to the clipboard

## 11. projects

`Save project` stores the document in `.cache/projects` on the backend: moves, transcript, names, cast, avatars, sounds, options, style, provider choice. `Open` restores it and re-renders the page.

the video is **not** stored — open the same clip again after loading.

## 12. export

«Render» runs the schedule through the scene and encodes H.264 + AAC with `mediabunny` at 1080×1920 (or the chosen format), 30 fps, with the sound effects mixed in. a 5-second clip takes a few seconds; a few minutes can take a while, the progress is in the export row.
