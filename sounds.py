"""Notable sound events of a clip: vocal emotions and nonverbal sounds, changes in
speech delivery, music, ambient noise.

Two detectors work together:
  * YAMNet (AudioSet, 521 classes; `assets/models/yamnet.onnx`, onnxruntime), one score
    vector every 0.48 s: laughter, crying, shouting, whispering, sighs, gasps, music
    (with its mood), applause, doors, sirens, shots, rain, ...;
  * the prosody of each transcript segment against the speaker's own usual delivery:
    louder / quieter, higher voice, faster / slower speech, long pauses.
A vocal event is tied to a character only when the speaker analysis is confident
about who was speaking. Every event says whether it is reliable (`certain`).

Results live in `<job>/sounds.json`. Events the person added or edited (`edited`)
survive a new detection; `include` marks the events shown in the video and given
to the analysis as context.
"""
from __future__ import annotations

import csv
import json
import logging
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import numpy as np

import config

log = logging.getLogger(__name__)
FILE = "sounds.json"
VERSION = 1
SR = 16000
HOP, WIN = 0.48, 0.96          # YAMNet frame hop / window, s
TAG_MAX = 3.0                  # s: a long event (music) is tagged in the video only at its start


@dataclass(frozen=True)
class Kind:
    label: str                  # short Russian label (the tag in the video)
    category: str               # voice | delivery | music | ambient
    describe: str               # sentence for the list / the analysis
    classes: tuple[str, ...] = ()   # YAMNet display-name prefixes
    threshold: float = 0.3      # frame score to count
    certain_at: float = 0.6     # peak score that makes it reliable
    min_frames: int = 1
    include: bool = False       # shown in the video by default (when reliable)


KINDS: dict[str, Kind] = {
    # ---- voice: emotions and nonverbal vocalizations
    "laughter": Kind("Смех", "voice", "смех", ("Laughter", "Baby laughter", "Giggle", "Snicker", "Belly laugh", "Chuckle"),
                     0.25, 0.55, include=True),
    "crying": Kind("Плач", "voice", "плач, всхлипы", ("Crying", "Whimper", "Wail", "Baby cry"), 0.25, 0.5, include=True),
    "scream": Kind("Крик", "voice", "крик, повышенный голос", ("Screaming", "Shout", "Yell", "Bellow", "Whoop",
                   "Children shouting"), 0.22, 0.5, include=True),
    "whisper": Kind("Шёпот", "voice", "говорит шёпотом", ("Whispering",), 0.3, 0.6, include=True),
    "sigh": Kind("Вздох", "voice", "вздох", ("Sigh",), 0.25, 0.5, include=True),
    "gasp": Kind("Резкий вдох", "voice", "резкий вдох — испуг или удивление", ("Gasp", "Pant"), 0.25, 0.5, include=True),
    "groan": Kind("Стон", "voice", "стон, кряхтение", ("Groan", "Grunt"), 0.3, 0.55),
    "cough": Kind("Кашель", "voice", "кашель, прочищает горло", ("Cough", "Throat clearing", "Sneeze", "Sniff"), 0.35, 0.6),
    "singing": Kind("Пение", "voice", "поёт или напевает", ("Singing", "Humming", "Whistling", "Rapping", "Choir"),
                    0.3, 0.55),
    # ---- delivery (prosody against the speaker's usual manner; see delivery_events)
    "louder": Kind("Громче", "delivery", "говорит заметно громче обычного"),
    "quieter": Kind("Тише", "delivery", "говорит заметно тише обычного"),
    "higher": Kind("Голос выше", "delivery", "голос выше обычного — волнение, напряжение"),
    "faster": Kind("Быстрее", "delivery", "говорит быстрее обычного"),
    "slower": Kind("Медленно", "delivery", "говорит медленно, с расстановкой"),
    "pause": Kind("Пауза", "delivery", "долгая пауза в разговоре"),
    # ---- music
    "music": Kind("Музыка", "music", "звучит музыка", ("Music", "Musical instrument", "Background music", "Soundtrack",
                  "Theme music", "Song", "Orchestra", "Piano", "Guitar", "Drum", "Synthesizer", "Violin", "String section"),
                  0.35, 0.55, min_frames=3, include=True),
    # ---- ambient and other notable sounds
    "applause": Kind("Аплодисменты", "ambient", "аплодисменты, одобрительные крики", ("Applause", "Clapping", "Cheering"),
                     0.3, 0.55, include=True),
    "crowd": Kind("Гул толпы", "ambient", "гул голосов вокруг", ("Crowd", "Chatter", "Hubbub", "Children playing"), 0.3, 0.6,
                  min_frames=2),
    "door": Kind("Дверь", "ambient", "дверь, стук", ("Door", "Slam", "Knock", "Sliding door"), 0.3, 0.55, include=True),
    "phone": Kind("Телефон", "ambient", "звонит телефон", ("Telephone", "Ringtone"), 0.3, 0.55, include=True),
    "siren": Kind("Сирена", "ambient", "сирена или сигнализация", ("Siren", "Civil defense siren", "Police car", "Ambulance",
                  "Fire engine", "Alarm", "Buzzer", "Car alarm"), 0.3, 0.55, include=True),
    "shot": Kind("Выстрел", "ambient", "выстрел или взрыв", ("Gunshot", "Machine gun", "Explosion", "Boom", "Fusillade",
                 "Artillery fire", "Bang"), 0.25, 0.5, include=True),
    "glass": Kind("Звон стекла", "ambient", "звон или бьющееся стекло", ("Glass", "Shatter", "Breaking", "Smash", "Chink"),
                  0.3, 0.55),
    "footsteps": Kind("Шаги", "ambient", "шаги", ("Walk, footsteps", "Run"), 0.35, 0.6),
    "vehicle": Kind("Машина", "ambient", "шум машины или мотора", ("Vehicle", "Car", "Motor vehicle", "Engine",
                    "Traffic noise", "Truck", "Motorcycle"), 0.35, 0.6, min_frames=2),
    "weather": Kind("Непогода", "ambient", "дождь, гроза или ветер", ("Rain", "Thunder", "Wind"), 0.35, 0.6, min_frames=2),
    "animal": Kind("Животное", "ambient", "голос животного", ("Dog", "Bark", "Cat", "Meow", "Bird", "Growling", "Roar"),
                   0.35, 0.6),
    "tv": Kind("Телевизор", "ambient", "работает телевизор или радио", ("Television", "Radio"), 0.4, 0.6, min_frames=2),
    "other": Kind("Звук", "ambient", "другой заметный звук"),
}
CATEGORIES = {"voice": "Голос", "delivery": "Подача речи", "music": "Музыка", "ambient": "Фон и шумы"}
MUSIC_MOODS = {"Sad music": "грустная", "Happy music": "весёлая", "Tender music": "нежная", "Exciting music": "напряжённая",
               "Angry music": "агрессивная", "Scary music": "тревожная"}
KIND_CHOICES = [(f"{CATEGORIES[k.category]} · {k.label}", key) for key, k in KINDS.items()]


@dataclass
class Event:
    id: str
    start: float
    end: float
    kind: str
    label: str
    description: str
    category: str
    confidence: float = 0.0
    certain: bool = False
    speaker: str | None = None      # "white" | "black" when reliably known
    include: bool = False
    source: str = "auto"            # auto | user
    edited: bool = False

    def tag(self, names: dict[str, str]) -> str:
        who = names.get(self.speaker or "", "")
        return f"{self.label} · {who}" if who else self.label


@dataclass
class Sounds:
    events: list[Event] = field(default_factory=list)
    method: str = ""                 # yamnet+prosody | prosody | manual
    notes: list[str] = field(default_factory=list)
    updated: float = 0.0
    version: int = VERSION

    @classmethod
    def exists(cls, job_dir: Path) -> bool:
        return (job_dir / FILE).exists()

    @classmethod
    def load(cls, job_dir: Path) -> "Sounds":
        p = job_dir / FILE
        if not p.exists():
            return cls()
        d = json.loads(p.read_text())
        names = {f.name for f in fields(Event)}
        return cls([Event(**{k: v for k, v in e.items() if k in names}) for e in d.get("events", [])],
                   d.get("method", ""), d.get("notes", []), d.get("updated", 0.0), d.get("version", VERSION))

    def save(self, job_dir: Path) -> None:
        self.events.sort(key=lambda e: (e.start, e.end))
        self.updated = time.time()
        tmp = job_dir / (FILE + ".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=1))
        tmp.replace(job_dir / FILE)

    def event(self, eid: str | None) -> Event | None:
        return next((e for e in self.events if e.id == eid), None)

    def included(self) -> list[Event]:
        return [e for e in self.events if e.include]


def new_id() -> str:
    return "s" + uuid.uuid4().hex[:8]


# ================================================================ audio
def load_audio(clip: Path) -> np.ndarray:
    proc = subprocess.run(["ffmpeg", "-v", "error", "-i", str(clip), "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                          capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg could not read the audio of {clip.name}")
    return np.frombuffer(proc.stdout, np.float32).copy()


def model_ready() -> bool:
    return config.SOUND_MODEL.exists() and config.SOUND_CLASSES.exists()


_session = None
_classes: list[str] = []


def _yamnet():
    global _session, _classes
    if _session is None:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        _session = ort.InferenceSession(str(config.SOUND_MODEL), opts, providers=["CPUExecutionProvider"])
        with open(config.SOUND_CLASSES, newline="") as f:
            _classes = [r[2] for r in list(csv.reader(f))[1:]]
    return _session, _classes


def classify(audio: np.ndarray) -> tuple[np.ndarray, list[str]]:
    """(frames × 521 scores, class names); one frame per 0.48 s."""
    sess, names = _yamnet()
    if len(audio) < int(WIN * SR):
        audio = np.pad(audio, (0, int(WIN * SR) - len(audio)))
    scores = sess.run(None, {sess.get_inputs()[0].name: audio.astype(np.float32)})[0]
    return scores, names


def _class_index(names: list[str], prefixes: tuple[str, ...]) -> list[int]:
    return [i for i, n in enumerate(names) if any(n == p or n.startswith(p + ",") or n.startswith(p + " ") for p in prefixes)]


def class_events(scores: np.ndarray, names: list[str], duration: float) -> list[Event]:
    out: list[Event] = []
    for key, k in KINDS.items():
        if not k.classes:
            continue
        idx = _class_index(names, k.classes)
        if not idx:
            continue
        s = scores[:, idx].max(axis=1)
        active = s >= k.threshold
        i, n = 0, len(s)
        while i < n:
            if not active[i]:
                i += 1
                continue
            j = i
            while j + 1 < n and (active[j + 1] or (j + 2 < n and active[j + 2])):  # bridge a one-frame dip
                j += 1
            if j - i + 1 >= k.min_frames:
                peak = float(s[i : j + 1].max())
                start, end = round(i * HOP, 2), round(min(duration, j * HOP + WIN), 2)
                desc = k.describe
                if key == "music":
                    moods = {m: float(scores[i : j + 1, _class_index(names, (m,))].max()) for m in MUSIC_MOODS
                             if _class_index(names, (m,))}
                    best = max(moods.items(), key=lambda kv: kv[1], default=(None, 0))
                    if best[0] and best[1] >= 0.15:
                        desc += f", характер — {MUSIC_MOODS[best[0]]}"
                out.append(Event(new_id(), start, end, key, k.label, desc, k.category, round(peak, 2),
                                 peak >= k.certain_at, include=k.include and peak >= k.certain_at))
            i = j + 1
    return out


# ================================================================ prosody
def _frames(x: np.ndarray, win: int, hop: int) -> np.ndarray:
    n = 1 + max(0, len(x) - win) // hop
    return x[np.arange(win)[None, :] + hop * np.arange(n)[:, None]] if len(x) >= win else np.zeros((0, win), x.dtype)


def segment_prosody(x: np.ndarray) -> tuple[float, float | None]:
    """(loudness dB of the voiced part, median pitch in semitones or None)."""
    fr = _frames(x, 640, 320)  # 40 ms / 20 ms
    if not len(fr):
        return -90.0, None
    rms = np.sqrt((fr ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms)
    voiced = db > db.max() - 25
    loud = float(np.percentile(db[voiced], 90)) if voiced.any() else float(db.max())
    # pitch by autocorrelation of the voiced frames, 70..450 Hz
    lo, hi = SR // 450, SR // 70
    f = fr[voiced][::2] * np.hanning(fr.shape[1])
    if len(f) < 3:
        return loud, None
    spec = np.fft.rfft(f, 2048, axis=1)
    ac = np.fft.irfft(np.abs(spec) ** 2, axis=1)[:, : hi + 1]
    ac = ac / (ac[:, :1] + 1e-12)
    lag = lo + np.argmax(ac[:, lo : hi + 1], axis=1)
    strength = ac[np.arange(len(ac)), lag]
    good = strength > 0.45
    if good.sum() < 3:
        return loud, None
    hz = SR / lag[good]
    return loud, float(12 * np.log2(np.median(hz) / 55.0))


def _robust_z(values: list[float]) -> np.ndarray:
    v = np.array(values, float)
    med = np.median(v)
    mad = np.median(np.abs(v - med)) * 1.4826
    return (v - med) / max(mad, 1e-6)


def delivery_events(audio: np.ndarray, segments, label_of: dict[int, str | None]) -> list[Event]:
    """Segments whose loudness, pitch or speech rate stand out from the same speaker's
    usual manner (robust z-score ≥ 2), and long pauses between lines."""
    rows = []
    for s in segments:
        dur = s.end - s.start
        if dur < 0.6 or not s.text.strip():
            continue
        loud, pitch = segment_prosody(audio[int(s.start * SR) : int(s.end * SR)])
        chars = len("".join(s.text.split()))
        rows.append((s, loud, pitch, chars / dur, label_of.get(s.id)))
    out: list[Event] = []
    groups: dict[str | None, list] = {}
    for r in rows:
        groups.setdefault(r[4], []).append(r)
    for label, items in groups.items():
        if len(items) < 4:  # too few lines to know this speaker's usual manner
            items = rows if len(rows) >= 4 else []
        if not items:
            continue
        zs = {"loud": _robust_z([r[1] for r in items]), "rate": _robust_z([r[3] for r in items])}
        pitched = [r for r in items if r[2] is not None]
        zp = dict(zip((id(r) for r in pitched), _robust_z([r[2] for r in pitched]))) if len(pitched) >= 4 else {}
        for n, r in enumerate(items):
            if r[4] != label:
                continue
            s = r[0]
            checks = [("louder", zs["loud"][n] >= 2.0, zs["loud"][n]), ("quieter", zs["loud"][n] <= -2.0, -zs["loud"][n]),
                      ("faster", zs["rate"][n] >= 2.0, zs["rate"][n]), ("slower", zs["rate"][n] <= -2.0, -zs["rate"][n])]
            if id(r) in zp:
                checks.append(("higher", zp[id(r)] >= 2.0, zp[id(r)]))
            for key, hit, z in checks:
                if hit:
                    k = KINDS[key]
                    out.append(Event(new_id(), round(s.start, 2), round(s.end, 2), key, k.label, k.describe, k.category,
                                     round(min(1.0, float(z) / 4), 2), bool(z >= 3.0)))
    ordered = sorted(segments, key=lambda s: s.start)
    for a, b in zip(ordered, ordered[1:]):
        gap = b.start - a.end
        if gap >= 1.5:
            k = KINDS["pause"]
            out.append(Event(new_id(), round(a.end, 2), round(b.start, 2), "pause", f"Пауза {gap:.1f} с",
                             k.describe, k.category, 1.0, True))
    return out


# ================================================================ detection
def _speaker_for(e: Event, segments, label_of, conf_of, label_side: dict[str, str]) -> str | None:
    """The side of whoever speaks during a vocal event — only when the speaker analysis is sure."""
    best, best_ov = None, 0.0
    for s in segments:
        ov = min(e.end, s.end) - max(e.start, s.start)
        if ov > best_ov:
            best, best_ov = s, ov
    if best is None or best_ov < 0.5 * min(e.end - e.start, best.end - best.start):
        return None
    label = label_of.get(best.id)
    if not label or conf_of.get(best.id, 0) < 0.75:
        return None
    return label_side.get(label)


def detect(clip: Path, segments, hints=None, label_side: dict[str, str] | None = None) -> Sounds:
    """All events of a clip. `hints` = speakers.SpeakerHints, `label_side` = {"A": "white", …}."""
    from ingest import probe

    duration = probe(clip).duration
    audio = load_audio(clip)
    label_of = {h.id: h.label for h in hints.segments} if hints else {}
    conf_of = {h.id: h.conf for h in hints.segments} if hints else {}
    label_side = label_side or {}
    events: list[Event] = []
    notes: list[str] = []
    method = "prosody"
    if model_ready():
        try:
            scores, names = classify(audio)
            events += class_events(scores, names, duration)
            method = "yamnet+prosody"
        except Exception as e:
            log.warning("Не удалось распознать звуки моделью (%s) — только анализ речи", e)
            notes.append("модель звуков не сработала — найдены только изменения подачи речи")
    else:
        notes.append("модель звуков не скачана (python app.py assets) — найдены только изменения подачи речи")
    if segments:
        events += delivery_events(audio, segments, label_of)
    # a shout detected by the model already explains "louder": keep the stronger one
    loud = [e for e in events if e.kind == "scream"]
    events = [e for e in events if not (e.kind == "louder" and any(min(e.end, s.end) - max(e.start, s.start) > 0 for s in loud))]
    for e in events:
        if e.category in ("voice", "delivery") and e.kind != "pause":
            e.speaker = _speaker_for(e, segments or [], label_of, conf_of, label_side)
    events.sort(key=lambda e: e.start)
    counts: dict[str, int] = {}
    for e in events:
        counts[CATEGORIES[e.category]] = counts.get(CATEGORIES[e.category], 0) + 1
    log.info("Звуки: %s", ", ".join(f"{k.lower()} — {v}" for k, v in counts.items()) or "ничего заметного")
    return Sounds(events, method, notes)


def merge_redetected(old: Sounds, new: Sounds) -> Sounds:
    """A new detection replaces the automatic events; the person's ones stay."""
    kept = [e for e in old.events if e.edited or e.source == "user"]
    fresh = [e for e in new.events
             if not any(k.kind == e.kind and min(k.end, e.end) - max(k.start, e.start) > 0 for k in kept)]
    return Sounds(kept + fresh, new.method, new.notes)


# ================================================================ editing
def validate(start, end, kind: str, label: str, duration: float | None) -> list[str]:
    errs = []
    if start is None or end is None:
        errs.append("Укажите начало и конец события.")
    elif end - start < 0.1:
        errs.append("Конец события должен быть позже начала.")
    elif duration and start >= duration:
        errs.append("Начало события дальше конца клипа.")
    if kind not in KINDS:
        errs.append("Выберите тип события.")
    if not (label or "").strip():
        errs.append("Подпись события пустая — она показывается в видео.")
    elif len(label) > 40:
        errs.append("Подпись длиннее 40 символов — сократите, в видео она не поместится.")
    return errs


def render_tags(snd: Sounds, names: dict[str, str]):
    """The included events as renderer.SoundTag (a long event only at its start)."""
    from renderer import SoundTag

    return [SoundTag(e.start, min(e.end, e.start + TAG_MAX), e.tag(names)) for e in snd.included()]


def context_text(snd: Sounds, names: dict[str, str]) -> str:
    """A line for the LLM about the notable sounds (included or reliable events)."""
    items = [e for e in snd.events if e.include or (e.certain and e.kind != "pause")]
    if not items:
        return ""
    parts = []
    for e in items[:24]:
        who = names.get(e.speaker or "", "")
        mark = "" if e.certain or e.edited else " (не точно)"
        parts.append(f"{e.start:.1f}–{e.end:.1f} с: {e.description}{f' ({who})' if who else ''}{mark}")
    return "Звуки и подача речи: " + "; ".join(parts) + "."
