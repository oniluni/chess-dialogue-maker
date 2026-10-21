# troubleshooting

## downloads

**«yt-dlp is not installed»**
install it (`winget install yt-dlp`, `brew install yt-dlp`, `pip install -U yt-dlp`) or point `YTDLP_BIN` at the binary. a `.js` build works too — the backend runs it with node.

**«YouTube asks to confirm you are not a bot»**
YouTube wants cookies. either set `YTDLP_COOKIES_FROM_BROWSER=chrome` (or `edge`, `firefox`) and restart the backend, or export a Netscape `cookies.txt` and save it in `Search → Cookies and yt-dlp`. the saved file wins over the variables.

**«ffmpeg is missing»**
yt-dlp needs ffmpeg to merge the video and the audio streams. install it and make sure it is in `PATH`.

**the fragment never arrives**
the backend already retries the whole video when a site refuses `--download-sections`; the job message says «downloading the whole video». trim in the editor afterwards.

**the same link downloads again**
the cache is keyed by URL **and** the exact range. a different `from`/`to` is a different download. the cached file is reused only while it still exists under `.cache/downloads`; deleting that directory or letting a job expire costs a re-download.

**«The download timed out»**
a job is stopped after ten minutes. fetch a shorter fragment, or check that the network is not stuck; `yt-dlp` itself retries three times before the timeout.

## models

**the first run is very slow**
whisper, WeSpeaker, SFace and YAMNet are downloaded once (a few hundred MB). after that the first transcription is still slower than the rest: the model has to be loaded into memory.

**«could not download … HTTP 403/404»**
the machine is offline or a URL moved. download the file by hand into `.cache/models` with the exact name listed in [configuration](configuration.md).

**`Initializer fc1_moving_mean appears in graph inputs` on stderr**
an onnxruntime warning from the SFace export, not an error. ignore it.

## analysis

the provider errors are classified before they reach you:

| what you see | what happened | what to do |
| --- | --- | --- |
| «The provider rejected the key» | 401/403 | check the key in `.env`, restart the backend |
| «The model is not available» | 404 | pick another model from the list, or check the id |
| «The provider is rate limiting» | 429 | wait a minute or switch provider/model |
| «The request is too long for this model» | 413 / context overflow | shorten the clip or split it, then analyse again |
| «The provider is having trouble» | 5xx | retry later or switch |
| «… is unreachable» | no answer at all | check the network and the base URL for a local server |

**the answer is rejected over and over**
that is the quality gate doing its job: target count, verdict↔strength, Brilliant limits, coverage, monotony, clichés. the recovery window lets you pick one variant per step, edit the text in place and ask for one more answer with the issue list.

**«no moves» after analysis**
the model returned valid JSON with an empty list, or the check threw everything away. try a bigger model, or write the moves by hand on `#/manual`.

## voices and faces

**«no voice hints» / low confidence**
diarization is calibrated on two clearly different voices. one voice, overlapping speech, music under the dialogue or a phone-quality recording will be unsure. the hints are optional — the analysis works without them.

**«No faces found in the kept range»**
YuNet ignores faces smaller than 12% of the frame. a wide shot with two small people may find none; a close-up usually works. detection runs on ~1 frame per second, so a person visible for less than a second may be missed.

**the eval bar or the tags look wrong in a project you reopened**
projects restore the document, not the video. open the same clip again.

## sound

**delivery events look random**
the comparison is per voice and needs at least three lines per voice. without speaker hints everything is compared against one global baseline, which on a single voice produces noise. run «Detect speakers» first.

**a long music event is tagged only at the start**
intended: a tag lives at most three seconds, and only reliable model events are drawn.

## export

**the export is slow**
it renders every frame through the scene and encodes with WebCodecs. a 30-second clip with freezes is a few thousand frames. close other heavy tabs; the progress is in the export row.

**the video is black or empty**
check that the clip plays in the player first. if the source decodes but the export does not, the format is probably unsupported by WebCodecs — remux it to H.264 first.

**the landscape layout looks off**
the side-panel geometry is checked by types and builds, not by eye yet. export one frame and compare: the card must stay inside the left 42%, the video must fill the rest.

## ports

**«EADDRINUSE»**
something else holds `8787` (or `5173`). stop it, or set `PORT` and start Vite with the matching proxy. `pnpm dev` expects the default pair.

**the UI says the backend is offline**
the health poll fails. run `pnpm dev` (both), or `pnpm dev:server` and `pnpm dev:web` separately, and check `.env` for a broken LLM URL — a bad base URL does not stop the backend, but it does break chat and models.
