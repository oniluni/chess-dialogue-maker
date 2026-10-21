"""Step 2: transcribe the clip with faster-whisper and let an LLM turn the dialogue
into chess.com-style "moves" (strict JSON validated with Pydantic)."""
from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

import config
import llm

log = logging.getLogger(__name__)


class NeedsReview(RuntimeError):
    """Every answer of the model still fails the checks: the person picks / edits the moves
    (recovery.py) instead of getting the best attempt silently. `attempts` are the answers."""

    def __init__(self, attempts: list[dict], lo: int, hi: int):
        super().__init__("Ответы нейросети не прошли проверку — выберите и поправьте варианты ходов.")
        self.attempts, self.lo, self.hi = attempts, lo, hi


class AnalysisError(RuntimeError):
    pass


# ================================================================ transcription
class Word(BaseModel):
    start: float
    end: float
    word: str


class Segment(BaseModel):
    id: int
    start: float
    end: float
    text: str
    words: list[Word] = []


_whisper_model = None


def _load_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel

        log.info("Загружаю модель распознавания речи faster-whisper «%s»…", config.WHISPER_MODEL)
        _whisper_model = WhisperModel(
            config.WHISPER_MODEL, device=config.WHISPER_DEVICE, compute_type=config.WHISPER_COMPUTE_TYPE
        )
    return _whisper_model


def load_audio(media: Path, sr: int = 16000):
    """Decode to 16 kHz mono float32 with ffmpeg (avoids PyAV version issues)."""
    import numpy as np

    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(media), "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise AnalysisError(f"ffmpeg audio decode failed: {proc.stderr.decode(errors='replace')[-500:]}")
    return np.frombuffer(proc.stdout, dtype=np.float32)


def transcribe(media: Path, language: str | None = config.WHISPER_LANGUAGE) -> list[Segment]:
    model = _load_whisper()
    segments, info = model.transcribe(
        load_audio(media),
        language=language,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
    )
    log.debug("whisper: language=%s (p=%.2f)", info.language, info.language_probability)
    out = []
    for seg in segments:
        words = [Word(start=w.start, end=w.end, word=w.word) for w in (seg.words or [])]
        out.extend(_split_on_sentences(len(out), seg.start, seg.end, seg.text.strip(), words))
    for i, s in enumerate(out):
        s.id = i
    log.info("Распознано реплик: %d, язык: %s", len(out), info.language)
    return out


def _split_on_sentences(base_id: int, start: float, end: float, text: str, words: list[Word]) -> list[Segment]:
    """Whisper often glues two speakers' lines into one segment; splitting on
    sentence ends (using word timestamps) gives the LLM finer-grained cut points."""
    if not words:
        return [Segment(id=base_id, start=start, end=end, text=text)]
    parts, cur = [], []
    for w in words:
        cur.append(w)
        if re.search(r"[.!?…]+[»\"']?$", w.word.strip()) and len(cur) >= 3:
            parts.append(cur)
            cur = []
    if cur:
        if parts and len(cur) < 3:
            parts[-1].extend(cur)
        else:
            parts.append(cur)
    return [
        Segment(
            id=base_id + i,
            start=round(p[0].start, 2),
            end=round(p[-1].end, 2),
            text="".join(w.word for w in p).strip(),
            words=p,
        )
        for i, p in enumerate(parts)
    ]


def save_transcript(segments: list[Segment], path: Path) -> None:
    path.write_text(json.dumps([s.model_dump() for s in segments], ensure_ascii=False, indent=1))


def load_transcript(path: Path) -> list[Segment]:
    return [Segment(**s) for s in json.loads(path.read_text())]


# ================================================================ move schema
MoveType = Literal["Brilliant", "Great", "Best", "Inaccuracy", "Mistake", "Blunder"]

_TYPE_ALIASES = {
    **{k.lower(): k for k in config.MOVE_TYPES},
    **{s.label_ru.lower(): s.key for s in config.MOVE_TYPES.values()},
    **{s.glyph: s.key for s in config.MOVE_TYPES.values()},
    "блестящий": "Brilliant", "отличный": "Great", "лучший": "Best",
    "excellent": "Great", "good": "Best", "*": "Best",
}


class _MoveFields(BaseModel):
    """Fields + lenient normalization shared by the final and the LLM-side schema."""

    speaker: Literal["white", "black"]
    move_type: MoveType
    title: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    eval_score: float

    @field_validator("move_type", mode="before")
    @classmethod
    def _norm_type(cls, v):
        key = str(v).strip().strip("«»\"'").lower()
        key = re.sub(r"\s*\(.*\)$", "", key)  # "Brilliant (!!)"
        if key in _TYPE_ALIASES:
            return _TYPE_ALIASES[key]
        for alias, canon in _TYPE_ALIASES.items():
            if len(alias) > 2 and alias in key:
                return canon
        return v  # let Literal produce a readable error

    @field_validator("speaker", mode="before")
    @classmethod
    def _norm_speaker(cls, v):
        s = str(v).strip().lower()
        if s in {"white", "белые", "белый", "1", "player 1", "игрок 1", "w"} or "бел" in s or "white" in s:
            return "white"
        if s in {"black", "чёрные", "черные", "чёрный", "2", "player 2", "игрок 2", "b"} or "чёр" in s or "чер" in s or "black" in s:
            return "black"
        return v

    @field_validator("eval_score", mode="before")
    @classmethod
    def _norm_eval(cls, v):
        if isinstance(v, str):
            m = re.search(r"[-+−]?\d+(?:[.,]\d+)?", v)
            if not m:
                raise ValueError(f"not a number: {v!r}")
            v = float(m.group().replace(",", ".").replace("−", "-"))
        return max(-10.0, min(10.0, round(float(v), 1)))

    @field_validator("title", "explanation", mode="after")
    @classmethod
    def _strip(cls, v: str) -> str:
        return re.sub(r"\s+", " ", v).strip()


class Move(_MoveFields):
    """One move as stored in moves.json and rendered."""

    start: float = Field(ge=0)
    end: float = Field(ge=0)
    move_number: str = Field(min_length=1)

    @field_validator("start", "end", mode="before")
    @classmethod
    def _norm_time(cls, v):
        if isinstance(v, str) and ":" in v:
            mins, secs = v.rsplit(":", 1)
            return float(mins) * 60 + float(secs)
        return v


class Analysis(BaseModel):
    moves: list[Move] = Field(min_length=1)


def sanitize_moves(moves: list[Move], duration: float | None) -> list[Move]:
    """Sort, clamp to the clip, remove overlaps and enforce numbering."""
    moves = sorted(moves, key=lambda m: m.start)
    out: list[Move] = []
    for m in moves:
        if duration:
            m.start, m.end = min(m.start, duration), min(m.end, duration)
        if m.end <= m.start:
            m.end = m.start + 1.0
        if out and m.start < out[-1].end:
            if m.start <= out[-1].start:  # duplicate / fully overlapping: drop it
                continue
            out[-1].end = m.start
        out.append(m)
    for i, m in enumerate(out, 1):
        m.move_number = re.sub(r"^\s*\d+[.)]?\s*", "", m.move_number).strip()
        m.move_number = f"{i} {m.move_number}"
    return out


MOVE_KEYS = ("start", "end", "speaker", "move_number", "move_type", "title", "explanation", "eval_score")


def moves_to_json(moves: list[Move]) -> str:
    rows = [{k: getattr(m, k) for k in MOVE_KEYS} for m in moves]
    return json.dumps({"moves": rows}, ensure_ascii=False, indent=2)


def _as_moves_dict(data):
    """Accept {"moves": [...]}, a bare list, or a dict with a single list value."""
    if isinstance(data, list):
        return {"moves": data}
    if isinstance(data, dict) and "moves" not in data:
        lists = [v for v in data.values() if isinstance(v, list)]
        if len(lists) == 1:
            return {"moves": lists[0]}
    return data


def parse_moves(text: str, duration: float | None = None) -> list[Move]:
    """Robust parse of moves.json-style text: strips code fences / prose, validates."""
    data = _as_moves_dict(extract_json(text))
    return sanitize_moves(Analysis.model_validate(data).moves, duration)


def extract_json(text: str):
    text = llm.strip_reasoning(text)
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    dec = json.JSONDecoder()
    for m in re.finditer(r"[\[{]", text):
        try:
            obj, _ = dec.raw_decode(text[m.start():])
            return obj
        except json.JSONDecodeError:
            continue
    # last resort: trailing commas are the most common LLM JSON sin
    cleaned = re.sub(r",\s*([}\]])", r"\1", text)
    return json.loads(cleaned[cleaned.find("{") : cleaned.rfind("}") + 1])


# ================================================================ LLM-side schema
class LLMMove(_MoveFields):
    """What the model returns: segment ids instead of raw timecodes (small models
    copy timecodes badly), and a short gist instead of the formatted move_number."""

    first_segment: int
    last_segment: int
    gist: str = Field(min_length=1, description="суть хода в 2–5 словах")
    effect: str = Field(default="", description="что изменилось в позиции после хода")
    strength: int | None = Field(default=None, description="сила хода по эффекту, от -3 до +3")

    @field_validator("strength", mode="before")
    @classmethod
    def _norm_strength(cls, v):
        if v is None or v == "":
            return None
        if isinstance(v, str):
            m = re.search(r"[-+−]?\d", v)
            if not m:
                return None
            v = int(m.group().replace("−", "-"))
        return max(-3, min(3, int(round(float(v)))))


# strength the move type stands for; check_llm_moves keeps them consistent
TYPE_STRENGTH = {"Brilliant": 3, "Great": 2, "Best": 1, "Inaccuracy": -1, "Mistake": -2, "Blunder": -3}
# generation order: reason about the effect first, then classify, then write
FIELD_ORDER = ("first_segment", "last_segment", "speaker", "effect", "strength", "move_type", "eval_score",
               "gist", "title", "explanation")


class LLMAnalysis(BaseModel):
    moves: list[LLMMove] = Field(min_length=1)


def target_moves(segments: list[Segment]) -> tuple[int, int]:
    """(min, max) number of moves for a dialogue of this length."""
    span = segments[-1].end - segments[0].start
    hi = max(1, min(12, len(segments), round(span / 4)))
    lo = min(hi, max(2 if len(segments) >= 2 else 1, round(span / 12)))
    return lo, hi


def llm_json_schema(lo: int, hi: int) -> dict:
    schema = LLMAnalysis.model_json_schema()
    schema["properties"]["moves"].update(minItems=lo, maxItems=hi)
    move = schema["$defs"]["LLMMove"]
    move["properties"] = {k: move["properties"][k] for k in FIELD_ORDER if k in move["properties"]}
    move["required"] = [k for k in FIELD_ORDER if k in move["properties"]]
    return schema


PLACEHOLDER_RE = re.compile(r"^(\.{3}|…|имя|суть|заголовок|title|name|gist|<.*>)$", re.I)


def _seg_range(m: LLMMove, n: int) -> range:
    a, b = sorted((min(max(m.first_segment, 0), n - 1), min(max(m.last_segment, 0), n - 1)))
    return range(a, b + 1)


def hint_sides(raw: list[LLMMove], segments: list[Segment], hints) -> dict[str, str]:
    """Voice label -> side: fixed by matching avatars, otherwise what the LLM chose
    for most of the confidently labelled speech."""
    if all(hints.sides.get(lab) for lab in ("A", "B")):
        return dict(hints.sides)
    conf = {h.id: h for h in hints.confident(HINT_TRUST)}
    a_white = a_black = 0.0
    for m in raw:
        for i in _seg_range(m, len(segments)):
            h = conf.get(i)
            if h:
                d = segments[i].end - segments[i].start
                same = (h.label == "A") == (m.speaker == "white")
                a_white, a_black = (a_white + d, a_black) if same else (a_white, a_black + d)
    return {"A": "white", "B": "black"} if a_white >= a_black else {"A": "black", "B": "white"}


def check_speakers(raw: list[LLMMove], segments: list[Segment], hints, names: dict[str, str]) -> list[str]:
    """Moves that contradict confident audio-visual speaker hints."""
    conf = {h.id: h for h in hints.confident(HINT_TRUST)}
    if not conf:
        return []
    sides = hint_sides(raw, segments, hints)
    issues = []
    for k, m in enumerate(raw, 1):
        rng = _seg_range(m, len(segments))
        labels = sorted({conf[i].label for i in rng if i in conf})
        if len(labels) > 1:
            issues.append(f"Ход {k} (сегменты {rng.start}–{rng.stop - 1}) объединяет реплики разных людей "
                          f"(голоса {' и '.join(labels)}) — раздели его на отдельные ходы.")
        elif labels and sides.get(labels[0]) != m.speaker:
            side = sides[labels[0]]
            issues.append(f"Ход {k} (сегменты {rng.start}–{rng.stop - 1}) произносит голос {labels[0]}, то есть "
                          f"{names[side]} (speaker = \"{side}\"), а не \"{m.speaker}\" — исправь speaker.")
    return issues[:6]


def fix_speakers(raw: list[LLMMove], segments: list[Segment], hints) -> list[LLMMove]:
    """Last resort after the retries: flip a move's speaker when voice AND face both
    confidently say it is the other player."""
    sides = hint_sides(raw, segments, hints)
    by_id = hints.by_id()
    for m in raw:
        votes = set()
        for i in _seg_range(m, len(segments)):
            h = by_id.get(i)
            if h and h.label and h.conf >= 0.9 and h.audio is not None and h.video is not None \
                    and abs(h.audio - 0.5) > 0.35 and abs(h.video - 0.5) > 0.3 \
                    and (h.audio > 0.5) == (h.video > 0.5) == (h.label == "A"):
                votes.add(sides[h.label])
        if len(votes) == 1 and (side := votes.pop()) != m.speaker:
            log.warning("Ход «%s»: исправил сторону на %s — голос и лицо однозначно указывают на другого игрока", m.gist, "белых" if side == "white" else "чёрных")
            m.speaker = side
    return raw


def check_llm_moves(raw: list[LLMMove], segments: list[Segment], lo: int) -> list[str]:
    """Semantic checks the JSON schema cannot express. Returns human-readable issues."""
    issues = []
    n = len(segments)
    if len(raw) < lo:
        issues.append(f"Слишком мало ходов: {len(raw)}, нужно минимум {lo}. Разбей диалог подробнее.")
    covered: set[int] = set()
    for m in raw:
        a, b = sorted((m.first_segment, m.last_segment))
        covered.update(range(max(0, a), min(n - 1, b) + 1))
        for field in ("title", "explanation", "gist"):
            if PLACEHOLDER_RE.match(getattr(m, field).strip()):
                issues.append(f"Поле {field} содержит заглушку «{getattr(m, field)}» — напиши реальный текст.")
        head = re.split(r"\s+[—–-]\s+", m.title.strip(), maxsplit=1)[0].strip(" «»\"'—–-")
        if m.title.strip().startswith(("—", "–", "-")) or len(head.split()) < 2:
            issues.append(f"Заголовок «{m.title}» — только вердикт: сначала суть хода (2–6 слов: что сказал или сделал "
                          "игрок), затем через тире вердикт.")
    off = [f"{k} ({mv.move_type} при силе {mv.strength:+d})" for k, mv in enumerate(raw, 1)
           if mv.strength is not None and abs(mv.strength - TYPE_STRENGTH[mv.move_type]) >= 2]
    if off:
        issues.append("Тип хода не совпадает с его силой в ходах " + ", ".join(off[:6])
                      + ". Тип и оценка должны отражать реальный эффект хода по шкале силы.")
    brilliant = sum(mv.move_type == "Brilliant" for mv in raw)
    if brilliant > max(1, len(raw) // 5):
        issues.append(f"«Brilliant» стоит у {brilliant} ходов — оставь его только для одного-двух ключевых моментов.")
    if len(raw) >= 5:
        top = max(set(mv.move_type for mv in raw), key=lambda t: sum(mv.move_type == t for mv in raw))
        if sum(mv.move_type == top for mv in raw) / len(raw) > 0.7:
            issues.append(f"Почти все ходы — {top}. Оцени каждый ход по его реальному эффекту: у сильного игрока тоже бывают неточности.")
    found = sorted({m.group(0).lower() for mv in raw for f in ("title", "explanation") for m in CLICHES.finditer(getattr(mv, f))})
    if found:
        issues.append("Перепиши живым языком тексты, где есть канцелярит и штампы: " + ", ".join(f"«{w}»" for w in found[:6]) + ".")
    if n and len(covered) / n < 0.8:
        missing = sorted(set(range(n)) - covered)
        issues.append(f"Ходы не покрывают сегменты {missing[:20]} — диалог нужно разобрать целиком.")
    return issues


def move_issues(m: LLMMove, segments: list[Segment], hints=None, sides: dict | None = None,
                names: dict[str, str] | None = None) -> list[str]:
    """Problems of one move (the per-move part of check_llm_moves / check_speakers), in plain Russian."""
    out = []
    for field, ru in (("title", "заголовок"), ("explanation", "объяснение"), ("gist", "суть")):
        if PLACEHOLDER_RE.match((getattr(m, field) or "").strip()):
            out.append(f"Вместо текста заглушка в поле «{ru}»: «{getattr(m, field)}».")
    head = re.split(r"\s+[—–-]\s+", m.title.strip(), maxsplit=1)[0].strip(" «»\"'—–-")
    if m.title.strip().startswith(("—", "–", "-")) or len(head.split()) < 2:
        out.append("Заголовок — только вердикт: нужна суть хода (2–6 слов), затем через тире вердикт.")
    if m.strength is not None and abs(m.strength - TYPE_STRENGTH[m.move_type]) >= 2:
        out.append(f"Тип «{m.move_type}» не совпадает с силой хода {m.strength:+d}.")
    found = sorted({x.group(0).lower() for f in ("title", "explanation") for x in CLICHES.finditer(getattr(m, f))})
    if found:
        out.append("Штампы и канцелярит: " + ", ".join(f"«{w}»" for w in found[:4]) + ".")
    if hints is not None and segments:
        conf = {h.id: h for h in hints.confident(HINT_TRUST)}
        labels = sorted({conf[i].label for i in _seg_range(m, len(segments)) if i in conf})
        if len(labels) > 1:
            out.append(f"Объединяет реплики разных людей (голоса {' и '.join(labels)}).")
        elif labels and sides and sides.get(labels[0]) and sides[labels[0]] != m.speaker:
            who = (names or {}).get(sides[labels[0]], sides[labels[0]])
            out.append(f"Говорит голос {labels[0]} — это {who}, а ход отдан другой стороне.")
    return out


def review_issues(raw: list[LLMMove], segments: list[Segment], lo: int, hints=None,
                  names: dict[str, str] | None = None) -> dict:
    """{"general": [...], "moves": {index: [...]}} — what the recovery window shows for one answer."""
    sides = hint_sides(raw, segments, hints) if hints is not None else {}
    per = {k: iss for k, m in enumerate(raw) if (iss := move_issues(m, segments, hints, sides, names))}
    general = []
    if len(raw) < lo:
        general.append(f"Слишком мало ходов: {len(raw)}, нужно минимум {lo}.")
    brilliant = sum(mv.move_type == "Brilliant" for mv in raw)
    if brilliant > max(1, len(raw) // 5):
        general.append(f"«Brilliant» стоит у {brilliant} ходов — это для одного-двух ключевых моментов.")
    if len(raw) >= 5:
        top = max(set(mv.move_type for mv in raw), key=lambda t: sum(mv.move_type == t for mv in raw))
        if sum(mv.move_type == top for mv in raw) / len(raw) > 0.7:
            general.append(f"Почти все ходы — {top}: оценки однообразные.")
    n = len(segments)
    covered = {i for m in raw for i in _seg_range(m, n)} if n else set()
    if n and len(covered) / n < 0.8:
        general.append(f"Ходы не покрывают реплики {', '.join(str(i) for i in sorted(set(range(n)) - covered)[:12])}.")
    return {"general": general, "moves": per}


# minimum eval swing in favour of the mover (negative = against the mover)
EVAL_MIN_SWING = {"Brilliant": 3.0, "Great": 2.0, "Best": 1.2, "Inaccuracy": -1.2, "Mistake": -2.5, "Blunder": -4.0}


def shape_evals(moves: list[Move], gain: float = config.EVAL_GAIN) -> list[Move]:
    """Turn the LLM's eval numbers into a lively but consistent trajectory.

    * direction: a good move pushes the eval towards its author, a mistake away from
      them (move_type is the LLM's real verdict; a contradicting number is repaired);
    * strength: the LLM's own swing is multiplied by `gain` and never smaller than
      EVAL_MIN_SWING for that move type — small models tend to move by ±0.5.
    """
    prev_raw = prev = 0.0
    for m in moves:
        raw, sign, step = m.eval_score, (1 if m.speaker == "white" else -1), EVAL_MIN_SWING[m.move_type]
        swing = (raw - prev_raw) * sign  # LLM's swing from the mover's point of view
        if swing * step < 0:
            # models often score from the mover's perspective: try the flipped sign first
            flipped = (-raw - prev_raw) * sign
            swing = flipped if flipped * step > 0 and abs(flipped) <= 2 * abs(step) + 2 else 0.0
        swing = max(abs(swing) * gain, abs(step)) * (1 if step > 0 else -1)
        m.eval_score = max(-10.0, min(10.0, round(prev + sign * swing, 1)))
        log.debug("eval '%s' %s: llm %+.1f -> %+.1f", m.move_number, m.move_type, raw, m.eval_score)
        prev_raw, prev = raw, m.eval_score
    return moves


def llm_to_moves(raw: list[LLMMove], segments: list[Segment], names: dict[str, str], duration: float | None) -> list[Move]:
    n = len(segments)
    moves = []
    for m in raw:
        a, b = sorted((min(max(m.first_segment, 0), n - 1), min(max(m.last_segment, 0), n - 1)))
        name = names[m.speaker]
        gist = m.gist.strip().rstrip(".")
        if gist.lower().startswith(name.lower()):  # "Гус · Гус скрывает имя" -> "Гус · Скрывает имя"
            gist = gist[len(name):].lstrip(" :—-,")
            gist = gist[:1].upper() + gist[1:]
        moves.append(
            Move(
                start=segments[a].start,
                end=segments[b].end,
                move_number=f"{name} · {gist or m.title}",
                **m.model_dump(include={"speaker", "move_type", "title", "explanation", "eval_score"}),
            )
        )
    return shape_evals(sanitize_moves(moves, duration))  # sanitize also numbers the moves


# ================================================================ prompts
SYSTEM_PROMPT = """Ты — лучший комментатор Chess.com, только разбираешь не партии, а словесные дуэли: сцены из фильмов, сериалов, интервью и дебатов. Ты видишь подтекст: кто давит, кто блефует, кто расставляет ловушку, а кто в неё уже шагнул. Пишешь коротко, точно и с ядом — так, что зритель TikTok досматривает до конца.

Игроки:
- Белые, speaker = "white": {white}
- Чёрные, speaker = "black": {black}

Тебе дают расшифровку диалога, разбитую на пронумерованные сегменты с таймкодами. Кто говорит — не размечено: определи по смыслу (реплики собеседников обычно чередуются, вопрос и ответ — разные люди).

Задача: разбей ВЕСЬ диалог на {lo}–{hi} «ходов». Ход — это атака, защита, уклонение, ловушка или контратака: одна реплика или несколько подряд идущих сегментов ОДНОГО игрока.

Что вскрывать в каждом ходе (выбери главное, не всё сразу):
- борьбу за доминацию: кто задаёт рамку разговора, кто вынужден оправдываться, кто перехватил инициативу;
- манипуляции и риторические ловушки: вопрос с подвохом, ложный выбор, перевод стрелок, давление виной, газлайтинг, апелляция к статусу, угроза под видом заботы;
- сарказм, блеф, показную слабость, многозначительную паузу, удар по больному месту;
- цену хода: что игрок выиграл или чем рискнул, какую слабость открыл.

Шахматные образы — точно и к месту, не в каждой карточке: вилка, связка, жертва, гамбит, промежуточный ход, размен, вскрытое нападение, цугцванг, мат. Метафора должна объяснять механику хода, а не украшать текст.

Поля каждого хода:
- first_segment, last_segment — номера первого и последнего сегмента хода. Ходы идут по порядку, не пересекаются и вместе покрывают все сегменты.
- speaker — "white" или "black".
- effect — что изменилось в позиции после хода, 3–12 слов: кто перехватил инициативу, кто вынужден защищаться, что вскрылось.
- strength — сила хода по шкале ниже, целое число от -3 до +3.
- move_type — тип по силе: +3 "Brilliant" (!!), +2 "Great" (!), +1 "Best" (★), -1 "Inaccuracy" (?!), -2 "Mistake" (?), -3 "Blunder" (??). Тип обязан совпадать с силой.
- eval_score — общий баланс власти в сцене ПОСЛЕ хода, число от -10.0 (полная доминация чёрных) до +10.0 (полная доминация белых). Оценка накапливается от хода к ходу: сильный ход сдвигает её в пользу автора, слабый — против него. Сдвиг по силе: ±3 → на 3–6, ±2 → на 2–3, ±1 → на 1–2.

Шкала силы — по ЭФФЕКТУ в диалоге, а не по громкости фразы:
+3 Brilliant — ход ломает рамку разговора или ставит соперника туда, где любой ответ проигрывает; неожиданно и точно. Обычно один на сцену.
+2 Great — перехват инициативы, вскрытое противоречие, соперник вынужден уступить или оправдываться.
+1 Best — точный ожидаемый ответ: держит позицию, но баланс не меняет.
-1 Inaccuracy — оправдание, уход от вопроса, лишняя эмоция: инициатива слабеет.
-2 Mistake — игрок сам открывает слабость, проговаривается, даёт сопернику козырь.
-3 Blunder — теряет лицо или самообладание, признаёт поражение, его же аргумент разворачивают против него.
Крик, угроза или пафос — не сильный ход, если соперник не прогнулся. Тихая точная реплика может оказаться сильнейшей. У доминирующего игрока тоже бывают неточности, а у проигрывающего — удачные огрызания: оценивай каждый ход отдельно.
Порядок мысли: сначала effect и strength, затем move_type и eval_score, и только потом тексты.
- gist — суть хода в 2–5 словах (для подзаголовка), своими словами про этот ход.
- title — заголовок карточки до 60 символов: крючок с вердиктом, который хочется дочитать. Вердикт формулируй по-разному («— и это мат», «— зевок ферзя», «— красивая жертва»), а не одним шаблоном.
- explanation — 1–2 предложения до 170 символов: что на самом деле происходит под словами и почему это работает или проваливается. Цитату из реплики бери в «ёлочки», если она бьёт точнее пересказа.

Как писать:
- живой разговорный русский, глаголы вместо отглагольных существительных, короткие фразы, конкретика этой сцены;
- называй игроков по именам и пиши, что они ДЕЛАЮТ друг с другом, а не что «утверждают»;
- не пересказывай реплику — объясняй её ход и последствия;
- без канцелярита («является», «данный», «осуществляет», «в рамках», «следует отметить», «демонстрирует», «тем самым», «что делает его»), без штампов («сильное заявление», «очень сильный ход», «ставит под сомнение»), без интернет-сленга и кринжа («база», «жиза», «рофл»), без эмодзи и восклицательных знаков подряд.

Примеры стиля — из ДРУГОЙ, выдуманной сцены (начальник и опоздавший сотрудник). Не копируй их слова, только манеру:
Плохо: «Начальник утверждает, что сотрудник опоздал, что делает его заявление серьёзным.»
Хорошо: «Шеф не спорит — молча кладёт на стол распечатку пропусков. Оправдания кончились раньше, чем начались.»
Плохо: «Это сильный ход, который демонстрирует уверенность.»
Хорошо: «Ответить вопросом на вопрос — и вот уже шеф объясняет, зачем вообще вызвал. Инициатива сменила владельца.»

Все тексты — на {language} языке. Ответ — только JSON-объект вида {{"moves": [ ... ]}} без markdown.{speakers}{context}"""

# clichés the prompt bans; check_llm_moves sends them back for a rewrite
CLICHES = re.compile(
    r"\b(явля(ется|ются|лся|лась)|данн(ый|ая|ое|ые|ого|ой)|осуществля\w*|в рамках|следует отметить|"
    r"демонстриру\w*|тем самым|что делает (его|её|их)|сильное заявление|очень сильный ход|"
    r"ставит под сомнение|база\b|жиза|рофл)",
    re.I,
)

SPEAKER_NOTE = """

Подсказки «кто говорит». У сегментов есть метка голоса из аудио-визуального анализа (тембр голоса + движение губ в кадре) и уверенность в процентах, «голос ?» — данных нет. Сегменты с одинаковой меткой произносит один и тот же человек. {mapping} Подсказкам с уверенностью от {trust}% доверяй: один ход не объединяет сегменты разных меток, а speaker хода соответствует его метке."""
HINT_TRUST = 0.8  # hints at least this confident are enforced by check_speakers


def extra_attempt(segments: list[Segment], white: str, black: str, context: str, provider, hints, previous: dict,
                  n: int) -> dict:
    """One more answer for the recovery window: the model sees its previous answer and the problems."""
    lo, hi = target_moves(segments)
    names = {"white": white, "black": black}
    base = build_messages(segments, white, black, context, lo, hi, hints)
    messages = base + [
        {"role": "assistant", "content": (previous.get("reply") or "")[:6000]},
        {"role": "user", "content": "Ответ не прошёл проверку:\n- " + "\n- ".join(previous.get("issues") or ["нужен другой вариант"])
         + "\nИсправь и верни ТОЛЬКО корректный JSON {\"moves\": [...]}."},
    ]
    log.info("Запрос ещё одного варианта у %s…", provider.label)
    reply = provider.chat(messages, llm_json_schema(lo, hi))
    try:
        raw = LLMAnalysis.model_validate(_as_moves_dict(extract_json(reply))).moves
        issues = check_llm_moves(raw, segments, lo) + (check_speakers(raw, segments, hints, names) if hints is not None else [])
    except (ValidationError, json.JSONDecodeError, ValueError) as e:
        raw, issues = None, [f"Невалидный JSON: {str(e)[:1200]}"]
    return {"n": n, "model": getattr(provider, "label", ""), "issues": issues, "reply": reply[:20000],
            "moves": [m.model_dump() for m in raw] if raw is not None else None}


def _hint_tag(h, names: dict[str, str], sides: dict[str, str | None]) -> str:
    if h is None or not h.label or h.conf < config.SPEAKER_HINT_MIN_CONF:
        return "голос ?"
    pct = f"{min(99, round(h.conf * 100))}%"
    side = sides.get(h.label)
    if side:
        return f"голос {h.label} = {'Белые' if side == 'white' else 'Чёрные'}, {names[side]} ({pct})"
    return f"голос {h.label} ({pct})"


def build_messages(
    segments: list[Segment], white: str, black: str, context: str = "", lo: int = 2, hi: int = 12, hints=None
) -> list[dict]:
    ctx = f"\n\nКонтекст сцены: {context.strip()}" if context.strip() else ""
    names = {"white": white, "black": black}
    note = ""
    if hints is not None and hints.confident():
        sides = hints.sides
        if all(sides.get(lab) for lab in ("A", "B")):
            mapping = (f"Голос A — {names[sides['A']]} (speaker = \"{sides['A']}\"), "
                       f"голос B — {names[sides['B']]} (speaker = \"{sides['B']}\"): это установлено по лицам.")
        else:
            mapping = (f"Метки анонимны: по смыслу реши, какой голос — Белые ({white}), а какой — Чёрные ({black}), "
                       "и придерживайся этого во всех ходах.")
        note = SPEAKER_NOTE.format(mapping=mapping, trust=round(HINT_TRUST * 100))
    system = SYSTEM_PROMPT.format(
        white=white, black=black, language=config.LLM_LANGUAGE, context=ctx, lo=lo, hi=hi, speakers=note
    )
    by_id = hints.by_id() if note else {}
    lines = "\n".join(
        f"[{s.id}] {s.start:.1f}–{s.end:.1f}с" + (f" | {_hint_tag(by_id.get(s.id), names, hints.sides)}" if note else "")
        + f": {s.text}"
        for s in segments
    )
    user = f"Сегменты диалога (0–{len(segments) - 1}):\n{lines}\n\nВерни JSON с {lo}–{hi} ходами."
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ================================================================ LLM providers
# providers live in llm.py; these names stay importable from here
get_provider = llm.get_provider
list_models = llm.list_models
default_model = llm.default_model


def analyze(
    segments: list[Segment],
    white: str,
    black: str,
    context: str = "",
    duration: float | None = None,
    provider=None,
    hints=None,
    review: bool = False,
) -> list[Move]:
    """Ask the LLM for moves; on invalid JSON or failed semantic checks, retry with
    the errors fed back. If retries run out, the best valid attempt is used — or, with
    `review`, NeedsReview carries every answer so the person can choose and edit.
    `hints` (speakers.SpeakerHints) adds per-segment "who speaks" labels."""
    if not segments:
        raise AnalysisError("Transcript is empty — nothing to analyze")
    provider = provider or get_provider()
    names = {"white": white, "black": black}
    lo, hi = target_moves(segments)
    schema = llm_json_schema(lo, hi)
    base = build_messages(segments, white, black, context, lo, hi, hints)
    messages = base
    best: tuple[int, list[LLMMove]] | None = None
    last_err = ""
    attempts: list[dict] = []
    for attempt in range(1, config.LLM_MAX_RETRIES + 1):
        log.info("Запрос к %s (попытка %d из %d), нужно %d–%d ходов…", provider.label, attempt, config.LLM_MAX_RETRIES, lo, hi)
        reply = provider.chat(messages, schema)
        try:
            raw = LLMAnalysis.model_validate(_as_moves_dict(extract_json(reply))).moves
            issues = check_llm_moves(raw, segments, lo)
            if hints is not None:
                issues += check_speakers(raw, segments, hints, names)
        except (ValidationError, json.JSONDecodeError, ValueError) as e:
            raw, issues = None, [f"Невалидный JSON: {str(e)[:1200]}"]
        attempts.append({"n": attempt, "model": getattr(provider, "label", ""), "issues": issues, "reply": reply[:20000],
                         "moves": [m.model_dump() for m in raw] if raw is not None else None})
        if raw is not None and (best is None or len(issues) < best[0]):
            best = (len(issues), raw)
        if not issues:
            break
        last_err = " ".join(issues)
        log.warning("Ответ нейросети не прошёл проверку (%d замечани%s) — прошу исправить", len(issues), "е" if len(issues) == 1 else "й")
        log.debug("issues: %s", last_err[:600])
        # feed the problems back so the model can fix its own output
        messages = base + [
            {"role": "assistant", "content": reply[:6000]},
            {"role": "user", "content": "Ответ не прошёл проверку:\n- " + "\n- ".join(issues)
             + "\nИсправь и верни ТОЛЬКО корректный JSON {\"moves\": [...]}."},
        ]
    if best is None:
        raise AnalysisError(f"LLM failed to return valid JSON after {config.LLM_MAX_RETRIES} attempts: {last_err}")
    if best[0] and review:
        log.warning("Ответы не прошли проверку (замечаний в лучшем: %d) — выберите варианты в окне", best[0])
        raise NeedsReview(attempts, lo, hi)
    if best[0]:
        log.warning("Беру лучший из ответов (осталось замечаний: %d)", best[0])
    raw = fix_speakers(best[1], segments, hints) if hints is not None else best[1]
    return llm_to_moves(raw, segments, names, duration)
