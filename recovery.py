"""Recovery when the model's answers fail the checks (analyzer.NeedsReview).

Instead of silently taking the best attempt, every answer is kept in `recovery.json`
next to the job. One answer is the *base* (its moves give the structure: how the
dialogue is split into steps); for every step the other answers offer their version
of the same lines (the move that overlaps it most). The person picks a version per
step, may edit it (type, side, gist, title, explanation), and confirms: the moves are built
from the base split with the chosen contents and the pipeline goes on. Nothing done
before the analysis is repeated, and the choices survive closing the window, asking
the model for one more answer or picking another base.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import analyzer
import config

FILE = "recovery.json"
# what a chosen version brings into a step (the segment range always stays the base's)
CONTENT = ("speaker", "move_type", "title", "explanation", "eval_score", "gist", "effect", "strength")
EDITABLE = ("move_type", "speaker", "gist", "title", "explanation")


@dataclass
class Recovery:
    attempts: list[dict] = field(default_factory=list)   # analyzer attempts: n, model, issues, reply, moves
    lo: int = 1
    hi: int = 1
    base: int = 0                                         # index into attempts: whose split is used
    choices: dict[str, dict] = field(default_factory=dict)  # step -> {"alt": "attempt:move", "edit": {...}}
    status: str = "pending"                               # pending | resolved
    stage: str = "analysis"
    created: float = 0.0

    # ---------------------------------------------------------------- storage
    @staticmethod
    def path(job_dir: Path) -> Path:
        return Path(job_dir) / FILE

    @classmethod
    def load(cls, job_dir: Path) -> Recovery | None:
        p = cls.path(job_dir)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text())
        except ValueError:
            return None
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self, job_dir: Path) -> None:
        self.path(job_dir).write_text(json.dumps(asdict(self), ensure_ascii=False, indent=1))

    # ---------------------------------------------------------------- answers
    def valid(self) -> list[int]:
        """Attempts whose JSON could be read (the only ones that can be picked)."""
        return [i for i, a in enumerate(self.attempts) if a.get("moves")]

    def raw(self, i: int) -> list[analyzer.LLMMove]:
        return [analyzer.LLMMove.model_validate(m) for m in self.attempts[i]["moves"]]

    def attempt_label(self, i: int) -> str:
        a = self.attempts[i]
        return f"Вариант {a.get('n', i + 1)}"


def pending(job_dir: Path) -> Recovery | None:
    rec = Recovery.load(job_dir)
    return rec if rec is not None and rec.status == "pending" and rec.valid() else None


def create(job_dir: Path, attempts: list[dict], lo: int, hi: int) -> Recovery:
    """A new recovery from the answers of one analysis (the base: the answer with the fewest problems)."""
    rec = Recovery(attempts=attempts, lo=lo, hi=hi, created=time.time())
    ok = rec.valid()
    if ok:
        rec.base = min(ok, key=lambda i: len(attempts[i].get("issues") or []))
    rec.save(job_dir)
    return rec


def add_attempt(job_dir: Path, rec: Recovery, attempt: dict) -> Recovery:
    """A new answer; one that passes every check becomes the base while nothing was chosen yet."""
    rec.attempts.append(attempt)
    if attempt.get("moves") and not attempt.get("issues") and not rec.choices:
        rec.base = len(rec.attempts) - 1
    rec.save(job_dir)
    return rec


def clean(rec: Recovery) -> list[int]:
    """Answers that passed every check (possible after «ещё вариант»)."""
    return [i for i in rec.valid() if not rec.attempts[i].get("issues")]


# ---------------------------------------------------------------- steps and their versions
def _overlap(a: range, b: range) -> int:
    return max(0, min(a.stop, b.stop) - max(a.start, b.start))


def _same(m1: dict, m2: dict) -> bool:
    return all(m1.get(k) == m2.get(k) for k in ("move_type", "speaker", "title", "explanation"))


def steps(rec: Recovery, segments: list[analyzer.Segment], hints=None, names: dict[str, str] | None = None) -> list[dict]:
    """The steps of the base answer: segment range, the lines, problems of the base version and the
    versions to pick from (`alts`: id "attempt:move", label, move, issues), plus the current choice."""
    n = len(segments)
    base = rec.raw(rec.base)
    others = [(i, rec.raw(i)) for i in rec.valid() if i != rec.base]
    sides = {i: analyzer.hint_sides(raw, segments, hints) if hints is not None else {} for i, raw in [(rec.base, base), *others]}
    out = []
    for k, m in enumerate(base):
        rng = analyzer._seg_range(m, n)
        alts = [{"id": f"{rec.base}:{k}", "label": rec.attempt_label(rec.base), "move": m.model_dump(),
                 "issues": analyzer.move_issues(m, segments, hints, sides[rec.base], names)}]
        for i, raw in others:
            best = max(range(len(raw)), key=lambda j: _overlap(rng, analyzer._seg_range(raw[j], n)), default=None)
            if best is None or _overlap(rng, analyzer._seg_range(raw[best], n)) == 0:
                continue
            cand = raw[best].model_dump()
            if any(_same(cand, a["move"]) for a in alts):
                continue
            alts.append({"id": f"{i}:{best}", "label": rec.attempt_label(i), "move": cand,
                         "issues": analyzer.move_issues(raw[best], segments, hints, sides[i], names)})
        choice = rec.choices.get(str(k)) or {}
        out.append({"index": k, "range": (rng.start, rng.stop - 1), "start": segments[rng.start].start,
                    "end": segments[rng.stop - 1].end, "lines": " ".join(s.text.strip() for s in segments[rng.start:rng.stop]),
                    "issues": alts[0]["issues"], "alts": alts, "choice": choice})
    return out


def current(step: dict) -> tuple[str, dict]:
    """(chosen version id, the move as it will be used: the version + the person's edits)."""
    choice = step.get("choice") or {}
    alt = next((a for a in step["alts"] if a["id"] == choice.get("alt")), step["alts"][0])
    move = dict(alt["move"])
    edit = {k: v for k, v in (choice.get("edit") or {}).items() if k in EDITABLE}
    move.update(edit)
    if "move_type" in edit:  # the person's type decides; the model's strength would contradict it
        move["strength"] = analyzer.TYPE_STRENGTH[edit["move_type"]]
    return alt["id"], move


def validate_edit(edit: dict) -> list[str]:
    """Russian problems of a hand edit (the fields the window lets the person change)."""
    errs = []
    if edit.get("move_type") not in config.MOVE_TYPES:
        errs.append("Выберите тип хода.")
    if edit.get("speaker") not in ("white", "black"):
        errs.append("Выберите, чей это ход.")
    for key, ru in (("gist", "Суть хода"), ("title", "Заголовок"), ("explanation", "Объяснение")):
        if not (edit.get(key) or "").strip():
            errs.append(f"{ru} не может быть пустым.")
    return errs


def set_choice(job_dir: Path, rec: Recovery, step: dict, alt_id: str, edit: dict | None) -> list[str]:
    """Remember the version picked for a step and the person's changes to it (only what differs)."""
    alt = next((a for a in step["alts"] if a["id"] == alt_id), None)
    if alt is None:
        return ["Такого варианта нет — обновите окно."]
    changed = {}
    if edit:
        edit = {k: (v.strip() if isinstance(v, str) else v) for k, v in edit.items() if k in EDITABLE}
        errs = validate_edit({**{k: alt["move"].get(k) for k in EDITABLE}, **edit})
        if errs:
            return errs
        changed = {k: v for k, v in edit.items() if v != alt["move"].get(k)}
    old = rec.choices.get(str(step["index"])) or {}
    edits = dict(old.get("edits") or {})
    if changed:
        edits[alt_id] = changed
    else:
        edits.pop(alt_id, None)
    rec.choices[str(step["index"])] = {"alt": alt_id, "edit": changed or None, "edits": edits}
    rec.save(job_dir)
    return []


def pick(job_dir: Path, rec: Recovery, step: dict, alt_id: str) -> None:
    """Show another version of a step; the person's edits of that version (if any) come back with it."""
    old = rec.choices.get(str(step["index"])) or {}
    edits = dict(old.get("edits") or {})
    rec.choices[str(step["index"])] = {"alt": alt_id, "edit": edits.get(alt_id), "edits": edits}
    rec.save(job_dir)


def set_base(job_dir: Path, rec: Recovery, i: int) -> None:
    """Another answer gives the split; choices for steps no longer existing are dropped."""
    if i in rec.valid() and i != rec.base:
        rec.base = i
        rec.choices = {}
        rec.save(job_dir)


def build(rec: Recovery, segments: list[analyzer.Segment], hints=None, names=None) -> list[analyzer.LLMMove]:
    """The base split with every step's chosen (and edited) content."""
    out = []
    for st in steps(rec, segments, hints, names):
        _, move = current(st)
        base = rec.raw(rec.base)[st["index"]].model_dump()
        merged = {**base, **{k: move[k] for k in CONTENT if k in move}}
        out.append(analyzer.LLMMove.model_validate(merged))
    return out


def remaining(rec: Recovery, segments, hints=None, names=None) -> dict:
    """Problems left with the current choices ({"general": [...], "moves": {k: [...]}})."""
    return analyzer.review_issues(build(rec, segments, hints, names), segments, rec.lo, hints, names)


def resolve(job_dir: Path, rec: Recovery, segments, names: dict[str, str], duration: float | None, hints=None) -> list:
    """The final moves (analyzer.Move) from the choices; the recovery is marked resolved."""
    raw = build(rec, segments, hints, names)
    if hints is not None:
        chosen = {int(k) for k, c in rec.choices.items() if "speaker" in (c.get("edit") or {})}
        keep = {k: raw[k].speaker for k in chosen if k < len(raw)}
        raw = analyzer.fix_speakers(raw, segments, hints)
        for k, side in keep.items():
            raw[k].speaker = side
    moves = analyzer.llm_to_moves(raw, segments, names, duration)
    rec.status = "resolved"
    rec.save(job_dir)
    return moves
