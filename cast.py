"""Who is who on screen: the persons found by vision.py, named and confirmed by the
person, and remembered for the whole project.

Per job, `cast.json` holds one entry per detected person (vision rank): name, side
(white / black / ""), removed, merged into another person, and the registry
character it was matched to. The project registry (`config.CAST_DIR/cast.json` +
one face image per character) keeps every confirmed character with a mean SFace
embedding: new clips get name suggestions from it, and a confident match names
the persons automatically until the person confirms (CLI runs, skipped dialog).

`apply(va, cast)` rewrites a vision.VideoAnalysis: merged persons become one,
the faces of removed persons are dropped, the white player becomes person 0 and
the black one person 1. Everything downstream (who speaks, reframing, avatars)
works on that view, so it follows the confirmed identities.
"""
from __future__ import annotations

import copy
import json
import logging
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import numpy as np

import config

log = logging.getLogger(__name__)
FILE = "cast.json"
MATCH = 0.40     # SFace cosine: suggest the registry character
SURE = 0.52      # … and use it without asking
SIDES = ("white", "black")
SIDE_RU = {"white": "белые", "black": "чёрные", "": "не играет"}
SIDE_FOR = {"white": "белых", "black": "чёрных"}          # «играет за белых»


# ================================================================ project registry
@dataclass
class Character:
    id: str
    name: str
    emb: list[float]
    n: int = 1                    # confirmations (embedding = running mean)
    jobs: list[str] = field(default_factory=list)
    image: str = ""               # file in CAST_DIR
    updated: float = 0.0


class Registry:
    def __init__(self, chars: list[Character] | None = None, root: Path | None = None):
        self.root = root or config.CAST_DIR
        self.chars = chars or []

    @classmethod
    def load(cls, root: Path | None = None) -> "Registry":
        root = root or config.CAST_DIR
        p = root / FILE
        if not p.exists():
            return cls([], root)
        try:
            names = {f.name for f in fields(Character)}
            data = json.loads(p.read_text())
            return cls([Character(**{k: v for k, v in c.items() if k in names}) for c in data.get("characters", [])], root)
        except Exception as e:
            log.warning("Реестр персонажей повреждён (%s) — начинаю новый", e)
            return cls([], root)

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / (FILE + ".tmp")
        tmp.write_text(json.dumps({"characters": [asdict(c) for c in self.chars]}, ensure_ascii=False, indent=1))
        tmp.replace(self.root / FILE)

    def get(self, cid: str | None) -> Character | None:
        return next((c for c in self.chars if c.id == cid), None)

    def by_name(self, name: str) -> Character | None:
        key = " ".join((name or "").split()).lower()
        return next((c for c in self.chars if c.name.lower() == key), None)

    def match(self, emb) -> tuple[Character | None, float]:
        if emb is None or not self.chars:
            return None, 0.0
        e = np.asarray(emb, np.float32)
        best, sim = None, -1.0
        for c in self.chars:
            s = float(np.dot(e, np.asarray(c.emb, np.float32)))
            if s > sim:
                best, sim = c, s
        return best, sim

    def image_path(self, c: Character) -> Path | None:
        p = self.root / c.image if c.image else None
        return p if p is not None and p.exists() else None

    def remember(self, name: str, emb, face: Path | None, job_id: str, cid: str | None = None) -> Character:
        """Add a confirmed character, or update the one with that id / name (mean embedding)."""
        name = " ".join(name.split())
        c = self.get(cid) if cid else None
        if c is None:
            c = self.by_name(name)
        e = np.asarray(emb, np.float32) if emb is not None else None
        if c is None:
            c = Character("c" + uuid.uuid4().hex[:8], name, [round(float(v), 5) for v in e] if e is not None else [])
            self.chars.append(c)
        else:
            c.name = name
            if e is not None and c.emb:
                m = (np.asarray(c.emb, np.float32) * c.n + e) / (c.n + 1)
                c.emb = [round(float(v), 5) for v in m / (np.linalg.norm(m) + 1e-9)]
                c.n += 1
            elif e is not None:
                c.emb = [round(float(v), 5) for v in e]
        if job_id not in c.jobs:
            c.jobs.append(job_id)
        if face is not None and face.exists():
            self.root.mkdir(parents=True, exist_ok=True)
            c.image = f"{c.id}{face.suffix}"
            shutil.copy(face, self.root / c.image)
        c.updated = time.time()
        return c

    def forget(self, cid: str) -> bool:
        c = self.get(cid)
        if c is None:
            return False
        self.chars.remove(c)
        if c.image:
            (self.root / c.image).unlink(missing_ok=True)
        self.save()
        return True

    def names(self) -> list[str]:
        return sorted(c.name for c in self.chars)


# ================================================================ one job's cast
@dataclass
class Person:
    person: int                   # vision rank (faces/person<N>.png)
    name: str = ""
    side: str = ""                # white | black | ""
    removed: bool = False         # not a character (poster, extra, wrong detection)
    merged_into: int | None = None  # the same character as another detected person
    character: str | None = None  # registry id once confirmed
    suggestion: str = ""          # registry name the face looks like
    similarity: float = 0.0
    guessed: bool = False         # name/side proposed automatically, not confirmed
    side_sure: bool = False       # the side may be used before confirmation (see proposal)
    screen: float = 0.0           # seconds on screen


@dataclass
class JobCast:
    persons: list[Person] = field(default_factory=list)
    confirmed: bool = False
    skipped: bool = False         # the person closed the dialog: automatic guesses only
    updated: float = 0.0

    @classmethod
    def load(cls, job_dir: Path) -> "JobCast | None":
        p = job_dir / FILE
        if not p.exists():
            return None
        d = json.loads(p.read_text())
        names = {f.name for f in fields(Person)}
        return cls([Person(**{k: v for k, v in x.items() if k in names}) for x in d.get("persons", [])],
                   d.get("confirmed", False), d.get("skipped", False), d.get("updated", 0.0))

    def save(self, job_dir: Path) -> None:
        self.updated = time.time()
        tmp = job_dir / (FILE + ".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=1))
        tmp.replace(job_dir / FILE)

    def get(self, person: int) -> Person | None:
        return next((p for p in self.persons if p.person == person), None)

    def root(self, person: int) -> int:
        seen = set()
        p = self.get(person)
        while p is not None and p.merged_into is not None and p.person not in seen:
            seen.add(p.person)
            nxt = self.get(p.merged_into)
            if nxt is None:
                break
            p = nxt
        return p.person if p is not None else person

    def side_of(self, side: str) -> Person | None:
        return next((p for p in self.persons if p.side == side and not p.removed and p.merged_into is None), None)

    def trusted(self) -> "JobCast":
        """What may be used without confirmation: only confident registry matches."""
        if self.confirmed:
            return self
        keep = [Person(p.person, p.name, p.side if p.side_sure else "", character=p.character, similarity=p.similarity)
                if p.similarity >= SURE and p.name else Person(p.person) for p in self.persons]
        return JobCast(keep, False, self.skipped)


def proposal(va, names: dict[str, str], registry: Registry | None = None) -> JobCast:
    """Unconfirmed cast for a fresh analysis: registry suggestions; the two main persons
    take the job's player names (white = the most visible one) as a guess."""
    registry = registry or Registry.load()
    persons = []
    for rank in list(va.persons) + list(va.others):
        c, sim = registry.match(va.person_emb.get(str(rank)))
        p = Person(rank, screen=va.screen.get(str(rank), 0.0))
        if c is not None and sim >= MATCH:
            p.suggestion, p.similarity, p.name, p.guessed = c.name, round(sim, 3), c.name, True
            if sim >= SURE:
                p.character = c.id
        persons.append(p)
    defaults = {"white": "Белые", "black": "Чёрные"}
    for side in SIDES:  # a known face with the job's player name takes that side
        hit = next((p for p in persons if p.name and p.name.lower() == (names.get(side) or "").lower()), None)
        if hit is not None and not hit.side:
            hit.side = side
    main = [p for p in persons if p.person in va.persons]
    for side in SIDES:
        if any(p.side == side for p in persons):
            continue
        free = next((p for p in main if not p.side), None)
        if free is not None:
            free.side, free.guessed = side, True
            if not free.name and names.get(side) and names[side] != defaults[side]:
                free.name = names[side]
    # a recognised face may keep its side without asking when the side came from the job's
    # player name, or when the player names are still the defaults (who plays white is arbitrary)
    default_names = all((names.get(sd) or defaults[sd]) == defaults[sd] for sd in SIDES)
    for p in persons:
        if p.side and p.similarity >= SURE:
            p.side_sure = default_names or p.name.lower() == (names.get(p.side) or "").lower()
    return JobCast(persons)


def validate(jc: JobCast) -> list[str]:
    """Russian messages for a cast that cannot be confirmed as is."""
    errs = []
    live = [p for p in jc.persons if not p.removed and p.merged_into is None]
    for side in SIDES:
        owners = [p for p in live if p.side == side]
        if len(owners) > 1:
            errs.append(f"За {SIDE_FOR[side]} играют сразу несколько лиц ({', '.join(f'№{p.person + 1}' for p in owners)}) — "
                        "оставьте одно или объедините их.")
    for p in jc.persons:
        if p.merged_into is not None:
            target = jc.get(p.merged_into)
            if target is None or target.person == p.person:
                errs.append(f"Лицо №{p.person + 1}: выберите, с кем объединить.")
            elif target.removed:
                errs.append(f"Лицо №{p.person + 1} объединено с убранным лицом №{target.person + 1}.")
            elif jc.root(p.person) == p.person:
                errs.append(f"Лицо №{p.person + 1}: объединение по кругу.")
        elif not p.removed:
            if p.side and not p.name.strip():
                errs.append(f"Лицо №{p.person + 1} играет за {SIDE_FOR[p.side]} — впишите имя.")
            if len(p.name) > 40:
                errs.append(f"Имя лица №{p.person + 1} длиннее 40 символов.")
    seen: dict[str, int] = {}
    for p in live:
        key = p.name.strip().lower()
        if key and key in seen:
            errs.append(f"Лица №{seen[key] + 1} и №{p.person + 1} названы одинаково — если это один персонаж, "
                        "объедините их («Тот же, что…»).")
        elif key:
            seen[key] = p.person
    if live and not any(p.side for p in live):
        errs.append("Отметьте, кто играет белыми и кто чёрными (хотя бы одного).")
    return errs


def confirm(jc: JobCast, job_dir: Path, job_id: str, va, registry: Registry | None = None) -> JobCast:
    """Save the confirmed cast and remember its named characters for the project."""
    registry = registry or Registry.load()
    jc.confirmed, jc.skipped = True, False
    for p in jc.persons:
        p.guessed = False
        if p.removed or p.merged_into is not None or not p.name.strip():
            continue
        group = [q.person for q in jc.persons if q.merged_into is not None and jc.root(q.person) == p.person] + [p.person]
        embs = [np.asarray(va.person_emb[str(r)], np.float32) for r in group if str(r) in va.person_emb]
        emb = None
        if embs:
            m = np.mean(embs, axis=0)
            emb = m / (np.linalg.norm(m) + 1e-9)
        face = job_dir / "faces" / f"person{p.person}.png"
        c = registry.remember(p.name, emb, face if face.exists() else None, job_id, p.character)
        p.character = c.id
    registry.save()
    jc.save(job_dir)
    return jc


def apply(va, jc: JobCast | None):
    """A copy of `va` that follows the cast (see the module docstring)."""
    if jc is None or not jc.persons:
        return va
    jc = jc.trusted()
    out = copy.deepcopy(va)
    removed = {p.person for p in jc.persons if p.removed}
    root = {p.person: jc.root(p.person) for p in jc.persons}
    white, black = jc.side_of("white"), jc.side_of("black")
    # fixed slots: white is person 0, black person 1; an unknown slot takes the next main person
    slots: list[int | None] = [white.person if white else None, black.person if black else None]
    rest_ranks = [root.get(r, r) for r in list(va.persons) + list(va.others)]
    rest_ranks = [r for i, r in enumerate(rest_ranks) if r not in removed and r not in slots and r not in rest_ranks[:i]]
    for i in range(2):
        if slots[i] is None and rest_ranks:
            slots[i] = rest_ranks.pop(0)
    order = [r for r in slots if r is not None] + rest_ranks
    new_of = {r: i for i, r in enumerate(order)}
    rest = len(order)

    def remap(person):
        if person is None:
            return None
        rr = root.get(person, person)
        if rr in removed or person in removed:
            return "drop"
        nonlocal rest
        if rr not in new_of:
            new_of[rr] = rest
            rest += 1
        return new_of[rr]

    dropped_tracks = set()
    for t in out.tracks:
        m = remap(t.person)
        if m == "drop":
            dropped_tracks.add(t.id)
            t.person = None
        else:
            t.person = m
    out.faces = [f for f in out.faces if f.track not in dropped_tracks]
    embs: dict[int, list] = {}
    for r, e in va.person_emb.items():
        m = remap(int(r))
        if m != "drop":
            embs.setdefault(m, []).append(np.asarray(e, np.float32))
    out.person_emb = {}
    for k, es in embs.items():
        mean = np.mean(es, axis=0)
        out.person_emb[str(k)] = [round(float(v), 5) for v in mean / (np.linalg.norm(mean) + 1e-9)]
    out.screen = {}
    for r, sec in va.screen.items():
        m = remap(int(r))
        if m != "drop":
            out.screen[str(m)] = round(out.screen.get(str(m), 0.0) + sec, 1)
    out.persons = [new_of[r] for r in order[:2]]
    out.others = sorted(v for v in set(new_of.values()) if v not in out.persons and str(v) in out.person_emb)
    out.crops = {str(new_of[r]): f"person{r}.png" for r in order}
    return out


def sides_known(jc: JobCast | None) -> bool:
    t = jc.trusted() if jc else None
    return t is not None and t.side_of("white") is not None and t.side_of("black") is not None


def names_for_sides(jc: JobCast | None) -> dict[str, str]:
    t = jc.trusted() if jc else None
    if t is None:
        return {}
    return {side: p.name.strip() for side in SIDES if (p := t.side_of(side)) is not None and p.name.strip()}


def context_text(jc: JobCast | None) -> str:
    """A line for the LLM: who is on screen (only confirmed or confident identities)."""
    t = jc.trusted() if jc else None
    if t is None:
        return ""
    players = [f"{p.name} — {SIDE_RU[p.side]}" for side in SIDES if (p := t.side_of(side)) is not None and p.name]
    extra = [p.name for p in t.persons if not p.removed and p.merged_into is None and not p.side and p.name]
    if not players and not extra:
        return ""
    how = "подтверждены человеком" if jc.confirmed else "узнаны по лицам"
    txt = f"Персонажи в кадре ({how}): " + "; ".join(players)
    if extra:  # confirmed: really not players; otherwise just their side is unknown
        tail = " (не игроки)" if jc.confirmed else " (сторона не известна)"
        txt += ("; " if players else "") + "также в кадре: " + ", ".join(extra) + tail
    return txt + "."
