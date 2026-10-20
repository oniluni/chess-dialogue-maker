"""Manual editing of a job's moves (moves.json): validation, insert / update / delete,
reordering and a one-time backup of the original (usually automatic) analysis.

Moves are time-ordered: a card appears when its move is played, so "reordering" a
move means giving it its neighbour's time slot (the two swap places on the timeline).
"""
from __future__ import annotations

import math
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import config
from analyzer import Move, sanitize_moves
from ingest import parse_time

MIN_LEN = 0.3          # s, shortest move
TITLE_SOFT_MAX = 60    # longer titles shrink the card font
EXPLANATION_SOFT_MAX = 190
BACKUP = "moves.orig.json"
TYPE_CHOICES = [(f"{s.glyph}  {s.label_ru}", s.key) for s in config.MOVE_TYPES.values()]


def fmt_ts(t: float | None) -> str:
    """83.25 -> '1:23.3' (editor-style timecode with tenths)."""
    if t is None:
        return ""
    t = max(0.0, float(t))
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:04.1f}" if h else f"{int(m)}:{s:04.1f}"


def parse_ts(text: str | float | None) -> float | None:
    if text is None or (isinstance(text, str) and not text.strip()):
        return None
    return parse_time(text if isinstance(text, (int, float)) else text.strip())


def parse_field(label: str, value) -> tuple[float | None, str | None]:
    """(seconds, error) for a timecode field of the editors."""
    try:
        return parse_ts(value), None
    except ValueError:
        return None, f"{label}: «{value}» — не похоже на время. Пишите 1:23.5, 83.5 или 0:01:23."


def split_number(move_number: str) -> tuple[str, str]:
    """'3 Уолтер · Давит статусом' -> ('Уолтер', 'Давит статусом')."""
    text = re.sub(r"^\s*\d+[.)]?\s*", "", move_number or "").strip()
    name, sep, gist = text.partition(" · ")
    return (name.strip(), gist.strip()) if sep else (text, "")


def join_number(name: str, gist: str) -> str:
    gist = (gist or "").strip()
    return f"{name} · {gist}" if gist else name


@dataclass
class MoveForm:
    start: str
    end: str
    speaker: str
    move_type: str
    eval_score: float
    title: str
    gist: str
    explanation: str


def form_of(move: Move) -> MoveForm:
    return MoveForm(fmt_ts(move.start), fmt_ts(move.end), move.speaker, move.move_type, float(move.eval_score),
                    move.title, split_number(move.move_number)[1], move.explanation)


TOLERANCE = 0.05       # s: timecodes are shown with tenths, touching moves are fine


def overlapping(moves: list[Move], start: float, end: float, skip: int | None) -> list[int]:
    return [i for i, m in enumerate(moves) if i != skip and start < m.end - TOLERANCE and end > m.start + TOLERANCE]


def validate(form: MoveForm, moves: list[Move], index: int | None, names: dict[str, str],
             duration: float | None) -> tuple[Move | None, list[str], list[str]]:
    """(move, errors, warnings). `index` is the edited move (None = a new one)."""
    errors: list[str] = []
    warnings: list[str] = []
    start, e1 = parse_field("Начало", form.start)
    end, e2 = parse_field("Конец", form.end)
    if e1 or e2:
        return None, [e for e in (e1, e2) if e], []
    if start is None or end is None:
        errors.append("Укажите начало и конец хода (кнопка ⏱ берёт время из плеера).")
    else:
        if end - start < MIN_LEN:
            errors.append(f"Ход должен длиться хотя бы {MIN_LEN:g} с: конец позже начала.")
        if duration and start >= duration:
            errors.append(f"Начало ({fmt_ts(start)}) дальше конца клипа ({fmt_ts(duration)}).")
        elif duration and end > duration + 0.05:
            warnings.append(f"Конец обрезан до конца клипа ({fmt_ts(duration)}).")
            end = duration
        clash = overlapping(moves, start, end, index)
        if clash:
            k = clash[0]
            errors.append(f"Пересекается с ходом {k + 1} ({fmt_ts(moves[k].start)}–{fmt_ts(moves[k].end)}): "
                          "ходы не должны накладываться — поправьте время.")
    if form.speaker not in ("white", "black"):
        errors.append("Выберите, кто делает ход.")
    if form.move_type not in config.MOVE_TYPES:
        errors.append("Выберите тип хода.")
    title = re.sub(r"\s+", " ", form.title or "").strip()
    explanation = re.sub(r"\s+", " ", form.explanation or "").strip()
    if not title:
        errors.append("Заголовок карточки пустой.")
    elif len(title) > TITLE_SOFT_MAX:
        warnings.append(f"Заголовок длиннее {TITLE_SOFT_MAX} символов — шрифт карточки станет мельче.")
    if not explanation:
        errors.append("Объяснение хода пустое.")
    elif len(explanation) > EXPLANATION_SOFT_MAX:
        warnings.append(f"Объяснение длиннее {EXPLANATION_SOFT_MAX} символов — пауза станет длиннее, текст мельче.")
    if errors:
        return None, errors, warnings
    name = names.get(form.speaker) or ("Белые" if form.speaker == "white" else "Чёрные")
    move = Move(start=start, end=end, speaker=form.speaker, move_type=form.move_type, title=title,
                explanation=explanation, eval_score=float(form.eval_score or 0), move_number=join_number(name, form.gist))
    return move, errors, warnings


def _order(moves: list[Move], duration: float | None) -> list[Move]:
    return sanitize_moves([m.model_copy() for m in moves], duration)


def upsert(moves: list[Move], index: int | None, move: Move, duration: float | None) -> tuple[list[Move], int]:
    """Insert (index None) or replace a move; returns the sorted list and the move's new index."""
    items = list(moves)
    if index is None or not (0 <= index < len(items)):
        items.append(move)
    else:
        items[index] = move
    ordered = _order(items, duration)
    new_index = min(range(len(ordered)), key=lambda i: abs(ordered[i].start - move.start))
    return ordered, new_index


def delete(moves: list[Move], index: int, duration: float | None) -> list[Move]:
    return _order([m for i, m in enumerate(moves) if i != index], duration)


def swap(moves: list[Move], index: int, step: int, duration: float | None) -> tuple[list[Move], int]:
    """Move a move one place earlier (step -1) or later (+1): the two exchange time slots."""
    j = index + step
    if not (0 <= index < len(moves) and 0 <= j < len(moves)):
        return list(moves), index
    a, b = moves[index].model_copy(), moves[j].model_copy()
    a.start, a.end, b.start, b.end = moves[j].start, moves[j].end, moves[index].start, moves[index].end
    items = list(moves)
    items[index], items[j] = b, a
    return _order(items, duration), j


def default_form(moves: list[Move], t: float, duration: float | None) -> MoveForm:
    """A sensible new move at playhead time `t`: up to 3 s, ending before the next move."""
    t = max(0.0, min(t, (duration or t + 3) - MIN_LEN))
    for m in moves:  # inside an existing move: start right after it
        if m.start <= t < m.end:
            t = m.end
    nxt = min((m.start for m in moves if m.start > t), default=duration or t + 3)
    end = math.floor(min(t + 3.0, nxt, duration or t + 3.0) * 10) / 10   # never into the next move
    t = math.ceil(t * 10 - 1e-6) / 10
    prev = [m for m in moves if m.end <= t + 1e-3]
    last = prev[-1] if prev else None
    speaker = ("black" if last.speaker == "white" else "white") if last else "white"
    return MoveForm(fmt_ts(t), fmt_ts(max(end, t + MIN_LEN)), speaker, "Best", float(last.eval_score) if last else 0.0,
                    "", "", "")


# ---------------------------------------------------------------- backup of the original analysis
def ensure_backup(job_dir: Path) -> None:
    src, dst = job_dir / "moves.json", job_dir / BACKUP
    if src.exists() and not dst.exists():
        shutil.copy(src, dst)


def has_backup(job_dir: Path) -> bool:
    return (job_dir / BACKUP).exists()


def restore_backup(job_dir: Path) -> bool:
    src = job_dir / BACKUP
    if not src.exists():
        return False
    shutil.copy(src, job_dir / "moves.json")
    return True
