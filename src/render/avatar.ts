import type { Side } from "../analysis/move.ts";
import { getAvatars } from "./avatar-store.ts";

export interface SceneAvatars {
  white: ImageBitmap | null;
  black: ImageBitmap | null;
}

const cache = new Map<string, ImageBitmap>();

async function bitmap(url: string): Promise<ImageBitmap> {
  const cached = cache.get(url);
  if (cached !== undefined) return cached;
  const response = await fetch(url);
  if (!response.ok) throw new Error("the avatar could not be loaded");
  const image = await createImageBitmap(await response.blob());
  cache.set(url, image);
  return image;
}

/** Bitmaps for the players' avatars; `null` where there is no avatar. */
export async function loadSceneAvatars(): Promise<SceneAvatars> {
  const urls: Record<Side, string | null> = getAvatars();
  const [white, black] = await Promise.all([
    urls.white === null ? Promise.resolve(null) : bitmap(urls.white),
    urls.black === null ? Promise.resolve(null) : bitmap(urls.black),
  ]);
  return { white, black };
}
