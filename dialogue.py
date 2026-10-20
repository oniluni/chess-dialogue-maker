"""Manual dialogue analysis: lines (replicas), their ratings and the key moments that
become moves. Stored per job in `dialogue.json`, independent of the automatic analysis.

    lines    — who says what and when, plus the person's own judgement of it:
               key line ★, strength −3…+3, accuracy / persuasiveness 1…5, tactics
               (sarcasm, bluff, …), a chess motif, the position after it, a note
    moments  — groups of lines that form one move of the review (title, subtitle,
               explanation; type / eval / speaker are derived from the lines unless set)

`build_moves` turns the moments into analyzer.Move objects (moves.json). Lines can be
imported from the whisper transcript (speaker labels from speakers.json) and from an
existing automatic analysis, or typed in from scratch.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

import config
from analyzer import EVAL_MIN_SWING, Move, sanitize_moves
from moves_edit import fmt_ts, join_number, split_number

FILE = "dialogue.json"
VERSION = 1
TACTICS = ["манипуляция", "сарказм", "блеф", "ловушка", "перевод стрелок", "давление", "угроза", "уклонение",
           "лесть", "газлайтинг", "апелляция к статусу", "контратака", "признание", "провокация"]
MOTIFS = ["вилка", "связка", "жертва", "гамбит", "промежуточный ход", "размен", "вскрытое нападение", "двойной удар",
          "цугцванг", "мат", "защита", "отступление", "блокада", "прорыв", "контригра"]
MIN_LEN = 0.2


def new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


@dataclass
class Line:
    id: str
    start: float
    end: float
    speaker: str | None = None        # "white" | "black" | None
    text: str = ""
    key: bool = False
    strength: int | None = None       # −3 … +3
    accuracy: int | None = None       # 1 … 5
    persuasion: int | None = None     # 1 … 5
    tactics: list[str] = field(default_factory=list)
    motif: str = ""
    eval: float | None = None         # position after the line, −10 … +10
    note: str = ""
    moment: str | None = None


@dataclass
class Moment:
    id: str
    title: str = ""
    gist: str = ""
    explanation: str = ""
    move_type: str | None = None      # None = from the lines' strength
    eval: float | None = None         # None = from the lines / automatic
    speaker: str | None = None        # None = most lines' speaker


@dataclass
class Dialogue:
    lines: list[Line] = field(default_factory=list)
    moments: list[Moment] = field(default_factory=list)
    origin: str = "manual"            # manual | transcript | moves
    updated: float = 0.0
    version: int = VERSION

    # ------------------------------------------------------------ persistence
    @classmethod
    def load(cls, job_dir: Path) -> "Dialogue":
        path = job_dir / FILE
        if not path.exists():
            return cls()
        data = json.loads(path.read_text())
        known_l = set(Line.__dataclass_fields__)
        known_m = set(Moment.__dataclass_fields__)
        return cls(
            lines=[Line(**{k: v for k, v in d.items() if k in known_l}) for d in data.get("lines", [])],
            moments=[Moment(**{k: v for k, v in d.items() if k in known_m}) for d in data.get("moments", [])],
            origin=data.get("origin", "manual"), updated=data.get("updated", 0.0), version=data.get("version", VERSION),
        )

    def save(self, job_dir: Path) -> None:
        self.updated = time.time()
        self.lines.sort(key=lambda ln: (ln.start, ln.end))
        tmp = job_dir / f"{FILE}.tmp"
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=1))
        tmp.replace(job_dir / FILE)

    @staticmethod
    def exists(job_dir: Path) -> bool:
        return (job_dir / FILE).exists()

    # ------------------------------------------------------------ lookups
    def line(self, line_id: str | None) -> Line | None:
        return next((ln for ln in self.lines if ln.id == line_id), None)

    def moment(self, moment_id: str | None) -> Moment | None:
        return next((m for m in self.moments if m.id == moment_id), None)

    def moment_lines(self, moment_id: str) -> list[Line]:
        return sorted((ln for ln in self.lines if ln.moment == moment_id), key=lambda ln: ln.start)

    def ordered_moments(self) -> list[Moment]:
        """Moments in time order (by their first line); moments without lines last."""
        def first(m: Moment) -> float:
            ls = self.moment_lines(m.id)
            return ls[0].start if ls else 1e12
        return sorted(self.moments, key=first)

    def number_of(self, moment_id: str) -> int:
        return next((i for i, m in enumerate(self.ordered_moments(), 1) if m.id == moment_id), 0)


# ================================================================ import
def from_transcript(segments, hints=None, moves: list[Move] | None = None) -> tuple[Dialogue, list[str]]:
    """Lines from whisper segments; speakers from the audio-visual hints (sides follow
    the existing analysis if there is one); key moments from existing moves."""
    import speakers as spk

    notes: list[str] = []
    sides: dict[str, str] = {}
    by_id = {}
    if hints is not None and hints.segments:
        by_id = hints.by_id()
        sides = spk.label_sides(hints, moves) if moves else {k: v for k, v in hints.sides.items() if v}
        if not sides:
            sides = {"A": "white", "B": "black"}
            notes.append("стороны назначены условно (голос A — белые, B — чёрные): проверьте говорящих")
    d = Dialogue(origin="transcript")
    for seg in segments:
        h = by_id.get(seg.id)
        side = sides.get(h.label) if h is not None and h.label and h.conf >= config.SPEAKER_HINT_MIN_CONF else None
        d.lines.append(Line(id=new_id("l"), start=round(seg.start, 2), end=round(seg.end, 2), speaker=side, text=seg.text.strip()))
    if moves:
        _attach_moves(d, moves)
    if not any(ln.speaker for ln in d.lines):
        notes.append("говорящие не определены — укажите их у реплик")
    return d, notes


def from_moves(moves: list[Move]) -> Dialogue:
    """No transcript: one line per move, text to be typed in."""
    d = Dialogue(origin="moves")
    for m in moves:
        d.lines.append(Line(id=new_id("l"), start=m.start, end=m.end, speaker=m.speaker, text=""))
    _attach_moves(d, moves)
    return d


def _attach_moves(d: Dialogue, moves: list[Move]) -> None:
    from analyzer import TYPE_STRENGTH

    for mv in moves:
        mo = Moment(id=new_id("m"), title=mv.title, gist=split_number(mv.move_number)[1], explanation=mv.explanation,
                    move_type=mv.move_type, eval=float(mv.eval_score), speaker=mv.speaker)
        d.moments.append(mo)
        for ln in d.lines:
            overlap = min(ln.end, mv.end) - max(ln.start, mv.start)
            if ln.moment is None and overlap >= 0.5 * max(ln.end - ln.start, 1e-3):
                ln.moment, ln.key = mo.id, True
                ln.strength = TYPE_STRENGTH.get(mv.move_type)
                ln.speaker = ln.speaker or mv.speaker


# ================================================================ editing
def validate_line(start: float | None, end: float | None, text: str, duration: float | None) -> list[str]:
    errors = []
    if start is None or end is None:
        errors.append("Укажите начало и конец реплики (⏱ берёт время из плеера).")
    elif end - start < MIN_LEN:
        errors.append("Конец реплики должен быть позже начала.")
    elif duration and start >= duration:
        errors.append(f"Начало ({fmt_ts(start)}) дальше конца клипа ({fmt_ts(duration)}).")
    if not (text or "").strip():
        errors.append("Текст реплики пустой — впишите, что сказано (хотя бы кратко).")
    return errors


def new_line(d: Dialogue, t: float, duration: float | None) -> Line:
    """A new line at playhead `t`, 2 s long, spoken by the other person than the previous line."""
    t = max(0.0, t)
    end = min(t + 2.0, duration or t + 2.0)
    prev = [ln for ln in d.lines if ln.start <= t]
    last = max(prev, key=lambda ln: ln.start) if prev else None
    speaker = None
    if last and last.speaker:
        speaker = "black" if last.speaker == "white" else "white"
    return Line(id=new_id("l"), start=round(t, 2), end=round(max(end, t + MIN_LEN), 2), speaker=speaker)


def split_line(d: Dialogue, line_id: str, t: float) -> Line | None:
    """Split a line at time `t`; the text is divided at the word nearest to the same proportion."""
    ln = d.line(line_id)
    if ln is None or not (ln.start + MIN_LEN <= t <= ln.end - MIN_LEN):
        return None
    words = ln.text.split()
    k = round(len(words) * (t - ln.start) / max(ln.end - ln.start, 1e-3))
    k = max(1, min(len(words) - 1, k)) if len(words) > 1 else len(words)
    second = Line(id=new_id("l"), start=round(t, 2), end=ln.end, speaker=ln.speaker, text=" ".join(words[k:]),
                  moment=ln.moment, key=ln.key)
    ln.end, ln.text = round(t, 2), " ".join(words[:k])
    d.lines.append(second)
    d.lines.sort(key=lambda x: x.start)
    return second


def merge_next(d: Dialogue, line_id: str) -> bool:
    lines = sorted(d.lines, key=lambda x: x.start)
    i = next((k for k, x in enumerate(lines) if x.id == line_id), None)
    if i is None or i + 1 >= len(lines):
        return False
    a, b = lines[i], lines[i + 1]
    a.end = max(a.end, b.end)
    a.text = f"{a.text} {b.text}".strip()
    a.key = a.key or b.key
    a.moment = a.moment or b.moment
    a.note = " ".join(x for x in (a.note, b.note) if x)
    a.tactics = list(dict.fromkeys(a.tactics + b.tactics))
    d.lines = [x for x in d.lines if x.id != b.id]
    return True


def delete_line(d: Dialogue, line_id: str) -> None:
    d.lines = [x for x in d.lines if x.id != line_id]


def drop_empty_moments(d: Dialogue) -> int:
    used = {ln.moment for ln in d.lines if ln.moment}
    before = len(d.moments)
    d.moments = [m for m in d.moments if m.id in used]
    return before - len(d.moments)


def shift(d: Dialogue, delta: float) -> int:
    """Move every line by `delta` seconds (a new cut of the clip); drops lines before 0."""
    kept = []
    for ln in d.lines:
        ln.start, ln.end = round(ln.start + delta, 2), round(ln.end + delta, 2)
        if ln.end > MIN_LEN:
            ln.start = max(0.0, ln.start)
            kept.append(ln)
    dropped = len(d.lines) - len(kept)
    d.lines = kept
    return dropped


# ================================================================ building moves
def type_for_strength(s: float | None) -> str:
    if s is None:
        return "Best"
    if s >= 2.5:
        return "Brilliant"
    if s >= 1.5:
        return "Great"
    if s >= 0.5:
        return "Best"
    if s <= -2.5:
        return "Blunder"
    if s <= -1.5:
        return "Mistake"
    if s <= -0.5:
        return "Inaccuracy"
    return "Best"


@dataclass
class Planned:
    """A moment resolved into everything a move needs (for the structure view and the build)."""
    number: int
    moment: Moment
    lines: list[Line]
    start: float
    end: float
    speaker: str | None
    move_type: str
    eval_score: float
    title: str
    explanation: str
    auto: dict[str, bool]
    problems: list[str]


def plan(d: Dialogue) -> list[Planned]:
    out: list[Planned] = []
    prev_eval = 0.0
    for n, mo in enumerate(d.ordered_moments(), 1):
        lines = d.moment_lines(mo.id)
        problems = []
        auto = {"speaker": mo.speaker is None, "type": mo.move_type is None, "eval": mo.eval is None}
        if not lines:
            out.append(Planned(n, mo, [], 0, 0, mo.speaker, mo.move_type or "Best", mo.eval or prev_eval,
                               mo.title, mo.explanation, auto, ["в момент не входит ни одна реплика"]))
            continue
        speaker = mo.speaker
        if speaker is None:
            votes = [ln.speaker for ln in lines if ln.speaker]
            if votes:
                top = max(set(votes), key=votes.count)
                speaker = top if votes.count(top) > len(votes) / 2 else None
            if speaker is None:
                problems.append("не понятно, чей это ход — укажите говорящего у момента или реплик")
        strengths = [ln.strength for ln in lines if ln.strength is not None]
        move_type = mo.move_type or type_for_strength(sum(strengths) / len(strengths) if strengths else None)
        if mo.eval is not None:
            eval_score = float(mo.eval)
        elif any(ln.eval is not None for ln in lines):
            eval_score = float([ln.eval for ln in lines if ln.eval is not None][-1])
        else:
            sign = 1 if speaker == "white" else -1 if speaker == "black" else 0
            eval_score = max(-10.0, min(10.0, round(prev_eval + sign * EVAL_MIN_SWING[move_type] * config.EVAL_GAIN, 1)))
        explanation = mo.explanation.strip() or " ".join(ln.note.strip() for ln in lines if ln.note.strip())
        if not mo.title.strip():
            problems.append("нет заголовка карточки")
        if not explanation:
            problems.append("нет объяснения (напишите его у момента или в пояснениях к репликам)")
        out.append(Planned(n, mo, lines, lines[0].start, max(ln.end for ln in lines), speaker, move_type, eval_score,
                           mo.title.strip(), explanation, auto, problems))
        prev_eval = eval_score
    for a, b in zip(out, out[1:]):
        if a.lines and b.lines and b.start < a.end - 1e-3:
            b.problems.append(f"пересекается по времени с моментом {a.number} — ходы не могут накладываться")
    return out


def build_moves(d: Dialogue, names: dict[str, str], duration: float | None) -> tuple[list[Move], list[str], list[str]]:
    """(moves, errors, warnings) — moves only when there are no errors."""
    planned = plan(d)
    errors = [f"Момент {p.number} «{p.title or 'без названия'}»: {pr}" for p in planned for pr in p.problems]
    warnings = []
    loose = [ln for ln in d.lines if ln.key and not ln.moment]
    if loose:
        warnings.append(f"{len(loose)} ключевых реплик не входят ни в один момент — они не попадут в видео.")
    if not planned:
        errors.append("Нет ни одного ключевого момента: отметьте реплики и выберите для них «Ключевой момент».")
    if errors:
        return [], errors, warnings
    moves = []
    for p in planned:
        name = names.get(p.speaker or "white") or ""
        moves.append(Move(start=p.start, end=p.end, speaker=p.speaker, move_type=p.move_type, title=p.title,
                          explanation=p.explanation, eval_score=p.eval_score,
                          move_number=join_number(name, p.moment.gist or _gist_from_lines(p.lines))))
    return sanitize_moves(moves, duration), errors, warnings


def preview_moves(d: Dialogue, names: dict[str, str]) -> tuple[list[Move], dict[str, int]]:
    """Moves of the complete moments only (for card previews while the rest is unfinished),
    and moment id -> index in that list."""
    moves: list[Move] = []
    index: dict[str, int] = {}
    for p in plan(d):
        if p.problems or not p.speaker:
            continue
        index[p.moment.id] = len(moves)
        moves.append(Move(start=p.start, end=p.end, speaker=p.speaker, move_type=p.move_type, title=p.title,
                          explanation=p.explanation, eval_score=p.eval_score,
                          move_number=f"{len(moves) + 1} " + join_number(names.get(p.speaker, ""),
                                                                         p.moment.gist or _gist_from_lines(p.lines))))
    return moves, index


def _gist_from_lines(lines: list[Line]) -> str:
    motifs = [ln.motif for ln in lines if ln.motif]
    tactics = [t for ln in lines for t in ln.tactics]
    parts = list(dict.fromkeys(motifs[:1] + tactics[:2]))
    return " · ".join(parts)[:40]


def short(text: str, n: int = 70) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"
