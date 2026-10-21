/** Sound tags of the clip: detected by YAMNet on the backend, or added by hand. */

export type SoundKind =
  | "laughter"
  | "crying"
  | "scream"
  | "whisper"
  | "sigh"
  | "gasp"
  | "groan"
  | "cough"
  | "singing"
  | "music"
  | "applause"
  | "crowd"
  | "door"
  | "phone"
  | "siren"
  | "shot"
  | "glass"
  | "footsteps"
  | "vehicle"
  | "weather"
  | "animal"
  | "tv"
  | "other";

export const SOUND_KINDS: Array<{ id: SoundKind; label: string }> = [
  { id: "laughter", label: "Laughter" },
  { id: "crying", label: "Crying" },
  { id: "scream", label: "Scream" },
  { id: "whisper", label: "Whisper" },
  { id: "sigh", label: "Sigh" },
  { id: "gasp", label: "Gasp" },
  { id: "groan", label: "Groan" },
  { id: "cough", label: "Cough" },
  { id: "singing", label: "Singing" },
  { id: "music", label: "Music" },
  { id: "applause", label: "Applause" },
  { id: "crowd", label: "Crowd" },
  { id: "door", label: "Door" },
  { id: "phone", label: "Phone" },
  { id: "siren", label: "Siren" },
  { id: "shot", label: "Shot" },
  { id: "glass", label: "Glass" },
  { id: "footsteps", label: "Footsteps" },
  { id: "vehicle", label: "Vehicle" },
  { id: "weather", label: "Weather" },
  { id: "animal", label: "Animal" },
  { id: "tv", label: "TV" },
  { id: "other", label: "Other" },
];

export interface SoundEvent {
  id: number;
  /** Where it starts, in seconds. */
  at: number;
  /** Where it ends; `null` for a point event. */
  end: number | null;
  kind: SoundKind;
  label: string;
  source: "model" | "user";
  /** Model confidence at the peak, 0…1. */
  peak: number;
  /** The model is sure enough to show this in the video. */
  reliable: boolean;
}

let events: SoundEvent[] = [];
let nextId = 1;

export function getSoundEvents(): SoundEvent[] {
  return events;
}

export function setSoundEvents(next: SoundEvent[]): void {
  events = next;
  nextId = Math.max(0, ...next.map((event) => event.id)) + 1;
}

export function addSoundEvent(event: Omit<SoundEvent, "id">): SoundEvent {
  const created: SoundEvent = { ...event, id: nextId };
  nextId += 1;
  events = [...events, created].sort((left, right) => left.at - right.at);
  return created;
}

export function removeSoundEvent(id: number): void {
  events = events.filter((event) => event.id !== id);
}

/** Replaces the detected events, keeping everything added by hand. */
export function replaceDetected(next: Array<Omit<SoundEvent, "id" | "source">>): void {
  const kept = events.filter((event) => event.source === "user");
  const detected: SoundEvent[] = next.map((event, index) => ({
    ...event,
    source: "model",
    id: -(index + 1),
  }));
  const merged = [...kept, ...detected].sort((left, right) => left.at - right.at);
  events = merged;
  nextId = 1;
  events = merged.map((event) => {
    const created = { ...event, id: nextId };
    nextId += 1;
    return created;
  });
}

export function soundKindLabel(kind: SoundKind): string {
  return SOUND_KINDS.find((item) => item.id === kind)?.label ?? kind;
}

export function isSoundKind(value: string): value is SoundKind {
  return SOUND_KINDS.some((item) => item.id === value);
}
