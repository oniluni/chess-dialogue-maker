"""Smart reframing: which part of every source frame fills the output video box.

`plan()` decides, per source frame, a crop window (normalized 0..1 coords) and a
mode:
  * CROP: the window (box aspect) is cut out and scaled; it follows the faces;
  * FIT: the whole frame is fitted into the box over a blurred, zoomed copy of
    itself (no black bars); used for vertical sources and for shots whose people
    don't fit into one window when we don't know who is talking;
  * LETTERBOX: legacy VIDEO_MODE=fit, the whole frame on black.

VIDEO_MODE=smart works per shot (cuts come from vision.py):
  * a vertical source keeps its composition: fitted, never re-cropped;
  * all faces of the shot fit into the window: a locked "camera" framing them,
    or a smoothed follow when a face travels across the frame;
  * faces too far apart: the camera eases to whoever is speaking (speaker hints),
    or the shot is fitted over a blurred background;
  * no faces: the window that holds most of the detail (edge energy: hands,
    a chess board, objects), with a mild preference for the centre.
The plan depends only on the source frame, so a frozen frame (pause-on-move)
keeps exactly the framing of the frame it froze on.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

import numpy as np

import config

log = logging.getLogger(__name__)

CROP, FIT, LETTERBOX = 0, 1, 2
VERTICAL_ASPECT = 0.7     # w/h at or below this counts as an already-vertical source
MAX_STATIC_LOSS = 0.12    # vertical source: allow a cover crop only if it loses at most this much
FACE_MARGIN = 0.35        # extra room around faces, in face widths
HEADROOM = 0.14           # top of the highest face sits this far below the window top
PAN_S = 0.45              # s, speaker-focus camera move
FOLLOW_S = 0.8            # s, smoothing window when following a moving face


_KIND_RU = {"static": "статичная камера", "follow": "слежение", "speaker": "за говорящим",
            "fit": "целиком на размытом фоне", "detail": "по деталям"}


@dataclass
class Plan:
    mode: np.ndarray      # (n,) int
    x: np.ndarray         # (n,) window left/top/width/height, normalized
    y: np.ndarray
    w: float
    h: float

    def at(self, i: int) -> tuple[int, float, float, float, float]:
        i = min(max(i, 0), len(self.mode) - 1)
        return int(self.mode[i]), float(self.x[i]), float(self.y[i]), self.w, self.h


def window(src_w: int, src_h: int, box_w: int, box_h: int) -> tuple[float, float]:
    """Largest window with the box aspect inside the source (normalized w, h)."""
    box_a, src_a = box_w / box_h, src_w / src_h
    return (box_a / src_a, 1.0) if src_a > box_a else (1.0, src_a / box_a)


def _ease(t: np.ndarray) -> np.ndarray:
    t = np.clip(t, 0, 1)
    return np.where(t < 0.5, 4 * t**3, 1 - (-2 * t + 2) ** 3 / 2)


def _smooth(v: np.ndarray, frames: int) -> np.ndarray:
    if frames < 3 or len(v) < 3:
        return v
    k = np.hanning(frames + 2)[1:-1]
    k /= k.sum()
    pad = frames // 2
    return np.convolve(np.pad(v, pad, mode="edge"), k, mode="same")[pad : pad + len(v)]


def _interp_track(frames: np.ndarray, values: np.ndarray, f0: int, f1: int) -> np.ndarray:
    return np.interp(np.arange(f0, f1), frames, values)


def plan(
    va,
    src_w: int,
    src_h: int,
    box_w: int,
    box_h: int,
    n_frames: int,
    fps: int,
    speaker_at: Callable[[float], int | None] | None = None,
    mode: str = config.VIDEO_MODE,
) -> Plan:
    w, h = window(src_w, src_h, box_w, box_h)
    n = max(1, n_frames)
    modes = np.full(n, CROP, dtype=np.int8)
    xs = np.full(n, (1 - w) / 2)
    ys = np.full(n, (1 - h) * config.VIDEO_ANCHOR_Y)
    if mode == "fit":
        modes[:] = LETTERBOX
        return Plan(modes, xs, ys, w, h)
    if mode in ("fill", "crop"):
        return Plan(modes, xs, ys, w, h)

    loss = 1 - w * h
    if src_w / src_h <= VERTICAL_ASPECT:
        if loss > MAX_STATIC_LOSS:
            modes[:] = FIT
        elif va is not None and va.faces:  # static crop, vertical position from the faces
            ys[:] = np.clip(np.median([f.box[1] for f in va.faces]) - HEADROOM * h, 0, 1 - h)
        log.info("Кадрирование: вертикальное видео — %s", "целиком на размытом фоне" if loss > MAX_STATIC_LOSS else "лёгкая обрезка")
        return Plan(modes, xs, ys, w, h)
    if va is None or loss < 0.01:
        return Plan(modes, xs, ys, w, h)

    stats = {"static": 0, "follow": 0, "speaker": 0, "fit": 0, "detail": 0}
    for f0, f1 in va.shots():
        f1 = min(f1, n)
        if f0 >= f1:
            continue
        kind = _plan_shot(va, f0, f1, w, h, xs, ys, modes, fps, speaker_at)
        stats[kind] += 1
    if va.n_frames < n:  # decoded a frame or two more than vision saw: hold the last framing
        xs[va.n_frames :], ys[va.n_frames :], modes[va.n_frames :] = xs[va.n_frames - 1], ys[va.n_frames - 1], modes[va.n_frames - 1]
    log.info("Кадрирование по сценам: %s", ", ".join(f"{_KIND_RU[k]} {v}" for k, v in stats.items() if v) or "по центру")
    log.debug("reframe: window %.2fx%.2f, shots %s", w, h, stats)
    return Plan(modes, np.clip(xs, 0, 1 - w), np.clip(ys, 0, 1 - h), w, h)


def _plan_shot(va, f0, f1, w, h, xs, ys, modes, fps, speaker_at) -> str:
    faces = va.faces_between(f0, f1)
    if faces:
        biggest = max(f.box[3] for f in faces)
        faces = [f for f in faces if f.box[3] >= 0.45 * biggest]  # ignore background faces
    if not faces:
        s0 = int(np.searchsorted(va.samples, f0))
        s1 = int(np.searchsorted(va.samples, f1))
        if s1 > s0 and va.col_profile:
            xs[f0:f1] = _detail_pos(np.mean(va.col_profile[s0:s1], axis=0), w)
            if h < 1:
                ys[f0:f1] = _detail_pos(np.mean(va.row_profile[s0:s1], axis=0), h, centre_bias=0.5)
        return "detail"

    tracks: dict[int, list] = {}
    for f in faces:
        tracks.setdefault(f.track, []).append(f)
    lo = np.percentile([f.box[0] for f in faces], 5)
    hi = np.percentile([f.box[0] + f.box[2] for f in faces], 95)
    top = float(np.percentile([f.box[1] for f in faces], 10))
    if h < 1:
        ys[f0:f1] = top - HEADROOM * h

    main = max(tracks.values(), key=len)
    travel = float(np.ptp([f.cx for f in main]))
    single = len(tracks) == 1 or all(len(t) < 0.2 * len(main) for t in tracks.values() if t is not main)
    if single and travel > 0.3 * w:  # one face moving across the frame: follow it, smoothed
        cx = _interp_track(np.array([f.frame for f in main]), np.array([f.cx for f in main]), f0, f1)
        xs[f0:f1] = _smooth(cx, int(FOLLOW_S * fps)) - w / 2
        return "follow"
    if single:
        xs[f0:f1] = float(np.median([f.cx for f in main])) - w / 2
        return "static"
    if hi - lo <= w:  # everyone fits: centre the group, with margins where there is room
        margin = FACE_MARGIN * float(np.median([f.box[2] for f in faces]))
        lo, hi = max(lo - margin, hi - w), min(hi + margin, lo + w)
        xs[f0:f1] = (lo + hi) / 2 - w / 2
        return "static"

    persons = {va.person_of(t[0]) for t in tracks.values()} - {None}
    if speaker_at is not None and len(persons) >= 2:
        centre: dict[int, np.ndarray] = {}
        for p in persons:
            pf = sorted((f for t in tracks.values() if va.person_of(t[0]) == p for f in t), key=lambda f: f.frame)
            centre[p] = _interp_track(np.array([f.frame for f in pf]), np.array([f.cx for f in pf]), f0, f1)
        cur = max(persons, key=lambda p: sum(len(t) for t in tracks.values() if va.person_of(t[0]) == p))
        target = np.empty(f1 - f0)
        move_start, move_from = None, 0.0
        pan = max(1, int(PAN_S * fps))
        for k in range(f1 - f0):
            spk = speaker_at((f0 + k) / fps)
            if spk in centre and spk != cur:
                move_from = target[k - 1] if k else centre[cur][k]
                cur, move_start = spk, k
            x = centre[cur][k]
            if move_start is not None and k - move_start < pan:
                x = move_from + (x - move_from) * float(_ease(np.array((k - move_start) / pan)))
            target[k] = x
        xs[f0:f1] = target - w / 2
        return "speaker"

    modes[f0:f1] = FIT
    return "fit"


def _detail_pos(profile: np.ndarray, size: float, centre_bias: float = 0.3) -> float:
    """Window start (normalized) holding most of the profile's energy."""
    bins = len(profile)
    k = max(1, int(round(size * bins)))
    if k >= bins:
        return 0.0
    sums = np.convolve(profile, np.ones(k), mode="valid")
    starts = np.arange(len(sums)) / bins
    prior = 1 - centre_bias * np.abs(starts + size / 2 - 0.5) * 2
    return float(starts[int(np.argmax(sums * prior))])


class Framer:
    """Turns a decoded RGB frame + a plan row into the video box image."""

    def __init__(self, box_w: int, box_h: int):
        self.bw, self.bh = box_w, box_h

    def render(self, frame: np.ndarray, row: tuple[int, float, float, float, float]) -> np.ndarray:
        import cv2

        mode, x, y, w, h = row
        H, W = frame.shape[:2]
        if mode == CROP:
            cw, ch = w * W, h * H
            s = self.bw / cw
            if s >= 0.75:  # sub-pixel accurate, so slow pans don't jitter
                m = np.float32([[s, 0, -x * W * s], [0, s, -y * H * s]])
                return cv2.warpAffine(frame, m, (self.bw, self.bh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            x0, y0 = int(round(x * W)), int(round(y * H))
            crop = frame[y0 : y0 + int(round(ch)), x0 : x0 + int(round(cw))]
            return cv2.resize(crop, (self.bw, self.bh), interpolation=cv2.INTER_AREA)

        s = min(self.bw / W, self.bh / H)
        fw, fh = max(2, int(W * s)), max(2, int(H * s))
        fg = cv2.resize(frame, (fw, fh), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
        if mode == LETTERBOX:
            out = np.zeros((self.bh, self.bw, 3), np.uint8)
        else:
            cw, ch = window(W, H, self.bw, self.bh)
            x0, y0 = int((1 - cw) / 2 * W), int((1 - ch) / 2 * H)
            bg = frame[y0 : y0 + int(ch * H), x0 : x0 + int(cw * W)]
            small = cv2.resize(bg, (max(8, self.bw // 12), max(8, self.bh // 12)), interpolation=cv2.INTER_AREA)
            small = cv2.GaussianBlur(small, (0, 0), 2.2)
            out = (cv2.resize(small, (self.bw, self.bh), interpolation=cv2.INTER_LINEAR) * 0.5).astype(np.uint8)
        ox, oy = (self.bw - fw) // 2, (self.bh - fh) // 2
        out[oy : oy + fh, ox : ox + fw] = fg
        return out
