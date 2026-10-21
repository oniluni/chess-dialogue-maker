export interface StylePreset {
  id: string;
  label: string;
  /** Page around the video. */
  pageBg: string;
  /** The move card. */
  cardBg: string;
  /** The players bar. */
  playersBg: string;
}

export const STYLE_PRESETS: StylePreset[] = [
  {
    id: "chess",
    label: "Chess.com dark",
    pageBg: "#000000",
    cardBg: "#262421",
    playersBg: "#161512",
  },
  {
    id: "mono",
    label: "Monochrome",
    pageBg: "#0b0b0b",
    cardBg: "#1c1c1c",
    playersBg: "#111111",
  },
  {
    id: "warm",
    label: "Warm brown",
    pageBg: "#140f0b",
    cardBg: "#2f241a",
    playersBg: "#1d1610",
  },
];

let current: StylePreset = STYLE_PRESETS[0] ?? {
  id: "chess",
  label: "Chess.com dark",
  pageBg: "#000000",
  cardBg: "#262421",
  playersBg: "#161512",
};

export function getStyle(): StylePreset {
  return current;
}

export function setStyle(id: string): void {
  const found = STYLE_PRESETS.find((preset) => preset.id === id);
  if (found !== undefined) current = found;
}
