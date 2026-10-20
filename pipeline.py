"""Job storage + orchestration of all stages. Shared by the CLI and the web UI.

Every job lives in `jobs/<id>/`:
    meta.json         inputs, caption, status
    clip.mp4          normalized source fragment
    transcript.json   faster-whisper segments
    vision.json       faces / shots / mouth activity (vision.py), faces/person<N>.png
    cast.json         who is who on screen, confirmed in the characters dialog (cast.py)
    speakers.json     who speaks each segment (speakers.py) — hints for the LLM
    sounds.json       notable sound events (sounds.py), reviewed on «Звуки»
    summary.json      structured scene summary (summary.py), reviewed before the analysis
    style.json        visual style of the video (style.py)
    moves.json        LLM analysis (editable)
    avatars/          white.png / black.png
    final.mp4         rendered video (1080x1920 for TikTok)
"""
from __future__ import annotations

import dataclasses
import json
import logging
import shutil
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Callable

import analyzer
import cast
import config
import describe
import dialogue
import errors
import ingest
import llm
import moves_edit
import renderer
import sounds
import speakers
import style as vstyle
import summary
import vision

log = logging.getLogger(__name__)
Progress = Callable[[float, str], None]


def _noop(_frac: float, _desc: str) -> None:
    pass


@dataclass
class Job:
    id: str
    source: str
    start: str | None = None
    end: str | None = None
    context: str = ""
    names: dict[str, str] = field(default_factory=lambda: {"white": "Белые", "black": "Чёрные"})
    avatar_inputs: dict[str, str | None] = field(default_factory=lambda: {"white": None, "black": None})
    caption: str = ""
    hashtags: str = "шахматы chess разбор диалог"
    status: str = "new"  # new|ingested|transcribed|analyzed|rendered|published|rejected|failed
    error: str = ""
    created: float = field(default_factory=time.time)
    mode: str = "auto"           # auto = the LLM writes the moves | manual = the person does (dialogue.json)
    allow_full_download: bool = False  # the person agreed to download the whole video when a fragment cannot be
    # what the source is (search result / yt-dlp metadata / file name) and the description built from it
    source_title: str = ""
    source_kind: str = ""        # video | short | file
    source_channel: str = ""
    work_kind: str = ""          # kind of the work chosen by the person ("" = detect, see describe.KINDS)
    caption_generated: str = ""  # last automatic description; caption != this means the person edited it
    # LLM used for the analysis
    llm_provider: str = config.LLM_PROVIDER
    llm_model: str = ""          # "" = the provider's default from .env
    # render settings (see renderer.RenderOptions); changing them only needs a re-render
    pause_on_move: bool = config.PAUSE_ON_MOVE
    sfx_enabled: bool = config.SFX_ENABLED
    sfx_volume: float = config.SFX_VOLUME
    output_format: str = config.OUTPUT_FORMAT
    typing_cps: float = config.TYPING_CPS
    read_hold: float = config.READ_HOLD
    sfx_pause: bool = config.SFX_PAUSE
    sfx_unpause: bool = config.SFX_UNPAUSE
    sfx_typing: bool = config.SFX_TYPING

    # ------------------------------------------------------------ paths
    @property
    def dir(self) -> Path:
        return config.JOBS_DIR / self.id

    clip = property(lambda self: self.dir / "clip.mp4")
    transcript = property(lambda self: self.dir / "transcript.json")
    moves_file = property(lambda self: self.dir / "moves.json")
    speakers_file = property(lambda self: self.dir / "speakers.json")
    final = property(lambda self: self.dir / "final.mp4")

    @property
    def avatars(self) -> dict[str, Path]:
        return {s: self.dir / "avatars" / f"{s}.png" for s in ("white", "black")}

    # ------------------------------------------------------------ persistence
    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "meta.json").write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2))

    @classmethod
    def load(cls, job_id: str) -> "Job":
        path = config.JOBS_DIR / job_id / "meta.json"
        if not path.exists():
            raise FileNotFoundError(f"No such job: {job_id}")
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in json.loads(path.read_text()).items() if k in known})

    def render_options(self) -> renderer.RenderOptions:
        return renderer.RenderOptions(**{k: getattr(self, k) for k in OPTION_FIELDS})

    def set_options(self, **values) -> None:
        """Update render / LLM settings (None = keep); call save() afterwards."""
        unknown = set(values) - set(OPTION_FIELDS) - {"llm_provider", "llm_model"}
        if unknown:
            raise TypeError(f"unknown job options: {sorted(unknown)}")
        fmt = values.get("output_format")
        if fmt is not None and fmt not in config.OUTPUT_FORMATS:
            raise ValueError(f"Формат вывода должен быть одним из: {', '.join(config.OUTPUT_FORMATS)}")
        prov = values.get("llm_provider")
        if prov is not None and prov not in config.LLM_PROVIDERS:
            raise ValueError(f"Нейросеть должна быть одной из: {', '.join(config.LLM_PROVIDERS)}")
        for name, value in values.items():
            if value is not None:
                setattr(self, name, value)

    @property
    def llm_label(self) -> str:
        return f"{llm.spec(self.llm_provider).label} · {self.llm_model or llm.default_model(self.llm_provider)}"

    def load_speakers(self) -> speakers.SpeakerHints | None:
        if not self.speakers_file.exists():
            return None
        try:
            return speakers.SpeakerHints.load(self.speakers_file)
        except Exception as e:
            log.warning("Файл speakers.json повреждён (%s) — работаю без подсказок", e)
            return None

    def load_moves(self) -> list[analyzer.Move]:
        return analyzer.parse_moves(self.moves_file.read_text())

    @property
    def caption_edited(self) -> bool:
        return bool(self.caption.strip()) and self.caption.strip() != self.caption_generated.strip()

    def clip_duration(self) -> float | None:
        return ingest.probe(self.clip).duration if self.clip.exists() else None

    def save_moves(self, moves: list[analyzer.Move]) -> None:
        self.moves_file.write_text(analyzer.moves_to_json(moves))


OPTION_FIELDS = tuple(f.name for f in dataclasses.fields(renderer.RenderOptions))


def list_jobs() -> list[Job]:
    if not config.JOBS_DIR.exists():
        return []
    jobs = []
    for meta in config.JOBS_DIR.glob("*/meta.json"):
        try:
            jobs.append(Job.load(meta.parent.name))
        except Exception as e:
            log.warning("Пропускаю повреждённое задание %s: %s", meta.parent.name, e)
    return sorted(jobs, key=lambda j: j.created, reverse=True)


def keep_upload(path: Path) -> Path:
    """A file uploaded through the web UI lives in Gradio's temp folder and may vanish before a
    retry: keep a hard link (or a copy) in the source cache. Other local files stay where they are."""
    import tempfile

    tmp = Path(tempfile.gettempdir()).resolve()
    path = path.resolve()
    if not path.is_relative_to(tmp) or path.is_relative_to(config.JOBS_DIR.resolve()):
        return path
    import hashlib
    import os

    st = path.stat()
    key = hashlib.sha1(f"{path.name}:{st.st_size}:{int(st.st_mtime)}".encode()).hexdigest()[:16]
    dst = config.SOURCES_DIR / f"upload-{key}" / f"full{path.suffix.lower()}"
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(path, dst)
        except OSError:
            shutil.copy2(path, dst)
    return dst


def create_job(
    source: str,
    start: str | None = None,
    end: str | None = None,
    white: str = "Белые",
    black: str = "Чёрные",
    white_avatar: str | None = None,
    black_avatar: str | None = None,
    context: str = "",
    source_title: str = "",
    source_kind: str = "",
    source_channel: str = "",
    work_kind: str = "",
    caption: str = "",
    mode: str = "auto",
    **options,
) -> Job:
    """`options`: render / LLM settings (see Job.set_options); missing ones come from .env.
    `caption`: a description the person already wrote (otherwise it is generated)."""
    if not source or not source.strip():
        raise ValueError("Source (URL or file path) is required")
    # validate timecodes and the source early: nothing is created for a wrong input
    t0, t1 = ingest.parse_time(start), ingest.parse_time(end)
    if t0 is not None and t1 is not None and t1 <= t0:
        raise ValueError("Конец фрагмента должен быть позже начала.")
    src = ingest.inspect_source(source)
    value = str(keep_upload(Path(src.value))) if src.kind == "file" else src.value
    job = Job(
        id=time.strftime("%Y%m%d-%H%M%S"),
        source=value,
        start=start or None,
        end=end or None,
        context=context,
        names={"white": white.strip() or "Белые", "black": black.strip() or "Чёрные"},
    )
    job.set_options(**options)
    while job.dir.exists():  # two jobs in the same second
        job.id += "x"
    # copy avatar uploads into the job: Gradio temp files disappear
    inputs = job.dir / "inputs"
    for side, src in (("white", white_avatar), ("black", black_avatar)):
        if src:
            inputs.mkdir(parents=True, exist_ok=True)
            dst = inputs / f"{side}{Path(src).suffix or '.png'}"
            shutil.copy(Path(src).expanduser(), dst)
            job.avatar_inputs[side] = str(dst)
    job.mode = mode if mode in ("auto", "manual") else "auto"
    job.source_title, job.source_channel, job.work_kind = source_title.strip(), source_channel.strip(), work_kind
    job.source_kind = source_kind or ("short" if "/shorts/" in job.source else "")
    if not job.source_title:
        meta = ingest.source_meta(job.source)  # cached metadata / file name, no network
        job.source_title = meta.get("title", "")
    job.hashtags = ""
    suggestion = suggest_caption(job)
    job.caption_generated = suggestion.text
    job.caption = caption.strip() or suggestion.text
    job.save()
    return job


# ---------------------------------------------------------------- description
def suggest_caption(job: Job) -> describe.Suggestion:
    meta = ingest.source_meta(job.source) if job.source else {}
    return describe.suggest(job.source_title or meta.get("title", ""), kind=job.work_kind,
                            channel=job.source_channel or meta.get("channel", ""), meta=meta, names=job.names)


def refresh_caption(job: Job, force: bool = False) -> tuple[describe.Suggestion, bool]:
    """New automatic description; applied only if the person hasn't edited theirs (or `force`)."""
    suggestion = suggest_caption(job)
    applied = force or not job.caption_edited
    if applied:
        job.caption = job.caption_generated = suggestion.text
        job.hashtags = ""
    return suggestion, applied


def _fill_source_meta(job: Job) -> None:
    """After the download: the video's title/channel/kind for the source card and description."""
    meta = ingest.source_meta(job.source)
    changed = False
    if meta.get("title") and not job.source_title:
        job.source_title, changed = meta["title"], True
    if meta.get("channel") and not job.source_channel:
        job.source_channel, changed = meta["channel"], True
    kind = "file" if meta.get("file") else ("short" if meta.get("is_short") else "video")
    if job.source_kind != kind:
        job.source_kind, changed = kind, True
    _, applied = refresh_caption(job)
    if applied:
        log.info("Описание публикации: %s", job.caption)
    if changed or applied:
        job.save()


# ---------------------------------------------------------------- stages
def step_ingest(job: Job, progress: Progress = _noop) -> None:
    log.info("▶ Загрузка и нарезка видео")
    ingest.ingest(job.source, job.start, job.end, job.dir, progress, allow_full=job.allow_full_download)
    job.status = "ingested"
    job.save()
    try:
        _fill_source_meta(job)
    except Exception as e:  # metadata is a nicety: never fail the pipeline because of it
        log.debug("source metadata: %s", e)


def step_transcribe(job: Job) -> list[analyzer.Segment]:
    log.info("▶ Распознавание речи (faster-whisper %s)", config.WHISPER_MODEL)
    segments = analyzer.transcribe(job.clip)
    analyzer.save_transcript(segments, job.transcript)
    job.status = "transcribed"
    job.save()
    return segments


def raw_vision(job: Job) -> vision.VideoAnalysis | None:
    """Faces/shots of the clip as detected (cached); None if face analysis is unavailable."""
    try:
        return vision.load_or_analyze(job.clip, job.dir)
    except Exception as e:  # never fail the pipeline because of faces
        log.warning("Анализ лиц недоступен (%s) — кадр будет по центру, аватарки из инициалов", e)
        return None


def load_vision(job: Job) -> vision.VideoAnalysis | None:
    """The face analysis as the cast describes it: merged / removed faces, white = person 0."""
    va = raw_vision(job)
    if va is None:
        return None
    try:
        return cast.apply(va, cast.JobCast.load(job.dir))
    except Exception as e:
        log.warning("Персонажи задания не применились (%s) — беру лица как найдены", e)
        return va


def step_faces(job: Job) -> cast.JobCast | None:
    """Faces in the clip and the proposed cast (names suggested from the project's characters).
    Faces recognised for sure name the players right away when the names are still the defaults."""
    log.info("▶ Лица в кадре")
    va = raw_vision(job)
    if va is None:
        return None
    jc = cast.JobCast.load(job.dir)
    ranks = sorted(list(va.persons) + list(va.others))
    if jc is None or (not jc.confirmed and sorted(p.person for p in jc.persons) != ranks):
        jc = cast.proposal(va, job.names)
        jc.save(job.dir)
    known = cast.names_for_sides(jc)
    defaults = {"white": "Белые", "black": "Чёрные"}
    renamed = {sd: n for sd, n in known.items() if job.names.get(sd) in ("", defaults[sd]) and n != job.names.get(sd)}
    if renamed:
        job.names.update(renamed)
        job.save()
        log.info("Узнал по лицам: %s", ", ".join(f"{n} ({cast.SIDE_RU[sd]})" for sd, n in renamed.items()))
    recognised = sum(1 for p in jc.persons if p.similarity >= cast.MATCH)
    log.info("✓ Лиц в кадре: %d%s", len(jc.persons), f", узнано из прошлых роликов: {recognised}" if recognised else "")
    return jc


def needs_cast(job: Job) -> bool:
    """The characters dialog is due: faces were found and nobody confirmed or skipped them yet."""
    jc = cast.JobCast.load(job.dir)
    return jc is not None and bool(jc.persons) and not jc.confirmed and not jc.skipped


def confirm_cast(job: Job, jc: cast.JobCast) -> list[str]:
    """Save the person's cast, remember it for the project and bring the job in line:
    player names (also inside the moves), who speaks, avatars and sound speakers are redone."""
    va = raw_vision(job)
    if va is None:
        raise ValueError("Анализ лиц для этого задания недоступен.")
    errs = cast.validate(jc)
    if errs:
        return errs
    cast.confirm(jc, job.dir, job.id, va)
    names = cast.names_for_sides(jc)
    old = dict(job.names)
    job.names.update(names)
    if job.moves_file.exists() and names:
        moves = job.load_moves()
        for m in moves:
            who, gist = moves_edit.split_number(m.move_number)
            if who == old.get(m.speaker) and job.names.get(m.speaker) != who:
                m.move_number = moves_edit.join_number(job.names[m.speaker], gist)
        job.save_moves(analyzer.sanitize_moves(moves, None))
    for stale in (job.speakers_file, *job.avatars.values()):
        stale.unlink(missing_ok=True)
    if job.transcript.exists():
        step_speakers(job)
        if sounds.Sounds.exists(job.dir):
            refresh_sound_speakers(job)
    if job.final.exists() or job.status == "rendered":
        job.status = "analyzed"  # the video shows the old names/faces until it is rendered again
    job.save()
    log.info("✓ Персонажи подтверждены: %s", cast.context_text(jc).removeprefix("Персонажи в кадре (подтверждены человеком): ") or "—")
    return []


def skip_cast(job: Job) -> None:
    jc = cast.JobCast.load(job.dir) or cast.JobCast()
    jc.skipped = True
    jc.save(job.dir)


def label_sides(job: Job, hints: speakers.SpeakerHints | None) -> dict[str, str]:
    """Voice label -> side: from the faces/cast, else from the moves of the analysis."""
    if hints is None:
        return {}
    if all(hints.sides.get(lab) for lab in speakers.LABELS):
        return dict(hints.sides)
    if job.moves_file.exists():
        try:
            return speakers.label_sides(hints, job.load_moves())
        except Exception:
            return {}
    return {}


def step_speakers(job: Job) -> speakers.SpeakerHints | None:
    """Audio-visual "who speaks" hints for every transcript segment."""
    if not config.DIARIZATION:
        return None
    log.info("▶ Лица и голоса: кто говорит")
    try:
        segments = analyzer.load_transcript(job.transcript)
        va = load_vision(job)
        embs = {s: vision.embed_image(p) for s, p in job.avatar_inputs.items() if p} if va else {}
        hints = speakers.diarize(segments, job.clip, va, embs)
        if va is not None and hints.persons.get("A") is not None and cast.sides_known(cast.JobCast.load(job.dir)):
            hints.sides = {"A": "white", "B": "black"}  # cast.apply made white person 0 and black person 1
    except Exception as e:
        log.warning("Не удалось определить, кто говорит (%s) — нейросеть решит сама", e)
        return None
    hints.save(job.speakers_file)
    return hints


def step_analyze(job: Job) -> list[analyzer.Move]:
    segments = analyzer.load_transcript(job.transcript)
    duration = ingest.probe(job.clip).duration
    hints = job.load_speakers() if job.speakers_file.exists() else step_speakers(job)
    log.info("▶ Разбор ходов нейросетью (%s)", job.llm_label)
    try:
        provider = llm.get_provider(job.llm_provider, job.llm_model or None)
        moves = analyzer.analyze(
            segments, job.names["white"], job.names["black"], analysis_context(job), duration, provider=provider, hints=hints
        )
    except Exception as e:
        e.ai_stage = "analysis"  # the UI offers another model and repeats exactly this request
        raise
    log.info("✓ Ходов в разборе: %d", len(moves))
    job.save_moves(moves)
    job.status = "analyzed"
    job.save()
    return moves


def analysis_context(job: Job) -> str:
    """Everything the LLM should know besides the transcript: the author's note, the confirmed
    characters, the reviewed scene summary and the notable sounds."""
    parts = [job.context.strip()]
    try:
        parts.append(cast.context_text(cast.JobCast.load(job.dir)))
        parts.append(summary.context_text(summary.Summary.load(job.dir)))
        if sounds.Sounds.exists(job.dir):
            parts.append(sounds.context_text(sounds.Sounds.load(job.dir), job.names))
    except Exception as e:  # a damaged side file must not stop the analysis
        log.debug("analysis context: %s", e)
    return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------- sounds and summary
def step_sounds(job: Job) -> sounds.Sounds:
    """Notable sound events (the person's edits survive a new detection)."""
    log.info("▶ Звуки и подача речи")
    import make_assets

    make_assets.ensure_sound_model()
    segments = analyzer.load_transcript(job.transcript) if job.transcript.exists() else []
    hints = job.load_speakers()
    new = sounds.detect(job.clip, segments, hints, label_sides(job, hints))
    snd = sounds.merge_redetected(sounds.Sounds.load(job.dir), new) if sounds.Sounds.exists(job.dir) else new
    snd.save(job.dir)
    return snd


def refresh_sound_speakers(job: Job) -> None:
    """After the cast or the speakers changed: re-tie the automatic vocal events to characters."""
    snd = sounds.Sounds.load(job.dir)
    hints = job.load_speakers()
    segments = analyzer.load_transcript(job.transcript) if job.transcript.exists() else []
    label_of = {h.id: h.label for h in hints.segments} if hints else {}
    conf_of = {h.id: h.conf for h in hints.segments} if hints else {}
    sides = label_sides(job, hints)
    for e in snd.events:
        if not e.edited and e.category in ("voice", "delivery") and e.kind != "pause":
            e.speaker = sounds._speaker_for(e, segments, label_of, conf_of, sides)
    snd.save(job.dir)


def step_summary(job: Job, provider_name: str | None = None, model: str | None = None) -> summary.Summary:
    """The structured scene summary from the LLM; sections the person edited are kept.
    Raises the provider's error as is (the UI then offers another model)."""
    log.info("▶ Сводка сцены (%s)", job.llm_label if not provider_name else f"{provider_name} · {model or ''}".strip(" ·"))
    segments = analyzer.load_transcript(job.transcript)
    hints = job.load_speakers()
    sides = label_sides(job, hints)
    label_name = {lab: job.names[sd] for lab, sd in sides.items() if sd in job.names} or \
        {lab: f"голос {lab}" for lab in speakers.LABELS}  # sides unknown: at least tell the voices apart
    label_of = {h.id: h.label for h in hints.confident(0.7)} if hints else {}
    jc = cast.JobCast.load(job.dir)
    known = {n for n in job.names.values() if n not in ("Белые", "Чёрные")}
    if jc is not None:
        known |= {p.name for p in jc.trusted().persons if p.name}
    snd = sounds.Sounds.load(job.dir) if sounds.Sounds.exists(job.dir) else None
    try:
        provider = llm.get_provider(provider_name or job.llm_provider, (model if provider_name else job.llm_model) or None)
        new = summary.generate(
            segments, provider, label_name=label_name, label_of=label_of, title=job.source_title,
            cast_line=cast.context_text(jc), sounds_line=sounds.context_text(snd, job.names) if snd else "",
            note=job.context.strip(), known_names=known, duration=job.clip_duration() or 0.0,
        )
    except Exception as e:
        e.ai_stage = "summary"
        raise
    new.model = getattr(provider, "label", "") or f"{provider_name or job.llm_provider} · {model or job.llm_model}".strip(" ·")
    merged = summary.merge(summary.Summary.load(job.dir), new)
    merged.save(job.dir)
    return merged


def needs_summary_review(job: Job) -> bool:
    s = summary.Summary.load(job.dir)
    return s is not None and s.status != "accepted"


def step_avatars(job: Job, moves: list[analyzer.Move]) -> None:
    times: dict[str, list[float]] = {"white": [], "black": []}
    for m in moves:  # sample frames while each side is speaking
        times[m.speaker] += [m.start + (m.end - m.start) * k for k in (0.25, 0.5, 0.75)]
    va = load_vision(job)
    person_for_side: dict[str, int] = {}
    hints = job.load_speakers()
    jc = cast.JobCast.load(job.dir)
    if va is not None and cast.sides_known(jc):  # confirmed faces: white is person 0, black person 1
        person_for_side = {"white": 0, "black": 1}
    elif va is not None and hints is not None and hints.persons.get("A") is not None:
        for label, side in speakers.label_sides(hints, moves).items():
            if hints.persons.get(label) in va.persons:
                person_for_side[side] = hints.persons[label]
    ingest.prepare_avatars(job.dir, job.names, job.avatar_inputs, va, times, person_for_side)


def step_render(job: Job, progress: Progress = _noop) -> Path:
    moves = job.load_moves()
    if not all(p.exists() for p in job.avatars.values()):
        step_avatars(job, moves)
    log.info("▶ Рендер видео")
    progress(0.0, "Анализ кадра")
    va = load_vision(job) if config.VIDEO_MODE == "smart" else None
    tmp = job.final.with_suffix(".tmp.mp4")
    st = vstyle.load_job_style(job.dir)
    vstyle.ensure_font(st.font_family)
    renderer.render_video(
        job.clip, moves, job.names, job.avatars, tmp, progress=lambda f: progress(f, "Рендер видео"),
        options=job.render_options(), vision=va, speaker_at=speakers.speaker_at(job.load_speakers()),
        style=st, segments=render_segments(job, st), tags=render_tags(job, st),
    )
    tmp.replace(job.final)
    job.status = "rendered"
    job.error = ""
    job.save()
    log.info("✓ Видео готово: %s (%s)", job.final, ingest.fmt_duration(ingest.probe(job.final).duration))
    return job.final


def render_segments(job: Job, st: vstyle.VideoStyle) -> list | None:
    if not st.subtitles or not job.transcript.exists():
        return None
    return analyzer.load_transcript(job.transcript)


def render_tags(job: Job, st: vstyle.VideoStyle) -> list | None:
    if not st.sound_tags or not sounds.Sounds.exists(job.dir):
        return None
    return sounds.render_tags(sounds.Sounds.load(job.dir), job.names)


STAGES = ("faces", "summary")   # where run_all can stop for the person's review


def run_all(job: Job, progress: Progress = _noop, until: str | None = None) -> Path | None:
    """Runs every stage that has not produced its output yet (resumable). `until` stops after
    "faces" (the characters dialog is next) or "summary" (the summary review is next)."""
    try:
        if not job.clip.exists():
            progress(0.02, "Проверка источника")
            step_ingest(job, lambda f, d: progress(0.02 + 0.12 * f, d))
        if not job.transcript.exists():
            progress(0.15, "Распознавание речи")
            step_transcribe(job)
        if config.DIARIZATION and not (job.dir / cast.FILE).exists():
            progress(0.2, "Лица в кадре")
            step_faces(job)
        if until == "faces":
            return None
        if not job.speakers_file.exists() and config.DIARIZATION:
            progress(0.25, "Кто говорит")
            step_speakers(job)
        if not sounds.Sounds.exists(job.dir):
            progress(0.28, "Звуки")
            try:
                step_sounds(job)
            except Exception as e:  # sounds are extra context: never fail the video because of them
                log.warning("Не удалось найти звуковые события (%s)", e)
        if job.mode != "manual" and not summary.Summary.exists(job.dir) and analyzer.load_transcript(job.transcript):
            progress(0.3, "Сводка сцены")
            if until == "summary":
                step_summary(job)  # the review stage: the UI handles a failed model
            else:
                try:
                    step_summary(job)
                except Exception as e:  # the summary helps the analysis but is not required for it
                    log.warning("Сводка сцены не получилась (%s) — разбор пойдёт без неё", errors.explain(e).title)
        if until == "summary":
            return None
        if not job.moves_file.exists():
            if job.mode == "manual":
                raise ValueError("Ходы ещё не собраны: откройте «Ручной разбор» и нажмите «Собрать ходы из разбора».")
            progress(0.35, "Разбор ходов нейросетью")
            moves = step_analyze(job)
        else:
            moves = job.load_moves()
        progress(0.5, "Аватарки")
        step_avatars(job, moves)
        return step_render(job, lambda f, d: progress(0.55 + 0.45 * f, d))
    except BaseException as e:
        job.status, job.error = "failed", errors.explain(e).title
        job.save()
        raise


# files that depend on the clip's content: a new cut invalidates all of them
CLIP_DERIVED = ("clip.mp4", "transcript.json", "speakers.json", "vision.json", "moves.json", "final.mp4", "faces", "avatars",
                "cast.json")  # faces are proposed again (the project still knows the characters)


def recut(job: Job, start: str | None, end: str | None) -> bool:
    """Change the job's in/out points like in a video editor. Returns True if the
    cut changed (the clip and everything derived from it are then rebuilt from the
    cached source by the next run_all; nothing is downloaded again)."""
    start, end = (start or "").strip() or None, (end or "").strip() or None
    t0, t1 = ingest.parse_time(start), ingest.parse_time(end)
    if t0 is not None and t1 is not None and t1 <= t0:
        raise ValueError("Конец фрагмента должен быть позже начала.")
    old_t0, old_t1 = ingest.parse_time(job.start), ingest.parse_time(job.end)
    if (old_t0, old_t1) == (t0, t1):
        return False
    delta = (old_t0 or 0.0) - (t0 or 0.0)  # clip time of a moment changes by this much
    keep_moves = job.mode == "manual" and job.moves_file.exists()
    if keep_moves:  # the person's moves follow the new cut instead of being thrown away
        shutil.copy(job.moves_file, job.dir / "moves.before_recut.json")
        moved = []
        for m in job.load_moves():
            m.start, m.end = max(0.0, m.start + delta), m.end + delta
            if m.end > m.start + 0.3:
                moved.append(m)
        job.save_moves(analyzer.sanitize_moves(moved, None))
    if dialogue.Dialogue.exists(job.dir):
        d = dialogue.Dialogue.load(job.dir)
        shutil.copy(job.dir / dialogue.FILE, job.dir / "dialogue.before_recut.json")
        if dialogue.shift(d, delta):
            log.info("Часть реплик осталась за пределами нового фрагмента")
        d.save(job.dir)
    if sounds.Sounds.exists(job.dir):  # the person's events follow the cut; automatic ones are found again
        snd = sounds.Sounds.load(job.dir)
        kept = []
        for e in snd.events:
            if e.edited or e.source == "user":
                e.start, e.end = max(0.0, e.start + delta), e.end + delta
                if e.end > e.start + 0.1:
                    kept.append(e)
        if kept:
            sounds.Sounds(kept, snd.method, snd.notes).save(job.dir)
        else:
            (job.dir / sounds.FILE).unlink(missing_ok=True)
    s_old = summary.Summary.load(job.dir)
    if s_old is not None:
        for it in s_old.moments:
            if it.time is not None:
                it.time = round(it.time + delta, 1)
        s_old.moments = [it for it in s_old.moments if it.time is None or it.time >= 0]
        s_old.status = "draft"
        s_old.save(job.dir)
    for name in CLIP_DERIVED:
        if name == "moves.json" and keep_moves:
            continue
        path = job.dir / name
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    if not keep_moves:
        (job.dir / moves_edit.BACKUP).unlink(missing_ok=True)
    job.start, job.end, job.status, job.error = start, end, "new", ""
    job.save()
    what = "клип и аватарки" if keep_moves else "клип, речь и разбор"
    log.info("✂ Новая обрезка %s–%s: %s будут пересчитаны", start or "0:00", end or "конец", what)
    return True


# ---------------------------------------------------------------- manual analysis
def prepare_manual(job: Job, transcribe: bool = True, progress: Progress = _noop) -> dialogue.Dialogue:
    """Everything a manual analysis needs, without the LLM: the clip, optionally the
    transcript (+ who speaks) as draft lines; then dialogue.json (created once)."""
    if not job.clip.exists():
        progress(0.02, "Проверка источника")
        step_ingest(job, lambda f, d: progress(0.02 + 0.26 * f, d))
    if transcribe and not job.transcript.exists():
        progress(0.3, "Распознавание речи")
        step_transcribe(job)
    if transcribe and job.transcript.exists() and config.DIARIZATION and not (job.dir / cast.FILE).exists():
        progress(0.5, "Лица в кадре")
        step_faces(job)
    if transcribe and job.transcript.exists() and not job.speakers_file.exists() and config.DIARIZATION:
        progress(0.6, "Кто говорит")
        step_speakers(job)
    if transcribe and job.transcript.exists() and not sounds.Sounds.exists(job.dir):
        progress(0.7, "Звуки")
        try:
            step_sounds(job)
        except Exception as e:
            log.warning("Не удалось найти звуковые события (%s)", e)
    if dialogue.Dialogue.exists(job.dir):
        return dialogue.Dialogue.load(job.dir)
    return import_dialogue(job)


def import_dialogue(job: Job) -> dialogue.Dialogue:
    """(Re)build dialogue.json from what the job has: transcript (+speakers, +moves), or moves only."""
    moves = job.load_moves() if job.moves_file.exists() else []
    if job.transcript.exists():
        d, notes = dialogue.from_transcript(analyzer.load_transcript(job.transcript), job.load_speakers(), moves)
        for n in notes:
            log.info("Реплики: %s", n)
    elif moves:
        d = dialogue.from_moves(moves)
    else:
        d = dialogue.Dialogue()
    d.save(job.dir)
    log.info("✓ Реплик: %d, ключевых моментов: %d", len(d.lines), len(d.moments))
    return d


def save_manual_moves(job: Job, moves: list[analyzer.Move]) -> None:
    """Moves built in an editor: the first edit keeps the original analysis as a backup."""
    moves_edit.ensure_backup(job.dir)
    job.save_moves(moves)
    job.status = "analyzed"
    job.save()


def preview_avatars(job: Job) -> dict[str, Path]:
    """Avatars for card previews: the real ones once made, placeholders before that."""
    if all(p.exists() for p in job.avatars.values()):
        return job.avatars
    out = job.dir / "preview"
    out.mkdir(parents=True, exist_ok=True)
    paths = {}
    for side in ("white", "black"):
        paths[side] = out / f"{side}.png"
        ingest.placeholder_avatar(job.names[side], side).save(paths[side])  # cheap; follows renamed players
    return paths


def update_moves_json(job: Job, text: str) -> list[analyzer.Move]:
    """Validate user-edited JSON and store it (raises on invalid input)."""
    duration = ingest.probe(job.clip).duration if job.clip.exists() else None
    moves = analyzer.parse_moves(text, duration)
    job.save_moves(moves)
    return moves
