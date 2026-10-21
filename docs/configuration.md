# configuration

everything lives in `.env` at the repository root. the backend reads it at startup; the UI never sees the keys.

```sh
cp .env.example .env
```

## providers

| variable | default | what it does |
| --- | --- | --- |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | local Ollama |
| `OLLAMA_MODEL` | `qwen3.5:9b` | default Ollama model |
| `CUSTOM_LLM_URL` | `http://127.0.0.1:1234/v1` | any OpenAI-compatible server: LM Studio (1234), llama.cpp (8080), vLLM (8000) |
| `CUSTOM_LLM_KEY` | empty | key for that server, if it wants one |
| `CUSTOM_LLM_MODEL` | empty | model id to show first |
| `GROQ_API_KEY` | — | Groq |
| `OPENROUTER_API_KEY` | — | OpenRouter |
| `MISTRAL_API_KEY` | — | Mistral |
| `GEMINI_API_KEY` | — | Google Gemini |
| `GEMINI_MODEL` | `gemini-3.8-flash` | default Gemini model |
| `ANTHROPIC_API_KEY` | — | Anthropic |

Pollinations works without a key. a provider without a key simply shows «no key» in the list and fails with a clear hint if you pick it anyway.

## speech

| variable | default | what it does |
| --- | --- | --- |
| `WHISPER_MODEL` | `Xenova/whisper-small` | any whisper checkpoint `@huggingface/transformers` can load |
| `WHISPER_LANGUAGE` | `ru` | default language; the UI can override it per transcription |

bigger checkpoints (`whisper-medium`, `whisper-large-v3-turbo`) are better and much slower on CPU. the first transcription downloads the checkpoint into `.cache/transformers`.

## downloads

| variable | default | what it does |
| --- | --- | --- |
| `YTDLP_BIN` | `yt-dlp` from `PATH` | path to another binary, or a `.js` build run with node |
| `YTDLP_COOKIES` | — | path to a Netscape `cookies.txt` |
| `YTDLP_COOKIES_FROM_BROWSER` | — | `chrome`, `firefox`, `edge`… — yt-dlp reads the browser's cookies itself |

cookies saved from the UI (`Search → Cookies and yt-dlp`) go to `.cache/yt-cookies.txt` and take priority over both variables. delete the file to fall back. on POSIX the file is written with mode `0600` inside an owner-only `.cache`; on Windows it inherits the ACL of the user profile. cookie values are never returned by the API.

link downloads run two at a time and time out after ten minutes. the cache reuses the same URL and the same exact range while the downloaded file is still under `.cache/downloads`.

## backend

| variable | default | what it does |
| --- | --- | --- |
| `PORT` | `8787` | backend port; the dev script expects this one |

`pnpm dev` starts Vite on `5173` and the backend on `8787`, with Vite proxying `/api` to it. `pnpm build && pnpm start` serves `dist/` from the backend on the same port.

## models

downloaded on first use, all under `.cache/` (gitignored).

| model | file | size | used for |
| --- | --- | --- | --- |
| whisper-small | `.cache/transformers` | ~250 MB | transcription |
| WeSpeaker ResNet34 | `.cache/models/wespeaker_en_voxceleb_resnet34.onnx` | 25 MB | speaker embeddings |
| YuNet 2023mar | `.cache/models/face_detection_yunet_2023mar.onnx` | 227 KB | face detection |
| SFace 2021dec | `.cache/models/face_recognition_sface_2021dec.onnx` | 37 MB | face embeddings |
| YAMNet | `.cache/models/yamnet.onnx` | 15 MB | sound events |
| YAMNet class map | `.cache/models/yamnet_class_map.csv` | 10 KB | class names |

offline setup: copy those files into place by hand; the backend checks for the files before downloading anything.

sources: YuNet and SFace from [opencv_zoo](https://github.com/opencv/opencv_zoo), WeSpeaker from [sherpa-onnx releases](https://github.com/k2-fsa/sherpa-onnx), YAMNet from [audiomagic/yamnet-onnx](https://huggingface.co/audiomagic/yamnet-onnx), whisper from Hugging Face.

## data directories

| path | contents |
| --- | --- |
| `.cache/downloads` | yt-dlp jobs plus `index.json` (same link + same fragment is not fetched twice) |
| `.cache/projects` | one folder per saved project |
| `.cache/publish` | (unused) |

`rm -rf .cache` is always safe: it only costs re-downloads.
