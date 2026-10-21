import type { Side } from "../analysis/move.ts";

let avatars: Record<Side, string | null> = { white: null, black: null };

export function getAvatars(): Record<Side, string | null> {
  return avatars;
}

export function setAvatars(patch: Partial<Record<Side, string | null>>): void {
  avatars = { ...avatars, ...patch };
}

export function clearAvatars(): void {
  avatars = { white: null, black: null };
}
