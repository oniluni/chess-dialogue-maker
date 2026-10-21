#!/usr/bin/env python3
"""Chess Dialogue Maker — CLI entry point.

  python app.py ui                                    # web UI (Gradio)
  python app.py run --url URL --start 1:02 --end 1:45 --white "Гус Фринг" --black "Хэнк"
  python app.py run --search "breaking bad i am the danger" --white Уолтер --black Скайлер
  python app.py run --file clip.mp4 --white A --black B --review web
  python app.py run --url URL --llm gemini --model gemini-2.5-flash --no-pause-on-move --format landscape
  python app.py run --search "уолтер скайлер" --shorts --white Уолтер --black Скайлер   # pick from Shorts
  python app.py search "запрос"                       # find videos on YouTube
  python app.py search --shorts --max-duration 60 --sort views "запрос"   # YouTube Shorts
  python app.py describe "Диалог в ресторане. Бесславные ублюдки" [--kind film]   # TikTok description
  python app.py styles                                # visual presets (use with --style on run / render)
  python app.py models [ollama|gemini]                # list available LLM models
  python app.py jobs                                  # list jobs
  python app.py resume <job>                          # continue a failed job from where it stopped
  python app.py analyze <job> [--llm ... --model ...] # ask the LLM again + render
  python app.py render <job> [--typing-cps 40 ...]    # re-render only (after editing moves.json or settings)
  python app.py publish <job> [--caption ...]         # upload to TikTok
  python app.py assets                                # icons, SFX, fonts, face/voice models
"""
from __future__ import annotations

import argparse
import logging
import sys

import config
import console
import describe
import errors
import pipeline
import publisher
import review
from pipeline import Job

log = logging.getLogger("app")
_current_job: str | None = None  # for the "how to resume" hint after a failure


def _track(job: Job) -> Job:
    global _current_job
    _current_job = job.id
    return job


def _find(query: str, shorts: bool = False, max_duration: float | None = None, sort: str = "relevance"):
    import search

    return search.search_shorts(query, max_duration=max_duration, sort=sort) if shorts else search.search_videos(query)


def _print_videos(videos) -> None:
    for i, v in enumerate(videos, 1):
        print(f"  {i:>2}. {'[Shorts] ' if v.is_short else ''}{v.caption.replace(chr(10), '   ')}\n      {v.url}")


def _pick_video(query: str, shorts: bool = False):
    videos = _find(query, shorts)
    if not videos:
        raise ValueError(f"По запросу «{query}» ничего не найдено — уточните запрос.")
    _print_videos(videos)
    while True:
        choice = input("Номер ролика (Enter = 1, q = выход): ").strip().lower()
        if choice == "q":
            raise KeyboardInterrupt
        if not choice:
            return videos[0]
        if choice.isdigit() and 1 <= int(choice) <= len(videos):
            return videos[int(choice) - 1]
        print("  введите номер из списка")


def cmd_run(a) -> None:
    picked = _pick_video(a.search, a.shorts) if a.search else None
    source = a.url or a.file or picked.url
    meta = dict(source_title=picked.title, source_kind=picked.kind, source_channel=picked.channel) if picked else {}
    job = _track(pipeline.create_job(
        source, a.start, a.end, a.white, a.black, a.white_avatar, a.black_avatar, a.context, **meta, **_options(a),
    ))
    if a.caption:
        job.caption = a.caption
    if a.hashtags:
        job.hashtags = a.hashtags
    if getattr(a, "allow_full_download", False):
        job.allow_full_download = True
    job.save()
    _apply_style(job, a)
    log.info("Задание %s: %s", job.id, job.dir)
    _run_all(job)
    _after(job, a)


def _run_all(job: Job) -> None:
    """run_all; when only the whole video can be downloaded (or nothing is selected in a long
    one), ask in the terminal instead of downloading it silently."""
    import ingest

    while True:
        try:
            pipeline.run_all(job, console.progress_bar, review=False)  # CLI: the best answer, as before
            return
        except (ingest.PartialUnavailable, ingest.NeedsRange) as e:
            log.warning("%s %s", e.title, e.hint)
            if job.allow_full_download or not sys.stdin.isatty():
                raise ingest.DownloadError(e.kind, e.title, e.hint + " Запустите с --allow-full-download, чтобы разрешить "
                                           "скачать весь ролик.") from e
            if input("Скачать весь ролик и вырезать фрагмент на компьютере? [y/N] ").strip().lower() not in ("y", "yes", "д", "да"):
                raise
            job = Job.load(job.id)
            job.allow_full_download, job.status, job.error = True, "new", ""
            job.save()


def cmd_resume(a) -> None:
    job = _load_with_options(a)
    log.info("Продолжаю задание %s (готовые этапы пропускаются)", job.id)
    if getattr(a, "allow_full_download", False):
        job.allow_full_download = True
        job.save()
    _run_all(job)
    _after(job, a)


def _after(job: Job, a) -> None:
    review_mode = getattr(a, "review", "none")
    if review_mode == "cli":
        review.cli_review(job)
    elif review_mode == "web":
        log.info("Откройте веб-интерфейс и выберите задание %s в «Прошлые задания»", job.id)
        review.launch_ui(port=a.port)


def cmd_jobs(_a) -> None:
    for j in pipeline.list_jobs():
        opts = f"{j.output_format}{' пауза' if j.pause_on_move else ''}{' звук' if j.sfx_enabled else ''}"
        print(f"{j.id:<18} {j.status:<12} {opts:<24} {j.names['white']} vs {j.names['black']}  {j.source[:50]}")


def cmd_search(a) -> None:
    videos = _find(" ".join(a.query), a.shorts, a.max_duration, a.sort)
    if not videos:
        print("Ничего не найдено — попробуйте другой запрос.")
    _print_videos(videos)


def cmd_describe(a) -> None:
    s = describe.suggest(" ".join(a.title), kind=a.kind or "", channel=a.channel or "")
    print(s.text)
    kind = "не определён"
    if s.kind:
        how = {"user": "указан", "title": "из названия", "meta": "по описанию ролика"}.get(s.kind_source, "")
        kind = f"{describe.KINDS[s.kind][0].lower()} ({how})"
    print(f"\n  произведение: {s.work or '—'} · сцена: {s.scene or '—'} · первоисточник: {kind}")
    for note in s.notes:
        print(f"  · {note}")


def cmd_models(a) -> None:
    import llm

    for name in [a.provider] if a.provider else config.LLM_PROVIDERS:
        s = llm.spec(name)
        print(f"{name} — {s.label} · {llm.key_status(name)}")
        try:
            models = llm.list_models(name)
        except Exception as e:
            print(f"    {errors.explain(e)}")
            continue
        for label, m in models:
            print(f"  {'*' if m == s.default_model else ' '} {label}")


def _options(a) -> dict:
    opts = {k: getattr(a, k, None) for k in (*pipeline.OPTION_FIELDS, "llm_provider", "llm_model")}
    import make_assets
    for key, kind in (("sfx_typing_set", "typing"), ("sfx_pause_set", "pause")):
        sets = make_assets.sfx_sets(kind)
        if opts.get(key) is not None and opts[key] not in sets:
            raise SystemExit(f"Нет набора звуков «{opts[key]}». Есть: {', '.join(sets)}.")
    return opts


def _load_with_options(a) -> Job:
    job = _track(Job.load(a.job))
    job.set_options(**_options(a))
    job.save()
    return job


def cmd_analyze(a) -> None:
    job = _load_with_options(a)
    pipeline.step_analyze(job, review=False)
    pipeline.step_render(job, console.progress_bar)
    _after(job, a)


def _apply_style(job: Job, a) -> None:
    name = getattr(a, "style", None)
    if not name:
        return
    import style

    presets = style.all_presets()
    if name not in presets:
        raise ValueError(f"Нет пресета «{name}». Доступны: {', '.join(presets)}.")
    style.save_job_style(job.dir, presets[name])
    log.info("Стиль видео: %s", name)


def cmd_styles(_a) -> None:
    import style

    builtin = set(style.PRESETS)
    for name, st in style.all_presets().items():
        extra = [x for x, on in (("субтитры", st.subtitles), ("метки звуков", st.sound_tags)) if on]
        print(f"  {name:<14} {'встроенный' if name in builtin else 'ваш':<10} шрифт {st.font_family}, переход {st.transition}"
              + (f", {', '.join(extra)}" if extra else ""))


def cmd_render(a) -> None:
    job = _load_with_options(a)
    _apply_style(job, a)
    pipeline.update_moves_json(job, job.moves_file.read_text())  # validate manual edits
    pipeline.step_render(job, console.progress_bar)
    _after(job, a)


def cmd_publish(a) -> None:
    job = _track(Job.load(a.job))
    caption = a.caption if a.caption is not None else job.caption
    hashtags = a.hashtags if a.hashtags is not None else job.hashtags
    log.info("Описание: %s", publisher.build_description(caption, hashtags))
    log.info(review.approve_and_publish(job, caption, hashtags))


def cmd_assets(a) -> None:
    import make_assets

    make_assets.build(fonts=not a.no_fonts, models=not a.no_models)


def add_llm_options(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("LLM")
    g.add_argument("--llm", dest="llm_provider", choices=config.LLM_PROVIDERS, default=None,
                   help=f"provider for the analysis (default {config.LLM_PROVIDER})")
    g.add_argument("--model", dest="llm_model", default=None, help="model name (see `app.py models`)")


def add_render_options(p: argparse.ArgumentParser) -> None:
    """Render settings; omitted flags keep the job's (or .env) value."""
    g = p.add_argument_group("render")
    g.add_argument("--pause-on-move", action=argparse.BooleanOptionalAction, default=None,
                   help=f"freeze the video while each move is explained (default {config.PAUSE_ON_MOVE})")
    g.add_argument("--typing-cps", type=float, default=None, metavar="N",
                   help=f"typewriter speed, characters per second (default {config.TYPING_CPS:g})")
    g.add_argument("--read-hold", type=float, default=None, metavar="SEC",
                   help=f"reading time after the text is typed (default {config.READ_HOLD:g})")
    g.add_argument("--sfx", dest="sfx_enabled", action=argparse.BooleanOptionalAction, default=None,
                   help=f"all sound effects on/off (default {config.SFX_ENABLED})")
    g.add_argument("--sfx-volume", type=float, default=None, metavar="0..1",
                   help=f"sound-effect volume (default {config.SFX_VOLUME})")
    g.add_argument("--sfx-pause", action=argparse.BooleanOptionalAction, default=None, help="pause sound")
    g.add_argument("--sfx-unpause", action=argparse.BooleanOptionalAction, default=None, help="resume sound")
    g.add_argument("--sfx-typing", action=argparse.BooleanOptionalAction, default=None, help="typewriter ticks")
    g.add_argument("--sfx-typing-set", default=None, metavar="SET",
                   help="typing sound: soft, typewriter, mechanical, pencil, bubble (or a folder in assets/sfx/typing)")
    g.add_argument("--sfx-pause-set", default=None, metavar="SET",
                   help="pause/resume sound: wood, clock, whoosh, chime, tape (or a folder in assets/sfx/pause)")
    g.add_argument("--style", default=None, metavar="PRESET", help="visual preset (see `app.py styles`)")
    g.add_argument("--format", dest="output_format", choices=config.OUTPUT_FORMATS, default=None,
                   help=f"output: tiktok 1080x1920, original aspect, landscape 1920x1080 (default {config.OUTPUT_FORMAT})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="app.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true", help="technical details and full tracebacks")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ui", help="launch the Gradio web UI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=7860)
    p.add_argument("--share", action="store_true", help="public gradio.live link")
    p.set_defaults(fn=lambda a: review.launch_ui(a.host, a.port, a.share))

    p = sub.add_parser("run", help="full pipeline: ingest → transcribe → analyze → render → review")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="YouTube (or any yt-dlp) link")
    src.add_argument("--file", help="local video file")
    src.add_argument("--search", help="search YouTube and pick a result interactively")
    p.add_argument("--shorts", action="store_true", help="with --search: pick from YouTube Shorts")
    p.add_argument("--allow-full-download", action="store_true",
                   help="if only the selected fragment cannot be downloaded, download the whole video (never by default)")
    p.add_argument("--start", help="start timecode (SS, MM:SS, HH:MM:SS)")
    p.add_argument("--end", help="end timecode")
    p.add_argument("--white", default="Белые", help="player 1 name (Белые)")
    p.add_argument("--black", default="Чёрные", help="player 2 name (Чёрные)")
    p.add_argument("--white-avatar", help="image for player 1 (default: face from video)")
    p.add_argument("--black-avatar", help="image for player 2")
    p.add_argument("--context", default="", help="scene description to help the LLM")
    p.add_argument("--caption")
    p.add_argument("--hashtags")
    p.add_argument("--review", choices=["cli", "web", "none"], default="cli")
    p.add_argument("--port", type=int, default=7860)
    add_llm_options(p)
    add_render_options(p)
    p.set_defaults(fn=cmd_run)

    sub.add_parser("jobs", help="list jobs").set_defaults(fn=cmd_jobs)

    p = sub.add_parser("search", help="search YouTube for a scene (videos or Shorts)")
    p.add_argument("query", nargs="+", help="words; for Shorts also «@channel words»")
    p.add_argument("--shorts", action="store_true", help="search YouTube Shorts")
    p.add_argument("--max-duration", type=float, default=None, metavar="SEC", help="Shorts: at most this long")
    p.add_argument("--sort", choices=["relevance", "views", "date"], default="relevance", help="Shorts: order")
    p.set_defaults(fn=cmd_search)

    sub.add_parser("styles", help="list the visual presets").set_defaults(fn=cmd_styles)

    p = sub.add_parser("describe", help="suggest the TikTok description for a source title")
    p.add_argument("title", nargs="+")
    p.add_argument("--kind", choices=list(describe.KINDS), help="the work's kind (default: detect)")
    p.add_argument("--channel", help="channel name (removed from the title)")
    p.set_defaults(fn=cmd_describe)

    p = sub.add_parser("models", help="list available LLM models")
    p.add_argument("provider", nargs="?", choices=config.LLM_PROVIDERS)
    p.set_defaults(fn=cmd_models)

    for name, fn, hlp in (
        ("resume", cmd_resume, "continue a job from the first unfinished stage"),
        ("analyze", cmd_analyze, "re-run the LLM analysis and render"),
        ("render", cmd_render, "re-render only (after editing moves.json or render settings)"),
    ):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("job")
        p.add_argument("--review", choices=["cli", "none"], default="cli" if name != "resume" else "none")
        if name == "resume":
            p.add_argument("--allow-full-download", action="store_true",
                           help="download the whole video if the fragment alone cannot be downloaded")
        add_llm_options(p)
        add_render_options(p)
        p.set_defaults(fn=fn)

    p = sub.add_parser("publish", help="upload a rendered job to TikTok")
    p.add_argument("job")
    p.add_argument("--caption")
    p.add_argument("--hashtags")
    p.set_defaults(fn=cmd_publish)

    p = sub.add_parser("assets", help="generate icons and SFX, download fonts and face/voice models")
    p.add_argument("--no-fonts", action="store_true")
    p.add_argument("--no-models", action="store_true")
    p.set_defaults(fn=cmd_assets)

    a = ap.parse_args(argv)
    console.setup(a.verbose)
    try:
        a.fn(a)
    except KeyboardInterrupt:
        console.end_progress()
        log.warning("Остановлено.%s", f" Продолжить: python app.py resume {_current_job}" if _current_job else "")
        return 130
    except Exception as e:
        console.end_progress()
        log.debug("traceback", exc_info=True)
        why = errors.explain(e)
        log.error(why.title)
        if why.hint:
            print(f"           Что делать: {why.hint}", file=sys.stderr)
        if _current_job and a.cmd in ("run", "resume"):
            print(f"           Продолжить с места сбоя: python app.py resume {_current_job}", file=sys.stderr)
        elif _current_job and a.cmd in ("analyze", "render"):
            print(f"           Повторить: python app.py {a.cmd} {_current_job}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
