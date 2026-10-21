import { MOVE_COLORS, MOVE_GLYPHS } from "../analysis/move.ts";
import type { Move } from "../analysis/move.ts";
import type { Names } from "../names.ts";

export interface CardSpec {
  title: string;
  subtitle: string;
  body: string;
  color: string;
  glyph: string | null;
}

export interface CardText {
  title: string[];
  subtitle: string;
  body: string[];
  titleSize: number;
  subtitleSize: number;
  bodySize: number;
  titleLine: number;
  subtitleLine: number;
  bodyLine: number;
  bodyChars: number;
}

const MUTED = "#9e9b98";

export function specFor(move: Move): CardSpec {
  return {
    title: move.title,
    subtitle: move.moveNumber,
    body: move.explanation,
    color: MOVE_COLORS[move.moveType],
    glyph: MOVE_GLYPHS[move.moveType],
  };
}

export function introSpec(names: Names): CardSpec {
  return {
    title: `${names.white} — ${names.black}`,
    subtitle: "The game begins",
    body: "",
    color: MUTED,
    glyph: null,
  };
}

export function bodyChars(body: string[]): number {
  return body.reduce((total, line) => total + line.length, 0) + Math.max(0, body.length - 1);
}
