"""Who speaks each transcript segment: audio clustering + visual active-speaker cues.

Audio: every whisper segment gets a voice embedding (WeSpeaker ResNet34 on Kaldi
fbank features, onnxruntime) and the segments are clustered by cosine similarity;
without the model a hand-made fingerprint (MFCC mean/std, YIN pitch F0, spectral
centroid; plain numpy) is used with lower trust. The two biggest clusters are the
voices; a clip with one voice gives every segment the same voice.

Video (see vision.py): for each segment the two main on-screen persons are judged by
whether their mouth moves; a visible face with a still mouth (reaction shot) points
to the other person, and being in the shot is a weak hint by itself.

Both channels are fused in log-odds once the voice clusters are matched to faces.
Labels are anonymous ("A" = person 0, the most visible face); a label gets a side
(white/black) only when the user supplied an avatar that matches that face. The LLM
receives the labels as hints and decides which label is which player.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

import config
from analyzer import Segment, load_audio

log = logging.getLogger(__name__)
SR = 16000
LABELS = ("A", "B")


@dataclass
class SegmentHint:
    id: int
    start: float
    end: float
    label: str | None        # "A" | "B" | None (no evidence)
    conf: float              # 0.5 .. 1.0
    audio: float | None = None  # P(person/label A) from the voice alone
    video: float | None = None  # P(person/label A) from the picture alone


@dataclass
class SpeakerHints:
    method: str = "none"                     # audio | video | audio+video | none
    sides: dict[str, str | None] = field(default_factory=lambda: {"A": None, "B": None})
    persons: dict[str, int | None] = field(default_factory=lambda: {"A": None, "B": None})
    segments: list[SegmentHint] = field(default_factory=list)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=1))

    @classmethod
    def load(cls, path: Path) -> "SpeakerHints":
        d = json.loads(path.read_text())
        d["segments"] = [SegmentHint(**s) for s in d["segments"]]
        return cls(**d)

    def by_id(self) -> dict[int, SegmentHint]:
        return {s.id: s for s in self.segments}

    def confident(self, min_conf: float = config.SPEAKER_HINT_MIN_CONF) -> list[SegmentHint]:
        return [s for s in self.segments if s.label and s.conf >= min_conf]


def _logit(p: float) -> float:
    p = min(max(p, 1e-3), 1 - 1e-3)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


# ================================================================ audio
_voice_session = None


def _voice_model():
    """WeSpeaker ResNet34 voice-embedding model (onnxruntime, CPU)."""
    global _voice_session
    if _voice_session is None:
        import onnxruntime as ort

        from make_assets import ensure_models

        path = ensure_models()["wespeaker_en_voxceleb_resnet34.onnx"]
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        _voice_session = ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])
    return _voice_session


def _kaldi_mel(n_fft: int = 512, n_mels: int = 80, lo: float = 20.0, hi: float = 8000.0) -> np.ndarray:
    mel = lambda f: 1127 * np.log(1 + f / 700)  # noqa: E731
    fft_f = np.arange(n_fft // 2 + 1) * SR / n_fft
    m, ml, mh = mel(fft_f), mel(lo), mel(hi)
    step = (mh - ml) / (n_mels + 1)
    fb = np.zeros((n_mels, n_fft // 2 + 1))
    for k in range(n_mels):
        left, centre, right = ml + k * step, ml + (k + 1) * step, ml + (k + 2) * step
        fb[k] = np.clip(np.minimum((m - left) / (centre - left), (right - m) / (right - centre)), 0, None)
    return fb * ((fft_f > lo) & (fft_f < hi))


_N_FFT, _WIN, _HOP = 512, 400, 160
_KALDI_FB = _kaldi_mel()
_POVEY = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(_WIN) / (_WIN - 1))) ** 0.85


def fbank(x: np.ndarray) -> np.ndarray:
    """Kaldi-compatible 80-dim log-mel filterbank with mean normalization (WeSpeaker input)."""
    x = x.astype(np.float64) * 32768
    n = 1 + (len(x) - _WIN) // _HOP
    fr = x[np.arange(_WIN)[None, :] + _HOP * np.arange(n)[:, None]]
    fr -= fr.mean(axis=1, keepdims=True)
    fr = np.concatenate([fr[:, :1] * 0.03, fr[:, 1:] - 0.97 * fr[:, :-1]], axis=1) * _POVEY
    feats = np.log(np.maximum((np.abs(np.fft.rfft(fr, _N_FFT)) ** 2) @ _KALDI_FB.T, 1.19e-7))
    return (feats - feats.mean(axis=0)).astype(np.float32)


def voice_embeddings(audio: np.ndarray, segments: list[Segment]) -> list[np.ndarray | None]:
    sess = _voice_model()
    out: list[np.ndarray | None] = []
    for s in segments:
        x = audio[int(s.start * SR) : int(s.end * SR)]
        if len(x) < int(0.4 * SR):
            out.append(None)
            continue
        e = sess.run(None, {"feats": fbank(x)[None]})[0][0]
        out.append(e / (np.linalg.norm(e) + 1e-9))
    return out


# fallback when the voice model is unavailable: hand-made voice fingerprint
_FB = None
_DCT = np.cos(np.pi / 40 * (np.arange(40)[None, :] + 0.5) * np.arange(20)[:, None])


def voice_features(x: np.ndarray) -> np.ndarray | None:
    """[mfcc1..19 mean, mfcc1..19 std, log2 F0, log2 centroid] of voiced frames."""
    global _FB
    if len(x) < int(0.3 * SR):
        return None
    if _FB is None:
        _FB = _kaldi_mel(n_mels=40, lo=60, hi=7600)
    n = 1 + (len(x) - _WIN) // _HOP
    frames = x[np.arange(_WIN)[None, :] + _HOP * np.arange(n)[:, None]] * np.hamming(_WIN)
    power = np.abs(np.fft.rfft(frames, _N_FFT)) ** 2
    energy_db = 10 * np.log10(power.sum(axis=1) + 1e-10)
    voiced = energy_db > energy_db.max() - 30
    if voiced.sum() < 10:
        return None
    p = power[voiced]
    mfcc = (_DCT @ np.log(p @ _FB.T + 1e-10).T).T[:, 1:]
    freqs = np.fft.rfftfreq(_N_FFT, 1 / SR)
    centroid = (p * freqs).sum(axis=1) / (p.sum(axis=1) + 1e-10)
    f0s = []  # YIN-style cumulative mean normalized difference, 70-400 Hz
    lo, hi, w = SR // 400, SR // 70, 640
    for k in np.flatnonzero(voiced)[::2]:
        seg = x[k * _HOP : k * _HOP + w + hi]
        if len(seg) < w + hi:
            break
        d = np.array([np.sum((seg[:w] - seg[tau : tau + w]) ** 2) for tau in range(hi + 1)])
        cmnd = d[1:] * np.arange(1, hi + 1) / (np.cumsum(d[1:]) + 1e-10)
        below = np.flatnonzero(cmnd[lo - 1 :] < 0.15)
        if len(below):
            f0s.append(SR / (lo + below[0]))
    f0 = np.log2(np.median(f0s)) if len(f0s) >= 3 else np.nan
    return np.concatenate([mfcc.mean(axis=0), mfcc.std(axis=0), [f0, np.log2(np.median(centroid) + 1)]])


def _handmade_embeddings(audio: np.ndarray, segments: list[Segment]) -> list[np.ndarray | None]:
    feats = [voice_features(audio[int(s.start * SR) : int(s.end * SR)]) for s in segments]
    ok = [i for i, f in enumerate(feats) if f is not None]
    if len(ok) < 3:
        return [None] * len(segments)
    X = np.array([feats[i] for i in ok])
    X = np.where(np.isnan(X), np.nanmean(X, axis=0), X)
    X = (X - X.mean(0)) / (X.std(0) + 1e-6)
    for sl, gw in ((slice(0, 19), 1.5), (slice(19, 38), 0.5), (slice(38, 39), 2.0), (slice(39, 40), 0.5)):
        X[:, sl] *= gw / math.sqrt(sl.stop - sl.start)
    out: list[np.ndarray | None] = [None] * len(segments)
    for row, i in enumerate(ok):
        out[i] = X[row] / (np.linalg.norm(X[row]) + 1e-9)
    return out


def _cluster(E: np.ndarray, threshold: float) -> list[list[int]]:
    """Average-linkage agglomerative clustering on cosine similarity."""
    sim = E @ E.T
    clusters = [[i] for i in range(len(E))]
    while len(clusters) > 1:
        best, pair = threshold, None
        for a in range(len(clusters)):
            for b in range(a + 1, len(clusters)):
                v = float(sim[np.ix_(clusters[a], clusters[b])].mean())
                if v > best:
                    best, pair = v, (a, b)
        if pair is None:
            break
        clusters[pair[0]] += clusters.pop(pair[1])
    return clusters


def audio_probs(audio: np.ndarray, segments: list[Segment]) -> tuple[list[float | None], float]:
    """P(voice 0) per segment (voice 0 = the voice with the most speech) and the
    reliability 0..1 of the split. One voice everywhere -> all segments ~voice 0."""
    try:
        embs, same, cap = voice_embeddings(audio, segments), 0.38, 1.0
    except Exception as e:
        log.warning("Модель голосов недоступна (%s) — сравниваю голоса упрощённо (MFCC/высота тона)", e)
        embs, same, cap = _handmade_embeddings(audio, segments), 0.15, 0.45
    ok = [i for i, e in enumerate(embs) if e is not None]
    if len(ok) < 2:
        return [None] * len(segments), 0.0
    E = np.array([embs[i] for i in ok])
    dur = np.array([segments[i].end - segments[i].start for i in ok])
    clusters = sorted(_cluster(E, same), key=lambda c: -dur[c].sum())
    main = clusters[:2]
    sim = E @ E.T

    def mean_sim(row: int, members: list[int]) -> float:
        others = [m for m in members if m != row]
        return float(sim[row, others].mean()) if others else float(sim[row, members].mean())

    if len(main) == 1:
        reliability = 0.8 * cap
        p0 = [0.95] * len(ok)
        log.debug("voices: one speaker (%d segments)", len(ok))
    else:
        cent = [E[c].mean(axis=0) for c in main]
        cross = float(cent[0] @ cent[1] / (np.linalg.norm(cent[0]) * np.linalg.norm(cent[1]) + 1e-9))
        reliability = float(np.clip((0.55 - cross) / 0.3, 0, 1)) * cap
        p0 = [_sigmoid((mean_sim(r, main[0]) - mean_sim(r, main[1])) / 0.06) for r in range(len(ok))]
        log.debug("voices: %d clusters, main sizes %s, centroid similarity %.2f, reliability %.2f",
                 len(clusters), [len(c) for c in main], cross, reliability)
    probs: list[float | None] = [None] * len(segments)
    for row, i in enumerate(ok):
        length = min(1.0, (segments[i].end - segments[i].start) / 1.0)  # short bits are unreliable
        probs[i] = 0.5 + (p0[row] - 0.5) * reliability * length
    return probs, reliability


# ================================================================ video
TALK_LEVEL = 0.07   # mouth activity (aligned, normalized ROI difference) above this ~ talking
TALK_SCALE = 0.04


def video_probs(va, segments: list[Segment]) -> list[float | None]:
    """P(person 0 speaks) per segment: a visible face whose mouth moves is talking,
    a visible face with a still mouth (reaction shot) points to the other person."""
    if len(va.persons) < 2:
        return [None] * len(segments)
    samples = np.array(va.samples)
    by_frame: dict[int, list] = {}
    for f in va.faces:
        by_frame.setdefault(f.frame, []).append(f)
    out: list[float | None] = []
    for s in segments:
        f0 = int(s.start * va.fps)
        f1 = max(f0 + 1, int(math.ceil(s.end * va.fps)))
        frames = samples[(samples >= f0) & (samples < f1)]
        if len(frames) == 0:
            out.append(None)
            continue
        seen = {0: 0, 1: 0}
        acts: dict[int, list[float]] = {0: [], 1: []}
        for fr in frames:
            here = {va.person_of(f): f for f in by_frame.get(int(fr), [])}
            for p in (0, 1):
                if p in here:
                    seen[p] += 1
                    if here[p].act is not None:
                        acts[p].append(here[p].act)
        vis = {p: seen[p] / len(frames) for p in (0, 1)}
        if max(vis.values()) < 0.2:
            out.append(None)
            continue
        L = 0.4 * (vis[0] - vis[1])  # weak prior: the one in the shot is talking
        for p, sign in ((0, 1.0), (1, -1.0)):
            if len(acts[p]) >= 2:
                talk = float(np.clip((np.mean(acts[p]) - TALK_LEVEL) / TALK_SCALE, -2.5, 2.5))
                L += sign * 1.1 * talk * min(1.0, 2 * vis[p])
        out.append(_sigmoid(L))
    return out


# ================================================================ fusion
def diarize(segments: list[Segment], clip: Path, va=None, avatar_embs: dict[str, list[float] | None] | None = None) -> SpeakerHints:
    hints = SpeakerHints()
    if not segments:
        return hints
    try:
        pa, rel = audio_probs(load_audio(clip, SR), segments)
    except Exception as e:
        log.warning("Не удалось сравнить голоса: %s", e)
        pa, rel = [None] * len(segments), 0.0
    pv = video_probs(va, segments) if va is not None else [None] * len(segments)
    has_a = rel > 0.15 and any(p is not None for p in pa)
    has_v = any(p is not None for p in pv)

    # match voice cluster 0 to person 0 or person 1
    flip = False
    if has_a and has_v:
        agree = sum((a - 0.5) * (v - 0.5) for a, v in zip(pa, pv) if a is not None and v is not None)
        total = sum(abs((a - 0.5) * (v - 0.5)) for a, v in zip(pa, pv) if a is not None and v is not None)
        if total > 0 and abs(agree) / total < 0.2:
            log.info("Голоса и лица дают разные ответы — доверяю лицам")
            log.debug("voices/faces agreement %.2f", agree / total)
            has_a = False
        flip = agree < 0
    hints.method = "+".join(n for n, ok in (("audio", has_a), ("video", has_v)) if ok) or "none"

    for s, a, v in zip(segments, pa, pv):
        a = None if (a is None or not has_a) else (1 - a if flip else a)
        L = (_logit(a) if a is not None else 0.0) + (_logit(v) if v is not None else 0.0)
        L = max(-6.0, min(6.0, L))
        label = None if abs(L) < 0.05 else ("A" if L > 0 else "B")
        hints.segments.append(SegmentHint(
            id=s.id, start=s.start, end=s.end, label=label, conf=round(_sigmoid(abs(L)), 3),
            audio=None if a is None else round(a, 3), video=None if v is None else round(v, 3),
        ))
    if has_v:
        hints.persons = {"A": 0, "B": 1}
        if avatar_embs and va is not None:
            hints.sides = _sides_from_avatars(va, avatar_embs)
    counts = {lab: sum(1 for h in hints.confident() if h.label == lab) for lab in LABELS}
    src = {"audio": "по голосу", "video": "по лицам", "audio+video": "по голосу и лицам"}.get(hints.method, "нет данных")
    log.info("Кто говорит (%s): голос A — %d реплик, голос B — %d, неясно — %d", src, counts["A"], counts["B"],
             len(hints.segments) - counts["A"] - counts["B"])
    log.debug("speakers (%s): %s", hints.method, " ".join(f"{h.id}:{h.label or '?'}{h.conf:.2f}" for h in hints.segments))
    return hints


def _sides_from_avatars(va, embs: dict[str, list[float] | None]) -> dict[str, str | None]:
    from vision import SAME_PERSON, cosine

    sides: dict[str, str | None] = {"A": None, "B": None}
    scores = []
    for side, e in embs.items():
        if e is None:
            continue
        for label, p in (("A", 0), ("B", 1)):
            pe = va.person_emb.get(str(p))
            if pe is not None:
                scores.append((cosine(e, pe), side, label))
    for sim, side, label in sorted(scores, reverse=True):
        if sim >= SAME_PERSON and sides[label] is None and side not in sides.values():
            sides[label] = side
    if sum(v is not None for v in sides.values()) == 1:  # the other label gets the other side
        known = next(k for k, v in sides.items() if v)
        other = "B" if known == "A" else "A"
        sides[other] = "black" if sides[known] == "white" else "white"
    return sides


# ================================================================ consumers
def label_sides(hints: SpeakerHints, moves) -> dict[str, str]:
    """label -> side, from what the LLM decided (duration-weighted vote) or the avatars."""
    if all(hints.sides.get(lab) for lab in LABELS):
        return dict(hints.sides)
    votes = {(lab, side): 0.0 for lab in LABELS for side in ("white", "black")}
    for h in hints.confident(0.55):
        for m in moves:
            overlap = min(h.end, m.end) - max(h.start, m.start)
            if overlap > 0:
                votes[(h.label, m.speaker)] += overlap * (h.conf - 0.5)
    a_white = votes[("A", "white")] + votes[("B", "black")]
    a_black = votes[("A", "black")] + votes[("B", "white")]
    if a_white == a_black == 0:
        return {}
    return {"A": "white", "B": "black"} if a_white >= a_black else {"A": "black", "B": "white"}


def speaker_at(hints: SpeakerHints | None):
    """time -> person index of whoever is speaking (holding the last speaker between lines)."""
    if hints is None or hints.persons.get("A") is None:
        return None
    lines = sorted((h.start, h.end, hints.persons[h.label]) for h in hints.confident(0.6))
    starts = [ln[0] for ln in lines]

    def at(t: float) -> int | None:
        import bisect

        k = bisect.bisect_right(starts, t) - 1
        return lines[k][2] if k >= 0 else (lines[0][2] if lines else None)

    return at
