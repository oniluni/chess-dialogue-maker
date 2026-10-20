"""Face analysis shared by avatars, active-speaker detection and smart reframing.

One ffmpeg decode pass over the clip gives:
  * shot cuts: mean abs difference of tiny grayscale thumbnails, every frame;
  * faces: OpenCV YuNet (DNN) at VISION_FPS and on the first frame of every shot;
  * tracks: faces linked by overlap inside a shot, identified across shots with
    SFace embeddings and clustered into persons (the two biggest are the players);
  * mouth activity: motion of the mouth region of the landmark-aligned face minus
    motion of the eye region (= head movement) between consecutive samples;
  * saliency: column/row edge-energy profiles, used when a shot has no faces;
  * avatar candidates: the sharpest, most frontal head crop of each person.

Everything is cached in `<job>/vision.json` + `<job>/faces/person<N>.png`.
Coordinates are normalized to 0..1 of the frame, so any output scale can use them.
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

import config

log = logging.getLogger(__name__)

VERSION = 3
MAX_PERSONS = 6     # faces offered in the characters dialog (by screen presence)
WORK_MAX = 1280       # px, long side of the decoded frames (embeddings, avatar crops)
DETECT_MAX = 640      # px, long side of the frame given to YuNet
SAL_BINS = 32         # saliency profile resolution
EMB_PER_TRACK = 4     # SFace embeddings averaged per track
SAME_PERSON = 0.34    # SFace cosine similarity threshold (OpenCV recommends 0.363 for clean crops)
MIN_SHOT = 6          # frames: no two cuts closer than this
# ArcFace/SFace 112x112 template for: right eye, left eye, nose, right/left mouth corner
TEMPLATE = np.array([[38.29, 51.70], [73.53, 51.50], [56.03, 71.74], [41.55, 92.37], [70.73, 92.20]], np.float32)


class VisionError(RuntimeError):
    pass


@dataclass
class Face:
    frame: int
    track: int
    box: list[float]              # x, y, w, h (normalized)
    marks: list[float]            # 5 landmarks x,y (normalized): r-eye, l-eye, nose, r-mouth, l-mouth
    score: float
    act: float | None = None      # mouth activity vs. the previous sample of the same track

    @property
    def cx(self) -> float:
        return self.box[0] + self.box[2] / 2

    @property
    def cy(self) -> float:
        return self.box[1] + self.box[3] / 2


@dataclass
class Track:
    id: int
    shot: int
    first: int
    last: int
    n: int = 0
    area: float = 0.0             # mean normalized face area
    person: int | None = None
    emb: list[float] | None = None
    quality: float = 0.0          # best avatar-candidate score


@dataclass
class VideoAnalysis:
    width: int
    height: int
    fps: int
    n_frames: int
    cuts: list[int] = field(default_factory=lambda: [0])
    samples: list[int] = field(default_factory=list)
    faces: list[Face] = field(default_factory=list)
    col_profile: list[list[float]] = field(default_factory=list)  # per sample
    row_profile: list[list[float]] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)
    persons: list[int] = field(default_factory=list)  # main persons (0, 1), by screen presence
    person_emb: dict[str, list[float]] = field(default_factory=dict)  # every person with a face crop
    others: list[int] = field(default_factory=list)   # further persons worth naming (rank 2…)
    screen: dict[str, float] = field(default_factory=dict)   # person -> seconds on screen
    crops: dict[str, str] = field(default_factory=dict)      # person -> face crop file (set by cast.apply)
    source_key: str = ""
    version: int = VERSION

    # ------------------------------------------------------------ queries
    def shots(self) -> list[tuple[int, int]]:
        bounds = self.cuts + [self.n_frames]
        return [(a, b) for a, b in zip(bounds, bounds[1:]) if b > a]

    def shot_of(self, frame: int) -> int:
        return max(0, int(np.searchsorted(self.cuts, frame, side="right")) - 1)

    def faces_between(self, f0: int, f1: int) -> list[Face]:
        return [f for f in self.faces if f0 <= f.frame < f1]

    def person_of(self, face: Face) -> int | None:
        return self.tracks[face.track].person

    @property
    def aspect(self) -> float:
        return self.width / self.height

    # ------------------------------------------------------------ persistence
    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), ensure_ascii=False))

    @classmethod
    def load(cls, path: Path) -> "VideoAnalysis":
        d = json.loads(path.read_text())
        d["faces"] = [Face(**f) for f in d["faces"]]
        d["tracks"] = [Track(**t) for t in d["tracks"]]
        return cls(**d)


# ================================================================ helpers
def _source_key(clip: Path) -> str:
    st = clip.stat()
    return f"{st.st_size}:{int(st.st_mtime)}:{config.FPS}:{config.VISION_FPS}"


def _models():
    import cv2
    from make_assets import ensure_models

    paths = ensure_models()
    det = cv2.FaceDetectorYN.create(str(paths["face_detection_yunet_2023mar.onnx"]), "", (320, 320), 0.7, 0.3, 50)
    rec = cv2.FaceRecognizerSF.create(str(paths["face_recognition_sface_2021dec.onnx"]), "")
    return det, rec


def _even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def _iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a[0], a[1], a[0] + a[2], a[1] + a[3]
    bx1, by1, bx2, by2 = b[0], b[1], b[0] + b[2], b[1] + b[3]
    iw, ih = max(0.0, min(ax2, bx2) - max(ax1, bx1)), max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    return inter / (a[2] * a[3] + b[2] * b[3] - inter + 1e-9)


def _frontal(marks: np.ndarray) -> float:
    """1 = looking into the camera, ~0 = profile (from eye/nose symmetry)."""
    re, le, nose = marks[0], marks[1], marks[2]
    eye_d = np.linalg.norm(le - re) + 1e-6
    asym = abs(np.linalg.norm(nose - re) - np.linalg.norm(nose - le)) / eye_d
    return float(np.clip(1.0 - asym, 0.05, 1.0))


def _aligned_gray(gray: np.ndarray, marks: np.ndarray):
    import cv2

    m, _ = cv2.estimateAffinePartial2D(marks.astype(np.float32), TEMPLATE)
    if m is None:
        return None
    return cv2.warpAffine(gray, m, (112, 112), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def _roi_vec(img: np.ndarray) -> np.ndarray:
    import cv2

    small = cv2.resize(img, (img.shape[1] // 2, img.shape[0] // 2), interpolation=cv2.INTER_AREA).astype(np.float32)
    return (small - small.mean()) / (small.std() + 4.0)


def head_crop(frame: np.ndarray, box_px, side_mul: float = 1.9) -> np.ndarray:
    """Head-and-shoulders square around a face box, shifted to stay inside the frame."""
    h, w = frame.shape[:2]
    x, y, fw, fh = box_px
    side = int(min(max(fw, fh) * side_mul, w, h))
    cx, cy = x + fw / 2, y + fh * 0.55
    x0 = int(np.clip(cx - side / 2, 0, w - side))
    y0 = int(np.clip(cy - side / 2, 0, h - side))
    return frame[y0 : y0 + side, x0 : x0 + side]


def detect_faces(det, img_bgr: np.ndarray) -> np.ndarray:
    """YuNet on an image of any size: rows of 15 values in the image's pixel coords."""
    import cv2

    h, w = img_bgr.shape[:2]
    s = min(1.0, DETECT_MAX / max(w, h))
    small = cv2.resize(img_bgr, (_even(w * s), _even(h * s)), interpolation=cv2.INTER_AREA) if s < 1 else img_bgr
    det.setInputSize((small.shape[1], small.shape[0]))
    _, rows = det.detect(small)
    if rows is None:
        return np.zeros((0, 15), np.float32)
    rows = rows.copy()
    rows[:, :14] /= small.shape[1] / w
    return rows


def embed_image(path: Path) -> list[float] | None:
    """SFace embedding of the largest face in a still image (user-supplied avatars)."""
    import cv2

    img = cv2.imread(str(Path(path).expanduser()))
    if img is None:
        return None
    det, rec = _models()
    rows = detect_faces(det, img)
    if not len(rows):
        return None
    row = max(rows, key=lambda r: r[2] * r[3])
    e = rec.feature(rec.alignCrop(img, row)).flatten()
    return (e / (np.linalg.norm(e) + 1e-9)).tolist()


def cosine(a, b) -> float:
    a, b = np.asarray(a), np.asarray(b)
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


# ================================================================ main pass
def analyze_video(clip: Path, faces_dir: Path | None = None) -> VideoAnalysis:
    import cv2

    from ingest import probe

    info = probe(clip)
    fps = config.FPS
    n_frames = max(1, int(info.duration * fps))
    s = min(1.0, WORK_MAX / max(info.width, info.height))
    ww, wh = _even(info.width * s), _even(info.height * s)
    det, rec = _models()
    step = max(1, round(fps / config.VISION_FPS))
    va = VideoAnalysis(width=info.width, height=info.height, fps=fps, n_frames=n_frames, source_key=_source_key(clip))

    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", str(clip), "-an", "-vf", f"fps={fps},scale={ww}:{wh}",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    frame_bytes = ww * wh * 3
    prev_thumb, diffs = None, []
    active: dict[int, dict] = {}          # track id -> {box, frame, aligned}
    best_crop: dict[int, np.ndarray] = {}
    embs: dict[int, list[np.ndarray]] = {}
    emb_frame: dict[int, int] = {}
    areas: dict[int, list[float]] = {}
    i = 0
    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            frame = np.frombuffer(buf, np.uint8).reshape(wh, ww, 3)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            # ---- shot cuts
            thumb = cv2.resize(gray, (64, 36), interpolation=cv2.INTER_AREA).astype(np.float32)
            new_shot = i == 0
            if prev_thumb is not None:
                # mean of the most-changed 40% of pixels: static borders/letterboxing don't dilute a cut
                diff = np.abs(thumb - prev_thumb).ravel()
                d = float(np.partition(diff, int(len(diff) * 0.6))[int(len(diff) * 0.6) :].mean())
                recent = np.median(diffs[-12:]) if diffs else 0.0
                if d > 18 and d > 6 * recent + 6 and i - va.cuts[-1] >= MIN_SHOT:
                    va.cuts.append(i)
                    active.clear()
                    new_shot = True
                diffs.append(d)
            prev_thumb = thumb

            # ---- faces
            if i % step == 0 or new_shot:
                va.samples.append(i)
                sob = np.abs(cv2.Sobel(cv2.resize(gray, (SAL_BINS * 4, SAL_BINS * 4)), cv2.CV_32F, 1, 1, ksize=3))
                cp, rp = sob.reshape(SAL_BINS * 4, SAL_BINS, 4).sum(axis=(0, 2)), sob.reshape(SAL_BINS, 4, -1).sum(axis=(1, 2))
                va.col_profile.append([round(float(v), 4) for v in cp / (cp.sum() + 1e-9)])
                va.row_profile.append([round(float(v), 4) for v in rp / (rp.sum() + 1e-9)])
                shot = len(va.cuts) - 1
                rows = [r for r in detect_faces(det, frame) if r[3] >= wh * 0.05]
                taken: set[int] = set()
                for r in sorted(rows, key=lambda r: -r[2] * r[3]):
                    box = r[:4]
                    marks = r[4:14].reshape(5, 2)
                    # ---- link to a track of this shot
                    tid, best = None, 0.0
                    for t, st in active.items():
                        if t in taken or i - st["frame"] > fps * 1.5:
                            continue
                        ov = _iou(box, st["box"])
                        dist = np.hypot(*(box[:2] + box[2:] / 2 - st["box"][:2] - st["box"][2:] / 2)) / max(box[2], 1)
                        score = ov if ov > 0.2 else (0.15 if dist < 0.6 else 0.0)
                        if score > best:
                            tid, best = t, score
                    if tid is None:
                        tid = len(va.tracks)
                        va.tracks.append(Track(id=tid, shot=shot, first=i, last=i))
                        active[tid] = {"box": box, "frame": i, "aligned": None}
                    taken.add(tid)
                    tr, st = va.tracks[tid], active[tid]
                    tr.last, tr.n = i, tr.n + 1
                    areas.setdefault(tid, []).append(float(box[2] * box[3]) / (ww * wh))

                    # ---- mouth activity
                    act = None
                    al = _aligned_gray(gray, marks)
                    if al is not None:
                        mouth, eyes = _roi_vec(al[76:108, 28:84]), _roi_vec(al[36:66, 20:92])
                        prev = st["aligned"]
                        if prev is not None and i - st["frame"] <= step * 2.5:
                            act = float(np.abs(mouth - prev[0]).mean() - 0.7 * np.abs(eyes - prev[1]).mean())
                        st["aligned"] = (mouth, eyes)
                    st["box"], st["frame"] = box, i

                    # ---- identity + avatar candidate
                    if box[3] >= 40 and len(embs.get(tid, [])) < EMB_PER_TRACK and i - emb_frame.get(tid, -10**9) >= fps // 2:
                        e = rec.feature(rec.alignCrop(frame, r)).flatten()
                        embs.setdefault(tid, []).append(e / (np.linalg.norm(e) + 1e-9))
                        emb_frame[tid] = i
                    if box[3] >= 48:
                        x, y, fw, fh = (int(v) for v in box)
                        face_g = gray[max(0, y) : y + fh, max(0, x) : x + fw]
                        if face_g.size:
                            sharp = cv2.Laplacian(cv2.resize(face_g, (96, 96)), cv2.CV_64F).var()
                            q = float(np.sqrt(box[2] * box[3] / (ww * wh)) * min(1.0, sharp / 120) * _frontal(marks) * r[14])
                            if q > tr.quality:
                                tr.quality = q
                                best_crop[tid] = head_crop(frame, (x, y, fw, fh)).copy()

                    va.faces.append(Face(
                        frame=i, track=tid,
                        box=[round(float(box[0]) / ww, 4), round(float(box[1]) / wh, 4),
                             round(float(box[2]) / ww, 4), round(float(box[3]) / wh, 4)],
                        marks=[round(float(v) / (ww if k % 2 == 0 else wh), 4) for k, v in enumerate(r[4:14])],
                        score=round(float(r[14]), 3),
                        act=None if act is None else round(act, 4),
                    ))
            i += 1
    finally:
        proc.stdout.close()
        proc.wait()
    if i == 0:
        raise VisionError(f"could not decode {clip}")
    va.n_frames = i
    for t in va.tracks:
        t.area = round(float(np.mean(areas.get(t.id, [0.0]))), 5)
        if embs.get(t.id):
            m = np.mean(embs[t.id], axis=0)
            t.emb = [round(float(v), 5) for v in m / (np.linalg.norm(m) + 1e-9)]
    _cluster_persons(va)
    if faces_dir is not None:
        _save_person_crops(va, best_crop, faces_dir)
    log.info("Лица: сцен %d, главных персонажей %d", len(va.cuts), len(va.persons))
    log.debug("vision: %d frames, %d faces, %d tracks, persons=%s", va.n_frames, len(va.faces), len(va.tracks), va.persons)
    return va


def _cluster_persons(va: VideoAnalysis) -> None:
    """Average-linkage clustering of track embeddings; tracks seen together in one
    frame can never be the same person. Persons are ranked by screen presence."""
    tracks = [t for t in va.tracks if t.emb is not None]
    if not tracks:
        return
    frames_of: dict[int, set[int]] = {}
    for f in va.faces:
        frames_of.setdefault(f.track, set()).add(f.frame)
    E = np.array([t.emb for t in tracks])
    sim = E @ E.T
    for a in range(len(tracks)):
        for b in range(a + 1, len(tracks)):
            ta, tb = tracks[a], tracks[b]
            if ta.shot == tb.shot and frames_of.get(ta.id, set()) & frames_of.get(tb.id, set()):
                sim[a, b] = sim[b, a] = -1.0
    clusters = [[k] for k in range(len(tracks))]
    while len(clusters) > 1:
        best, pair = SAME_PERSON, None
        for x in range(len(clusters)):
            for y in range(x + 1, len(clusters)):
                block = sim[np.ix_(clusters[x], clusters[y])]
                if block.min() < -0.5:  # co-occurring tracks
                    continue
                s = float(block.mean())
                if s > best:
                    best, pair = s, (x, y)
        if pair is None:
            break
        x, y = pair
        clusters[x] += clusters.pop(y)
    presence = [sum(tracks[k].area * tracks[k].n for k in c) for c in clusters]
    order = sorted(range(len(clusters)), key=lambda c: -presence[c])
    top = presence[order[0]] or 1.0
    per_sec = len(va.samples) / max(1e-6, va.n_frames / max(1, va.fps)) if va.samples else 1.0
    for rank, c in enumerate(order):
        for k in clusters[c]:
            tracks[k].person = rank
        main = rank < 2 and presence[c] >= 0.05 * top
        if main or (rank < MAX_PERSONS and presence[c] >= 0.02 * top):
            (va.persons if main else va.others).append(rank)
            m = E[clusters[c]].mean(axis=0)
            va.person_emb[str(rank)] = [round(float(v), 5) for v in m / (np.linalg.norm(m) + 1e-9)]
            ids = {tracks[k].id for k in clusters[c]}
            seen = {f.frame for f in va.faces if f.track in ids}
            va.screen[str(rank)] = round(len(seen) / max(per_sec, 1e-6), 1)
    # tracks too small for an embedding: inherit the person of the closest track in the same shot
    for t in va.tracks:
        if t.person is None:
            same_shot = [o for o in tracks if o.shot == t.shot and o.person is not None]
            if same_shot:
                fx = np.mean([f.cx for f in va.faces if f.track == t.id])
                near = min(same_shot, key=lambda o: abs(np.mean([f.cx for f in va.faces if f.track == o.id]) - fx))
                if not (frames_of.get(near.id, set()) & frames_of.get(t.id, set())):
                    t.person = near.person


def _save_person_crops(va: VideoAnalysis, crops: dict[int, np.ndarray], out: Path) -> None:
    import cv2

    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("person*.png"):
        old.unlink()
    for p in va.persons + va.others:
        cands = [t for t in va.tracks if t.person == p and t.id in crops]
        if cands:
            best = max(cands, key=lambda t: t.quality)
            cv2.imwrite(str(out / f"person{p}.png"), crops[best.id])


def person_crop(job_dir: Path, person: int, va: "VideoAnalysis | None" = None) -> Path | None:
    """The face crop of a person; after cast.apply the persons are renumbered and `va.crops` says which file."""
    name = (va.crops.get(str(person)) if va is not None and va.crops else None) or f"person{person}.png"
    p = job_dir / "faces" / name
    return p if p.exists() else None


def load_or_analyze(clip: Path, job_dir: Path) -> VideoAnalysis:
    """Cached `analyze_video` for a job (recomputed when the clip changes)."""
    cache = job_dir / "vision.json"
    if cache.exists():
        try:
            va = VideoAnalysis.load(cache)
            if va.version == VERSION and va.source_key == _source_key(clip):
                return va
        except Exception as e:
            log.warning("Кэш анализа лиц повреждён (%s) — пересчитываю", e)
    va = analyze_video(clip, job_dir / "faces")
    va.save(cache)
    return va
