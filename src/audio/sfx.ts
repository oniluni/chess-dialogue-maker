/** Sound-effect sets, loaded once per sample rate and reused by the preview and the export. */

export type SfxName = "pause" | "unpause" | "move" | "tick1" | "tick2" | "tick3" | "tick4";

export interface SfxSetOption {
  id: string;
  label: string;
}

export const TYPING_SETS: readonly SfxSetOption[] = [
  { id: "soft", label: "Soft keys" },
  { id: "typewriter", label: "Typewriter" },
  { id: "mechanical", label: "Mechanical" },
  { id: "pencil", label: "Pencil" },
  { id: "bubble", label: "Bubbles" },
];

export const PAUSE_SETS: readonly SfxSetOption[] = [
  { id: "wood", label: "Chess piece" },
  { id: "clock", label: "Chess clock" },
  { id: "whoosh", label: "Whoosh" },
  { id: "chime", label: "Chime" },
  { id: "tape", label: "Tape stop" },
];

/** Per-sound gain the old renderer mixed with, before the master volume. */
export const SFX_GAIN: Record<SfxName, number> = {
  pause: 0.9,
  unpause: 0.75,
  move: 0.6,
  tick1: 0.55,
  tick2: 0.55,
  tick3: 0.55,
  tick4: 0.55,
};

export type SfxLibrary = Readonly<Record<SfxName, AudioBuffer>>;

const cache = new Map<string, Promise<SfxLibrary>>();

async function fetchSfx(context: BaseAudioContext, url: string): Promise<AudioBuffer> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`sound ${url}: HTTP ${response.status}`);
  return context.decodeAudioData(await response.arrayBuffer());
}

/** Loads one typing set and one pause set, decoded to the context's sample rate. */
export function loadSfx(context: BaseAudioContext, typingSet: string, pauseSet: string): Promise<SfxLibrary> {
  const key = `${context.sampleRate}|${typingSet}|${pauseSet}`;
  const cached = cache.get(key);
  if (cached !== undefined) return cached;

  const loading = (async (): Promise<SfxLibrary> => {
    const [tick1, tick2, tick3, tick4] = await Promise.all(
      [1, 2, 3, 4].map((number) => fetchSfx(context, `/sfx/typing/${typingSet}/type_tick_${number}.wav`)),
    );
    const [pause, unpause] = await Promise.all([
      fetchSfx(context, `/sfx/pause/${pauseSet}/pause.wav`),
      fetchSfx(context, `/sfx/pause/${pauseSet}/unpause.wav`),
    ]);
    if (!tick1 || !tick2 || !tick3 || !tick4) throw new Error(`typing set "${typingSet}" is incomplete`);
    return { pause, unpause, move: pause, tick1, tick2, tick3, tick4 };
  })();

  cache.set(key, loading);
  loading.catch(() => cache.delete(key));
  return loading;
}
