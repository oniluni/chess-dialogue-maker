import type { LlmModelInfo, LlmProviderInfo } from "../../shared/llm.ts";
import { analyzeDialogue } from "../analysis/analyze.ts";
import type { Move } from "../analysis/move.ts";
import { transcribeWav, transcriptionInfo } from "../analysis/transcribe.ts";
import { parseTranscript } from "../analysis/transcript.ts";
import { listModels, listProviders } from "../llm/api.ts";
import { extractWav } from "../media/wav.ts";
import {
  getClip,
  getLlmChoice,
  getNames,
  getTranscript,
  setLlmChoice,
  setMoves,
  setTranscript,
} from "../session.ts";
import { el } from "../ui/dom.ts";
import { formatTimestamp, messageOf } from "../ui/text.ts";

const LANGUAGES = [
  { id: "ru", label: "Russian" },
  { id: "en", label: "English" },
  { id: "uk", label: "Ukrainian" },
  { id: "es", label: "Spanish" },
  { id: "fr", label: "French" },
  { id: "de", label: "German" },
  { id: "it", label: "Italian" },
  { id: "pt", label: "Portuguese" },
  { id: "pl", label: "Polish" },
  { id: "tr", label: "Turkish" },
  { id: "zh", label: "Chinese" },
  { id: "ja", label: "Japanese" },
];

export interface AnalysisPanel {
  element: HTMLElement;
  refresh(): void;
  dispose(): void;
}

/** Transcript import plus the provider/model choice and the analysis button. */
export function createAnalysisPanel(options: { onMoves: (moves: Move[]) => void }): AnalysisPanel {
  let providers: LlmProviderInfo[] = [];
  let models: LlmModelInfo[] = [];
  let disposed = false;

  const fileInput = el("input", {
    class: "visually-hidden",
    attrs: { type: "file", accept: ".json,application/json" },
  });
  const importButton = el("button", { class: "button", text: "Import transcript", attrs: { type: "button" } });
  const transcribeButton = el("button", { class: "button", text: "Transcribe", attrs: { type: "button" } });
  const languageSelect = el("select", {
    class: "select",
    attrs: { "aria-label": "Speech language" },
    children: LANGUAGES.map((language) => el("option", { text: language.label, attrs: { value: language.id } })),
  });
  const transcriptInfo = el("span", { class: "meta" });
  const providerSelect = el("select", { class: "select", attrs: { "aria-label": "Provider" } });
  const modelSelect = el("select", { class: "select", attrs: { "aria-label": "Model" } });
  const analyzeButton = el("button", {
    class: "button button-primary",
    text: "Analyze dialogue",
    attrs: { type: "button" },
  });
  const status = el("p", { class: "status-line" });

  const element = el("section", {
    class: "analysis",
    children: [
      el("h2", { class: "section-title", text: "Dialogue and analysis" }),
      el("div", {
        class: "toolbar",
        children: [
          transcribeButton,
          el("label", { class: "field", children: [el("span", { text: "Language" }), languageSelect] }),
          importButton,
          transcriptInfo,
        ],
      }),
      el("div", {
        class: "toolbar",
        children: [
          el("label", { class: "field", children: [el("span", { text: "Provider" }), providerSelect] }),
          el("label", { class: "field", children: [el("span", { text: "Model" }), modelSelect] }),
          analyzeButton,
        ],
      }),
      fileInput,
      status,
    ],
  });

  function setStatus(text: string): void {
    status.textContent = text;
  }

  function currentProvider(): LlmProviderInfo | undefined {
    return providers.find((provider) => provider.id === providerSelect.value);
  }

  function refresh(): void {
    const segments = getTranscript();
    const clip = getClip();
    if (segments.length === 0) {
      transcriptInfo.textContent = clip === null ? "No clip, no transcript" : "No transcript yet";
    } else {
      const last = segments[segments.length - 1];
      const end = last === undefined ? 0 : last.end;
      transcriptInfo.textContent = `${segments.length} lines · up to ${formatTimestamp(end)}`;
    }
    analyzeButton.disabled =
      clip === null || segments.length === 0 || providerSelect.value === "" || modelSelect.value === "";
    transcribeButton.disabled = clip === null;
  }

  function fillProviders(): void {
    providerSelect.replaceChildren(
      ...providers.map((provider) =>
        el("option", {
          text: provider.needsKey && !provider.configured ? `${provider.label} · no key` : provider.label,
          attrs: { value: provider.id },
        }),
      ),
    );
    const stored = getLlmChoice().provider;
    const preferred =
      providers.find((provider) => provider.id === stored) ??
      providers.find((provider) => provider.local && provider.configured) ??
      providers.find((provider) => provider.configured) ??
      providers[0];
    if (preferred !== undefined) providerSelect.value = preferred.id;
  }

  function fillModels(): void {
    const provider = currentProvider();
    const list = models.length > 0 ? models : (provider?.suggested ?? []).map((id) => ({ id, label: id }));
    modelSelect.replaceChildren(...list.map((model) => el("option", { text: model.label, attrs: { value: model.id } })));
    const stored = getLlmChoice().model;
    const preferred = list.find((model) => model.id === stored) ?? list[0];
    if (preferred !== undefined) {
      modelSelect.value = preferred.id;
      setLlmChoice({ model: preferred.id });
    }
  }

  async function loadModels(providerId: string): Promise<void> {
    models = [];
    fillModels();
    refresh();
    try {
      const result = await listModels(providerId);
      if (disposed) return;
      models = result.models;
      fillModels();
      if (result.error !== undefined) setStatus(`Model list unavailable: ${result.error}`);
    } catch (error) {
      if (disposed) return;
      setStatus(`Model list unavailable: ${messageOf(error)}`);
    }
    refresh();
  }

  async function loadProviders(): Promise<void> {
    try {
      providers = await listProviders();
      if (disposed) return;
      fillProviders();
      const provider = currentProvider();
      if (provider !== undefined) {
        setLlmChoice({ provider: provider.id });
        await loadModels(provider.id);
      }
    } catch (error) {
      if (disposed) return;
      setStatus(`Could not load providers: ${messageOf(error)}`);
    }
    refresh();
  }

  async function importTranscript(file: File): Promise<void> {
    try {
      const segments = parseTranscript(await file.text());
      setTranscript(segments);
      setStatus(`Imported ${segments.length} lines from ${file.name}`);
    } catch (error) {
      setStatus(messageOf(error));
    }
    refresh();
  }

  async function transcribe(): Promise<void> {
    const clip = getClip();
    if (clip === null) return;

    transcribeButton.disabled = true;
    setStatus("Extracting the audio…");
    try {
      const wav = await extractWav(clip.file);
      if (disposed) return;
      setStatus("Transcribing… the first run downloads the model.");
      const segments = await transcribeWav(wav, languageSelect.value);
      if (disposed) return;
      setTranscript(segments);
      setStatus(`Transcribed ${segments.length} lines`);
    } catch (error) {
      if (!disposed) setStatus(messageOf(error));
    } finally {
      if (!disposed) {
        transcribeButton.disabled = false;
        refresh();
      }
    }
  }

  async function loadTranscriptionInfo(): Promise<void> {
    try {
      const info = await transcriptionInfo();
      if (disposed) return;
      if (LANGUAGES.every((language) => language.id !== info.language)) {
        languageSelect.append(el("option", { text: info.language, attrs: { value: info.language } }));
      }
      languageSelect.value = info.language;
    } catch (error) {
      if (!disposed) setStatus(`Speech settings unavailable: ${messageOf(error)}`);
    }
  }

  async function analyze(): Promise<void> {
    const clip = getClip();
    const segments = getTranscript();
    const choice = getLlmChoice();
    if (clip === null || segments.length === 0 || choice.provider === "") return;

    analyzeButton.disabled = true;
    setStatus("Asking the model…");
    try {
      const moves = await analyzeDialogue({
        segments,
        names: getNames(),
        provider: choice.provider,
        model: choice.model,
        duration: clip.media.info.duration,
        onAttempt: (attempt, total) => setStatus(`Asking the model (attempt ${attempt} of ${total})…`),
      });
      if (disposed) return;
      setMoves(moves);
      options.onMoves(moves);
      setStatus(`${moves.length === 1 ? "1 move" : `${moves.length} moves`} on the timeline`);
    } catch (error) {
      if (!disposed) setStatus(messageOf(error));
    } finally {
      if (!disposed) {
        analyzeButton.disabled = false;
        refresh();
      }
    }
  }

  importButton.addEventListener("click", () => fileInput.click());
  transcribeButton.addEventListener("click", () => {
    void transcribe();
  });
  fileInput.addEventListener("change", () => {
    const file = fileInput.files?.[0];
    if (file !== undefined) void importTranscript(file);
  });
  providerSelect.addEventListener("change", () => {
    setLlmChoice({ provider: providerSelect.value, model: "" });
    void loadModels(providerSelect.value);
  });
  modelSelect.addEventListener("change", () => {
    setLlmChoice({ model: modelSelect.value });
    refresh();
  });
  analyzeButton.addEventListener("click", () => {
    void analyze();
  });

  void loadProviders();
  void loadTranscriptionInfo();
  refresh();

  return {
    element,
    refresh,
    dispose() {
      disposed = true;
    },
  };
}
