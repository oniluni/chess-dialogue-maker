import { formatEval, MOVE_COLORS, MOVE_GLYPHS } from "../analysis/move.ts";
import type { Move } from "../analysis/move.ts";
import type { Names } from "../names.ts";
import type { FrameFaces } from "../../shared/people.ts";
import type { SceneAvatars } from "./avatar.ts";
import { getStyle } from "./style.ts";
import type { TranscriptSegment } from "../analysis/transcript.ts";
import { getScheduleOptions } from "../session.ts";
import type { FrameState } from "./state.ts";
import { ellipsize, setFont, wrapLines } from "./text.ts";

export const WIDTH = 1080;
export const HEIGHT = 1920;

const TOP_MARGIN = 120;
const CARD_MIN_H = 170;
const CARD_MAX_H = 320;
const CARD_PAD_X = 40;
const CARD_PAD_Y = 18;
const ACCENT_W = 7;
const ICON_SIZE = 80;
const TITLE_SIZE = 36;
const SUBTITLE_SIZE = 22;
const BODY_SIZE = 31;
const TITLE_LH = 1.12;
const SUBTITLE_LH = 1.15;
const BODY_LH = 1.22;
const TITLE_GAP = 4;
const BODY_GAP = 12;
const PLAYERS_H = 84;
const PLAYERS_PAD = 18;
const AVATAR_SIZE = 62;
const NAME_SIZE = 31;
const SIDE_SIZE = 20;
const EVAL_GAP = 3;
const EVAL_H = 30;
const EVAL_LABEL_SIZE = 19;

const TEXT = "#ffffff";
const MUTED = "#9e9b98";
const EVAL_WHITE = "#ffffff";
const EVAL_BLACK = "#403d39";
const RING = "#3a3835";
const AVATAR_BG = "#2b2926";

const SCALES = [1, 0.93, 0.86, 0.8, 0.74] as const;

export interface Scene {
  width: number;
  height: number;
  draw(
    ctx: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D,
    source: CanvasImageSource,
    state: FrameState,
    time: number,
  ): void;
}

interface CardSpec {
  title: string;
  subtitle: string;
  body: string;
  color: string;
  glyph: string | null;
}

interface CardText {
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

function specFor(move: Move): CardSpec {
  return {
    title: move.title,
    subtitle: move.moveNumber,
    body: move.explanation,
    color: MOVE_COLORS[move.moveType],
    glyph: MOVE_GLYPHS[move.moveType],
  };
}

function introSpec(names: Names): CardSpec {
  return {
    title: `${names.white} — ${names.black}`,
    subtitle: "The game begins",
    body: "",
    color: MUTED,
    glyph: null,
  };
}

function bodyChars(body: string[]): number {
  return body.reduce((total, line) => total + line.length, 0) + Math.max(0, body.length - 1);
}

function measureCard(
  ctx: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D,
  spec: CardSpec,
  scale: number,
): { text: CardText; height: number } {
  const width = WIDTH - CARD_PAD_X * 2;
  const titleWidth = width - (spec.glyph === null ? 0 : Math.round(ICON_SIZE * 0.9));
  const titleSize = Math.round(TITLE_SIZE * scale);
  const subtitleSize = Math.round(SUBTITLE_SIZE * Math.max(scale, 0.85));
  const bodySize = Math.round(BODY_SIZE * scale);

  setFont(ctx, 700, titleSize);
  const title = wrapLines(ctx, spec.title, titleWidth).slice(0, 3);
  setFont(ctx, 400, bodySize);
  const body = spec.body === "" ? [] : wrapLines(ctx, spec.body, width);

  const titleLine = Math.round(titleSize * TITLE_LH);
  const subtitleLine = Math.round(subtitleSize * SUBTITLE_LH);
  const bodyLine = Math.round(bodySize * BODY_LH);
  const height =
    CARD_PAD_Y * 2 +
    title.length * titleLine +
    TITLE_GAP +
    subtitleLine +
    (body.length === 0 ? 0 : BODY_GAP + body.length * bodyLine);

  return {
    text: { title, subtitle: spec.subtitle, body, titleSize, subtitleSize, bodySize, titleLine, subtitleLine, bodyLine, bodyChars: bodyChars(body) },
    height,
  };
}

/** Fits one card into the shared height: shrink the type, then drop body lines. */
function layoutCard(
  ctx: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D,
  spec: CardSpec,
  cardHeight: number,
): CardText {
  const available = cardHeight - CARD_PAD_Y * 2;
  let result = measureCard(ctx, spec, 1);
  for (const scale of SCALES) {
    result = measureCard(ctx, spec, scale);
    if (result.height <= available) return result.text;
  }

  const text = result.text;
  const fixed =
    CARD_PAD_Y * 2 + text.title.length * text.titleLine + TITLE_GAP + text.subtitleLine + BODY_GAP;
  const maxLines = Math.max(1, Math.floor((available - fixed) / text.bodyLine));
  if (text.body.length > maxLines) {
    text.body = text.body.slice(0, maxLines);
    setFont(ctx, 400, text.bodySize);
    const last = text.body.length - 1;
    const line = text.body[last];
    if (line !== undefined) text.body[last] = ellipsize(ctx, line, WIDTH - CARD_PAD_X * 2);
    text.bodyChars = bodyChars(text.body);
  }
  return text;
}

/** One card height per video: tall enough for the longest card, so nothing jumps. */
function fitCardHeight(
  ctx: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D,
  specs: CardSpec[],
): number {
  const needed = specs.map((spec) => measureCard(ctx, spec, 1).height);
  const tallest = needed.length === 0 ? CARD_MIN_H : Math.max(...needed);
  return Math.max(CARD_MIN_H, Math.min(CARD_MAX_H, tallest));
}

function sourceSize(source: CanvasImageSource): { width: number; height: number } {
  if (source instanceof HTMLVideoElement) return { width: source.videoWidth, height: source.videoHeight };
  if (source instanceof HTMLCanvasElement || source instanceof OffscreenCanvas || source instanceof ImageBitmap) {
    return { width: source.width, height: source.height };
  }
  return { width: WIDTH, height: HEIGHT };
}

function drawVideo(
  ctx: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D,
  source: CanvasImageSource,
  uiHeight: number,
  time: number,
  frames: FrameFaces[] | null,
): void {
  const boxHeight = HEIGHT - uiHeight;
  const size = sourceSize(source);
  if (size.width <= 0 || size.height <= 0) return;

  const crop = cropRect(frames, time, size.width, size.height, boxHeight);
  if (crop !== null) {
    ctx.drawImage(source, crop.x, crop.y, crop.w, crop.h, 0, uiHeight, WIDTH, boxHeight);
    return;
  }

  const scale = Math.max(WIDTH / size.width, boxHeight / size.height);
  const width = size.width * scale;
  const height = size.height * scale;
  ctx.drawImage(source, (WIDTH - width) / 2, uiHeight + (boxHeight - height) / 2, width, height);
}

/** The crop that keeps the faces in view, in source pixels; `null` means a plain centre crop. */
function cropRect(
  frames: FrameFaces[] | null,
  time: number,
  sourceWidth: number,
  sourceHeight: number,
  boxHeight: number,
): { x: number; y: number; w: number; h: number } | null {
  if (frames === null || frames.length === 0) return null;

  let nearest: FrameFaces | undefined;
  let distance = Number.POSITIVE_INFINITY;
  for (const frame of frames) {
    const next = Math.abs(frame.time - time);
    if (next < distance) {
      distance = next;
      nearest = frame;
    }
  }
  if (nearest === undefined || nearest.boxes.length === 0) return null;

  let cx = 0;
  let cy = 0;
  for (const box of nearest.boxes) {
    cx += box.x + box.width / 2;
    cy += box.y + box.height / 2;
  }
  cx /= nearest.boxes.length;
  cy /= nearest.boxes.length;

  const aspect = WIDTH / boxHeight;
  const sourceAspect = sourceWidth / sourceHeight;
  const w = sourceAspect > aspect ? sourceHeight * aspect : sourceWidth;
  const h = sourceAspect > aspect ? sourceHeight : sourceWidth / aspect;
  const x = Math.max(0, Math.min(sourceWidth - w, cx * sourceWidth - w / 2));
  const y = Math.max(0, Math.min(sourceHeight - h, cy * sourceHeight - h / 2));
  return { x, y, w, h };
}

function drawIcon(
  ctx: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D,
  spec: CardSpec,
  top: number,
  scale: number,
): void {
  if (spec.glyph === null || scale <= 0.01) return;
  const radius = (ICON_SIZE / 2) * scale;
  const cx = WIDTH - 12 - ICON_SIZE / 2;
  const cy = top + (ICON_SIZE / 2) * 0.7;
  ctx.save();
  ctx.globalAlpha *= Math.min(1, scale * 1.4);
  ctx.beginPath();
  ctx.arc(cx, cy, radius, 0, Math.PI * 2);
  ctx.fillStyle = spec.color;
  ctx.fill();
  ctx.fillStyle = TEXT;
  setFont(ctx, 700, ICON_SIZE * 0.42 * scale);
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(spec.glyph, cx, cy + 1);
  ctx.restore();
}

function drawCard(
  ctx: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D,
  text: CardText,
  spec: CardSpec,
  top: number,
  height: number,
  alpha: number,
  iconScale: number,
  typed: number,
): void {
  if (alpha <= 0) return;
  ctx.save();
  ctx.globalAlpha *= alpha;
  ctx.textAlign = "left";
  ctx.textBaseline = "top";

  ctx.fillStyle = getStyle().cardBg;
  ctx.fillRect(0, top, WIDTH, height);
  ctx.fillStyle = spec.color;
  ctx.fillRect(0, top, ACCENT_W, height);

  const x = ACCENT_W + CARD_PAD_X - 8;
  let y = top + CARD_PAD_Y;

  setFont(ctx, 700, text.titleSize);
  ctx.fillStyle = spec.color;
  for (const line of text.title) {
    ctx.fillText(line, x, y);
    y += text.titleLine;
  }

  y += TITLE_GAP;
  setFont(ctx, 400, text.subtitleSize);
  ctx.fillStyle = MUTED;
  ctx.fillText(ellipsize(ctx, text.subtitle, WIDTH - CARD_PAD_X * 2), x, y);
  y += text.subtitleLine;

  if (text.body.length > 0) y += BODY_GAP;
  setFont(ctx, 400, text.bodySize);
  ctx.fillStyle = TEXT;
  let remaining = Math.floor(typed * text.bodyChars);
  for (const line of text.body) {
    if (remaining <= 0) break;
    ctx.fillText(line.slice(0, remaining), x, y);
    remaining -= line.length + 1;
    y += text.bodyLine;
  }

  drawIcon(ctx, spec, top, iconScale);
  ctx.restore();
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter((part) => part !== "");
  const first = parts[0]?.slice(0, 1) ?? "?";
  const second = parts.length > 1 ? (parts[1]?.slice(0, 1) ?? "") : "";
  return `${first}${second}`.toUpperCase();
}

function fittedSize(
  ctx: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D,
  text: string,
  weight: 400 | 600 | 700,
  size: number,
  maxWidth: number,
): number {
  for (let current = size; current > 14; current -= 2) {
    setFont(ctx, weight, current);
    if (ctx.measureText(text).width <= maxWidth) return current;
  }
  return 14;
}

function drawPlayer(
  ctx: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D,
  name: string,
  side: string,
  top: number,
  left: boolean,
  avatar: ImageBitmap | null = null,
): void {
  const avatarY = top + (PLAYERS_H - AVATAR_SIZE) / 2;
  const avatarX = left ? PLAYERS_PAD : WIDTH - PLAYERS_PAD - AVATAR_SIZE;
  const centreX = avatarX + AVATAR_SIZE / 2;
  const centreY = avatarY + AVATAR_SIZE / 2;

  ctx.save();
  ctx.beginPath();
  ctx.arc(centreX, centreY, AVATAR_SIZE / 2 - 2, 0, Math.PI * 2);
  ctx.clip();
  ctx.fillStyle = AVATAR_BG;
  ctx.fillRect(avatarX, avatarY, AVATAR_SIZE, AVATAR_SIZE);
  if (avatar !== null) {
    ctx.drawImage(avatar, avatarX, avatarY, AVATAR_SIZE, AVATAR_SIZE);
  } else {
    setFont(ctx, 700, AVATAR_SIZE * 0.42);
    ctx.fillStyle = TEXT;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(initials(name), centreX, centreY + 1);
  }
  ctx.restore();

  ctx.beginPath();
  ctx.arc(centreX, centreY, AVATAR_SIZE / 2 - 2, 0, Math.PI * 2);
  ctx.lineWidth = 2;
  ctx.strokeStyle = RING;
  ctx.stroke();

  const maxNameWidth = WIDTH / 2 - AVATAR_SIZE - PLAYERS_PAD - 48;
  const nameSize = fittedSize(ctx, name, 700, NAME_SIZE, maxNameWidth);
  setFont(ctx, 700, nameSize);
  const nameLine = Math.round(nameSize * 1.1);
  setFont(ctx, 400, SIDE_SIZE);
  const sideLine = Math.round(SIDE_SIZE * 1.2);
  const blockTop = top + (PLAYERS_H - (nameLine + sideLine)) / 2;

  ctx.textAlign = left ? "left" : "right";
  ctx.textBaseline = "top";
  const textX = left ? avatarX + AVATAR_SIZE + 16 : avatarX - 16;
  setFont(ctx, 700, nameSize);
  ctx.fillStyle = TEXT;
  ctx.fillText(name, textX, blockTop);
  setFont(ctx, 400, SIDE_SIZE);
  ctx.fillStyle = MUTED;
  ctx.fillText(side, textX, blockTop + nameLine + 2);
}

function drawEval(
  ctx: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D,
  score: number,
  top: number,
): void {
  const fraction = Math.max(0.02, Math.min(0.98, 0.5 + 0.5 * Math.tanh(score / 5)));
  const split = Math.round(WIDTH * fraction);
  ctx.fillStyle = EVAL_BLACK;
  ctx.fillRect(0, top, WIDTH, EVAL_H);
  ctx.fillStyle = EVAL_WHITE;
  ctx.fillRect(0, top, split, EVAL_H);

  setFont(ctx, 700, EVAL_LABEL_SIZE);
  ctx.textBaseline = "middle";
  const centreY = top + EVAL_H / 2;
  const label = formatEval(score);
  if (score >= 0) {
    ctx.textAlign = "left";
    ctx.fillStyle = EVAL_BLACK;
    ctx.fillText(label, 12, centreY);
  } else {
    ctx.textAlign = "right";
    ctx.fillStyle = EVAL_WHITE;
    ctx.fillText(label, WIDTH - 12, centreY);
  }
}

export function createScene(
  moves: Move[],
  names: Names,
  avatars: SceneAvatars | null = null,
  frames: FrameFaces[] | null = null,
  subtitles: TranscriptSegment[] | null = null,
): Scene {
  const measure = new OffscreenCanvas(1, 1).getContext("2d");
  if (measure === null) throw new Error("the browser did not provide a 2D context for the card");

  const specs = moves.map(specFor);
  const intro = introSpec(names);
  const cardHeight = fitCardHeight(measure, [...specs, intro]);
  const cards = specs.map((spec) => layoutCard(measure, spec, cardHeight));
  const introCard = layoutCard(measure, intro, cardHeight);
  const playersTop = TOP_MARGIN + cardHeight;
  const evalTop = playersTop + PLAYERS_H + EVAL_GAP;
  const uiHeight = evalTop + EVAL_H;

  function drawSubtitle(
    ctx: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D,
    time: number,
  ): void {
    if (subtitles === null || !getScheduleOptions().subtitles) return;
    const line = subtitles.find((segment) => time >= segment.start && time <= segment.end);
    if (line === undefined || line.text.trim() === "") return;

    ctx.save();
    ctx.textAlign = "center";
    ctx.textBaseline = "bottom";
    setFont(ctx, 400, 34);
    const maxWidth = WIDTH - 120;
    const words = line.text.split(/\s+/);
    const lines: string[] = [];
    let current = "";
    for (const word of words) {
      const next = current === "" ? word : `${current} ${word}`;
      if (ctx.measureText(next).width > maxWidth && current !== "") {
        lines.push(current);
        current = word;
      } else {
        current = next;
      }
    }
    if (current !== "") lines.push(current);

    const lineHeight = 44;
    const bottom = HEIGHT - 70;
    const top = bottom - lines.length * lineHeight;
    ctx.fillStyle = "rgba(0, 0, 0, 0.55)";
    ctx.fillRect(0, top - 14, WIDTH, lines.length * lineHeight + 28);
    ctx.fillStyle = "#ffffff";
    lines.forEach((text, index) => {
      ctx.fillText(text, WIDTH / 2, top + (index + 1) * lineHeight - 10);
    });
    ctx.restore();
  }

  return {
    width: WIDTH,
    height: HEIGHT,
    draw(ctx, source, state, time) {
      ctx.save();
      ctx.globalAlpha = 1;
      ctx.fillStyle = getStyle().pageBg;
      ctx.fillRect(0, 0, WIDTH, HEIGHT);
      drawVideo(ctx, source, uiHeight, time, frames);
      drawSubtitle(ctx, time);

      const current = cards[state.moveIndex];
      if (state.moveIndex < 0 || current === undefined) {
        drawCard(ctx, introCard, intro, TOP_MARGIN, cardHeight, 1, 1, 1);
      } else if (state.cardMix < 1) {
        const previousIndex = state.moveIndex - 1;
        const previousCard = cards[previousIndex];
        const previousSpec = specs[previousIndex];
        if (previousCard !== undefined && previousSpec !== undefined) {
          drawCard(ctx, previousCard, previousSpec, TOP_MARGIN, cardHeight, 1, 1, 1);
        } else {
          drawCard(ctx, introCard, intro, TOP_MARGIN, cardHeight, 1, 1, 1);
        }
        const spec = specs[state.moveIndex];
        if (spec !== undefined) {
          drawCard(ctx, current, spec, TOP_MARGIN, cardHeight, state.cardMix, state.iconScale, state.typed);
        }
      } else {
        const spec = specs[state.moveIndex];
        if (spec !== undefined) {
          drawCard(ctx, current, spec, TOP_MARGIN, cardHeight, 1, state.iconScale, state.typed);
        }
      }

      ctx.fillStyle = getStyle().playersBg;
      ctx.fillRect(0, playersTop, WIDTH, PLAYERS_H);
      drawPlayer(ctx, names.white, "White", playersTop, true, avatars?.white ?? null);
      drawPlayer(ctx, names.black, "Black", playersTop, false, avatars?.black ?? null);
      drawEval(ctx, state.evalScore, evalTop);
      ctx.restore();
    },
  };
}
