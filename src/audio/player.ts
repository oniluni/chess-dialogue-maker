import { loadSfx, SFX_GAIN } from "./sfx.ts";
import type { SfxLibrary, SfxName } from "./sfx.ts";

/** Plays the sound effects over the preview; the export mixes the same buffers itself. */
export class SoundBoard {
  private context: AudioContext | null = null;
  private libraries = new Map<string, SfxLibrary>();
  private active = new Set<AudioBufferSourceNode>();
  private volume = 0.8;
  private typingSet = "soft";
  private pauseSet = "wood";

  setVolume(volume: number): void {
    this.volume = Math.max(0, Math.min(1, volume));
  }

  setSets(typingSet: string, pauseSet: string): void {
    this.typingSet = typingSet;
    this.pauseSet = pauseSet;
  }

  /** Creates the context on a user gesture and starts loading the sets. */
  async unlock(): Promise<void> {
    this.context ??= new AudioContext();
    await this.context.resume();
    await this.library();
  }

  async play(name: SfxName): Promise<void> {
    const context = this.context;
    const library = await this.library();
    if (context === null || library === null) return;
    this.start(context, library[name], name, 0);
  }

  async playSequence(events: Array<{ name: SfxName; offset: number }>): Promise<void> {
    const context = this.context;
    const library = await this.library();
    if (context === null || library === null) return;
    for (const event of events) {
      this.start(context, library[event.name], event.name, event.offset);
    }
  }

  stopAll(): void {
    for (const source of this.active) {
      try {
        source.stop();
      } catch {
        // already stopped
      }
      source.disconnect();
    }
    this.active.clear();
  }

  dispose(): void {
    this.stopAll();
    void this.context?.close();
    this.context = null;
    this.libraries.clear();
  }

  private async library(): Promise<SfxLibrary | null> {
    const context = this.context;
    if (context === null) return null;
    const key = `${this.typingSet}|${this.pauseSet}`;
    const cached = this.libraries.get(key);
    if (cached !== undefined) return cached;
    try {
      const library = await loadSfx(context, this.typingSet, this.pauseSet);
      this.libraries.set(key, library);
      return library;
    } catch (error) {
      console.warn("sound set failed:", error);
      return null;
    }
  }

  private start(context: AudioContext, buffer: AudioBuffer, name: SfxName, offset: number): void {
    const source = context.createBufferSource();
    source.buffer = buffer;
    const gain = context.createGain();
    gain.gain.value = SFX_GAIN[name] * this.volume;
    source.connect(gain).connect(context.destination);
    source.start(context.currentTime + offset);
    this.active.add(source);
    source.onended = () => {
      this.active.delete(source);
      source.disconnect();
      gain.disconnect();
    };
  }
}
