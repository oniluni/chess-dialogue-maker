/** Sound tags of the clip: what happens when, drawn over the video and later detected automatically. */

export type SoundKind = "laugh" | "applause" | "music" | "shout" | "silence" | "other";

export const SOUND_KINDS: Array<{ id: SoundKind; label: string }> = [
  { id: "laugh", label: "Laughter" },
  { id: "applause", label: "Applause" },
  { id: "music", label: "Music" },
  { id: "shout", label: "Shout" },
  { id: "silence", label: "Silence" },
  { id: "other", label: "Other" },
];

export interface SoundEvent {
  id: number;
  at: number;
  kind: SoundKind;
  label: string;
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

export function soundKindLabel(kind: SoundKind): string {
  return SOUND_KINDS.find((item) => item.id === kind)?.label ?? kind;
}
