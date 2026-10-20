"""Step 4: human-in-the-loop review — Gradio web UI and a terminal fallback."""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import config
import llm
import console
import describe
import design
import errors
import ingest
import pipeline
import prefs
import publisher
import renderer
import search
import ui_parts as up
from ingest import fmt_duration
from pipeline import Job

log = logging.getLogger(__name__)


# ================================================================ shared actions
def approve_and_publish(job: Job, caption: str, hashtags: str) -> str:
    if not job.final.exists():
        raise RuntimeError("Нет отрендеренного видео")
    job.caption, job.hashtags = caption, hashtags
    job.save()
    publisher.publish(job.final, publisher.build_description(caption, hashtags))
    job.status = "published"
    job.save()
    return "✅ Опубликовано в TikTok"


def reject(job: Job) -> str:
    job.status = "rejected"
    job.save()
    return "❌ Отклонено. Видео сохранено в " + str(job.final)


# ================================================================ terminal review
def open_player(path) -> None:
    for player in ("mpv", "vlc", "xdg-open"):
        if shutil.which(player):
            args = [player, "--loop=inf", str(path)] if player == "mpv" else [player, str(path)]
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    print(f"(плеер не найден — откройте вручную: {path})")


def cli_review(job: Job) -> None:
    print(f"\n🎬 Готово: {job.final}")
    open_player(job.final)
    while True:
        print(
            f"\nОписание: {publisher.build_description(job.caption, job.hashtags)}\n"
            "[a] одобрить и опубликовать   [e] править moves.json и перерендерить\n"
            "[c] изменить описание         [h] изменить хэштеги\n"
            "[p] открыть видео снова       [r] отклонить   [q] выйти (сохранить)"
        )
        choice = input("> ").strip().lower()
        try:
            if choice == "a":
                print(approve_and_publish(job, job.caption, job.hashtags))
                return
            if choice == "e":
                editor = os.environ.get("EDITOR") or shutil.which("nano") or shutil.which("vi") or "vi"
                subprocess.call([*shlex.split(editor), str(job.moves_file)])
                pipeline.update_moves_json(job, job.moves_file.read_text())
                pipeline.step_render(job, console.progress_bar)
                open_player(job.final)
            elif choice == "c":
                job.caption = input("Описание: ").strip()
                job.save()
            elif choice == "h":
                job.hashtags = input("Хэштеги (через пробел): ").strip()
                job.save()
            elif choice == "p":
                open_player(job.final)
            elif choice == "r":
                print(reject(job))
                return
            elif choice == "q":
                print(f"Задание сохранено: {job.id}  (python app.py publish {job.id})")
                return
        except Exception as e:
            why = errors.explain(e)
            print(f"⚠️  {why.title}\n    {why.hint}" if why.hint else f"⚠️  {why.title}")


# ================================================================ web UI
FORMATS = [("tiktok (9:16)", "tiktok"), ("original", "original"), ("landscape (16:9)", "landscape")]
PROVIDERS = [(llm.SPECS[p].label, p) for p in llm.PROVIDER_IDS]
AVG_MOVE_CHARS = 100  # "typical" explanation length for the pause-duration hint



def short_error(e: BaseException) -> str:
    """One-line Russian explanation of an error (full trace goes to the log with -v)."""
    return errors.explain(e).title


def _source_from(value) -> tuple[str, bool]:
    """MultimodalTextbox value -> (uploaded file path or typed text, is_file)."""
    if not value:
        return "", False
    if isinstance(value, str):
        return value.strip(), False
    files = value.get("files") or []
    if files:
        f = files[-1]
        return (f if isinstance(f, str) else (f.get("path") or "")), True
    return (value.get("text") or "").strip(), False


def _looks_like_query(text: str) -> bool:
    """Not a link and not an existing local file -> treat it as a YouTube search."""
    return bool(text) and not search.is_url(text) and not Path(text).expanduser().exists()


STATUS_RU = {"rendered": "готово", "published": "опубликовано", "failed": "ошибка", "analyzed": "разобрано",
             "rejected": "отклонено", "new": "в работе", "ingested": "в работе", "transcribed": "в работе"}
NEW_TITLE = "# Новое видео\nДиалог из ролика превращается в разбор ходов в стиле Chess.com — со стоп-кадрами, оценкой и озвучкой."
HEADER_HTML = f"""<div class="cdm-brand">
  <button class="cdm-hamburger" aria-label="Меню">{design.svg("menu", size=20)}</button>
  <a class="cdm-logo" href="./"><span class="cdm-logo-mark">!!</span>Chess Dialogue Maker<span class="cdm-logo-sub">· студия разборов</span></a>
</div>"""


def job_when(job: Job) -> str:
    """'20261019-034630' -> '19.10 03:46'."""
    d = job.id
    return f"{d[6:8]}.{d[4:6]} {d[9:11]}:{d[11:13]}" if len(d) >= 13 and d[8] == "-" else d


def job_title(job: Job) -> str:
    fmt = dict((v, k) for k, v in FORMATS).get(job.output_format, job.output_format)
    return (f"# {job.names['white']} vs {job.names['black']}\n"
            f"Задание `{job.id}` · {STATUS_RU.get(job.status, job.status)} · {job.llm_label} · {fmt}")


def provider_info(name: str) -> str:
    s = llm.SPECS[name]
    return f"{llm.key_status(name)} · {s.note}"


def pause_hint(cps: float, hold: float) -> str:
    opts = renderer.RenderOptions(typing_cps=cps, read_hold=hold)
    total = renderer.pause_duration(AVG_MOVE_CHARS, opts)
    typing = renderer.typing_time(AVG_MOVE_CHARS, cps)
    return f"Ход средней длины ({AVG_MOVE_CHARS} символов): печать {typing:.1f} с + чтение {hold:g} с ≈ пауза **{total:.1f} с**"


STAGE_ICONS = (("загруз", "📥"), ("скачив", "📥"), ("распозн", "🎙️"), ("кто говорит", "👥"), ("разбор", "🧠"),
               ("аватар", "🖼️"), ("анализ кадра", "🎯"), ("рендер", "🎬"), ("публикац", "📤"), ("обрез", "✂️"))


def stage_icon(desc: str) -> str:
    d = desc.lower()
    return next((icon for key, icon in STAGE_ICONS if key in d), "⏳")


def progress_html(frac: float | None) -> str:
    """The thin progress bar of the bottom dock ("" = hidden)."""
    if frac is None:
        return ""
    pct = max(0.0, min(1.0, frac)) * 100
    return (f'<div class="cdm-progress" role="progressbar" aria-valuenow="{pct:.0f}" aria-valuemin="0" aria-valuemax="100">'
            f'<div class="cdm-progress-track"><div class="cdm-progress-fill" style="width:{pct:.1f}%"></div></div>'
            f'<span class="cdm-progress-pct">{pct:.0f}%</span></div>')


class LiveRun:
    """Runs a pipeline action in a worker thread; the UI polls its stage, progress and log."""

    def __init__(self, action):
        self.action = action
        self.job: Job | None = None
        self.frac, self.desc = 0.0, "Запуск…"
        self.error: BaseException | None = None
        self.trace = ""
        self.log: console.RunLog | None = None
        self.thread = threading.Thread(target=self._main, daemon=True)
        self.thread.start()

    def progress(self, frac: float, desc: str) -> None:
        self.frac, self.desc = max(0.0, min(1.0, frac)), desc or self.desc

    def _main(self) -> None:
        self.log = console.RunLog(threading.get_ident())
        logging.getLogger().addHandler(self.log)
        try:
            self.action(self)
        except BaseException as e:
            self.error = e
            self.trace = "".join(traceback.format_exception(e))
            log.debug("traceback", exc_info=True)
            why = errors.explain(e)
            log.error("%s %s", why.title, why.hint)
        finally:
            logging.getLogger().removeHandler(self.log)

    def journal(self) -> str:
        return self.log.text() if self.log else ""

    def status(self) -> str:
        return f"{stage_icon(self.desc)} **{self.desc.rstrip('…. ')}…**"

    def bar(self) -> str:
        return progress_html(self.frac)

    def details(self) -> str:
        """Technical text of the failure (exception + traceback) for the «Подробности» spoiler."""
        return self.trace.strip() if self.error is not None else ""

    def failure(self, retry: str) -> str:
        why = errors.explain(self.error)
        msg = f"❌ **{why.title}**"
        if why.hint:
            msg += f"  \n{why.hint}"
        if self.job is not None:
            msg += f"  \nЗадание `{self.job.id}` сохранено — после исправления нажмите «{retry}»."
        return msg


PAGES = [("Создание видео", "create"), ("Ходы", "moves"), ("Ручной разбор", "manual"), ("Звуки", "sounds"),
         ("Стиль видео", "style")]
ANCHORS = {
    "create": [("sec-source", "Источник"), ("sec-players", "Игроки"), ("sec-summary", "Сводка сцены"), ("sec-llm", "Нейросеть"),
               ("sec-video", "Видео и звук")],
    "moves": [("moves-timeline", "Таймлайн"), ("moves-grid", "Ходы и форма")],
    "manual": [("sec-m-video", "Видео и таймлайн"), ("sec-m-lines", "Реплики"), ("sec-m-structure", "Структура разбора")],
    "sounds": [("sounds-timeline", "Таймлайн"), ("sounds-grid", "События")],
    "style": [("style-controls", "Настройки"), ("style-preview-col", "Превью")],
}
KIND_BADGE = {"short": ("Shorts", "short"), "video": ("Видео", "video"), "file": ("Файл", "file")}


def anchors_html(page: str) -> str:
    links = "".join(f'<a class="nav-link{" active" if i == 0 else ""}" href="#{a}">{t}</a>'
                    for i, (a, t) in enumerate(ANCHORS.get(page, [])))
    return f'<nav class="nav-tree nav-tree-sub">{links}</nav>' if links else ""


def source_card_html(title: str, url: str, kind: str = "", channel: str = "", duration: float | None = None,
                     views: int | None = None, thumb: str = "") -> str:
    """The chosen source: thumbnail, kind badge, title, channel / duration / views and a link to the original."""
    if not (title or url):
        return ""
    import html as _h

    kind = kind or ("short" if search.is_short_url(url) else "video" if search.is_url(url) else "file")
    label, cls = KIND_BADGE.get(kind, KIND_BADGE["video"])
    vid = re.search(r"(?:[?&]v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})", url or "")
    if not thumb and vid:
        thumb = f"https://i.ytimg.com/vi/{vid.group(1)}/{'oar2' if kind == 'short' else 'mqdefault'}.jpg"
    meta = " · ".join(x for x in (channel, fmt_duration(duration) if duration else "", search.fmt_views(views)) if x)
    img = f'<img src="{_h.escape(thumb)}" alt="">' if thumb else ""
    link = (f'<a href="{_h.escape(url)}" target="_blank" rel="noopener">Открыть оригинал {design.svg("external", size=12)}</a>'
            if search.is_url(url) else "")
    return (f'<div class="cdm-source{" is-short" if kind == "short" else ""}">{img}<div class="cdm-source-body">'
            f'<span class="cdm-kind cdm-kind-{cls}">{label}</span>'
            f'<div class="cdm-source-title">{_h.escape(title or url)}</div>'
            f'<div class="cdm-source-meta">{_h.escape(meta)}</div>{link}</div></div>')


def desc_hint(suggestion, edited: bool) -> str:
    if edited:
        return "✎ Описание изменено вручную — автоматические обновления его не перезапишут."
    kind = describe.KINDS.get(suggestion.kind, ("", ""))[0] if suggestion else ""
    src = {"user": "выбран вами", "title": "из названия", "meta": "из описания ролика"}.get(getattr(suggestion, "kind_source", ""), "")
    parts = ["✨ Описание собрано из названия"]
    if kind:
        parts.append(f"первоисточник: {kind.lower()} ({src})")
    if suggestion and suggestion.notes:
        parts.append(suggestion.notes[0])
    return " · ".join(parts) + ". Его можно править."


def build_ui():
    import gradio as gr

    import ui_ai
    import ui_cast
    import ui_download
    import ui_manual
    import ui_moves
    import ui_sounds
    import ui_style
    import ui_summary
    import ui_trim

    saved = prefs.load()

    def job_choices():
        return [(f"{j.names['white']} vs {j.names['black']} · {job_when(j)} · {STATUS_RU.get(j.status, j.status)}", j.id)
                for j in pipeline.list_jobs()]

    with gr.Blocks(title="Chess Dialogue Maker") as ui:
        job_id = gr.State(None)
        results = gr.State([])       # current search results (dicts)
        picked = gr.State(None)      # the search result chosen as the source (dict) until a job is created
        desc_auto = gr.State("")     # last automatic description shown in the field (edited = field differs)
        proposal = gr.State("")      # a new suggestion waiting for «Заменить»
        # the dock is drawn at the bottom, but every page streams into it: create it first, render it later
        status = gr.Markdown(elem_id="status", render=False)
        bar = gr.HTML("", elem_id="progress", container=False, padding=False, render=False)
        journal = gr.Textbox(label="Ход работы", lines=8, max_lines=8, autoscroll=True, interactive=False,
                             elem_id="journal", buttons=["copy"], placeholder="Здесь появится ход работы последнего запуска",
                             render=False)
        tech = gr.Textbox(label="Технические подробности ошибки", lines=8, max_lines=8, interactive=False,
                          elem_id="tech", buttons=["copy"], placeholder="Ошибок не было", render=False)

        # ---------------------------------------------------------------- glass header
        with gr.Row(elem_id="site-header"):
            gr.HTML(HEADER_HTML, elem_id="hdr-brand", container=False, padding=False)
            hdr_search = gr.Textbox(show_label=False, container=False, lines=1, max_lines=1, elem_id="hdr-search",
                                    placeholder="Найти видео…")
            gr.HTML('<div class="cdm-kbd"><kbd>Ctrl</kbd><kbd>K</kbd></div>', elem_id="hdr-kbd", container=False, padding=False)

        ai = ui_ai.build(gr)
        castui = ui_cast.build(gr)
        dlg = ui_download.build(gr)

        with gr.Row(elem_id="shell", equal_height=False):
            # outside the header: its backdrop-filter would trap a position:fixed overlay inside it
            gr.HTML('<div class="cdm-backdrop"></div>', elem_id="backdrop-wrap", container=False, padding=False)
            # ---------------------------------------------------------------- sidebar navigation
            with gr.Column(elem_id="sidebar", min_width=0):
                new_btn = gr.Button("Новое видео", icon=design.icon("plus", "text"), variant="secondary", size="sm",
                                    elem_id="nav-new")
                with gr.Column(elem_id="pages-group", min_width=0):
                    gr.HTML('<div class="nav-group-title">Страницы</div>', container=False, padding=False)
                    pages_nav = gr.Radio(PAGES, value="create", show_label=False, container=False, elem_id="pages-nav")
                    page_anchors = gr.HTML(anchors_html("create"), container=False, padding=False, elem_id="page-anchors")
                with gr.Column(elem_id="jobs-group", min_width=0):
                    gr.HTML('<div class="nav-group-title">Задания</div>', container=False, padding=False)
                    jobs_nav = gr.Radio(job_choices(), value=None, show_label=False, container=False, elem_id="jobs-nav")
                    resume_btn = gr.Button("Продолжить выбранное", icon=design.icon("play"), variant="secondary",
                                           size="sm", elem_id="resume-btn")

            with gr.Tabs(selected="create", elem_id="pages") as pages:
                # ============================================================ page: create
                with gr.Tab("Создание видео", id="create"):
                    with gr.Row(elem_id="create-grid", equal_height=False):
                        with gr.Column(elem_id="content", min_width=0):
                            page_title = gr.Markdown(NEW_TITLE, elem_id="page-title")

                            gr.Markdown("## Источник\nСсылка на ролик, видеофайл или поисковый запрос — по запросу откроется "
                                        "выбор роликов и Shorts.", elem_id="sec-source", elem_classes="sec")
                            with gr.Row(elem_id="src-row"):
                                source = gr.MultimodalTextbox(
                                    placeholder="Ссылка, поисковый запрос, видеофайл или .txt со ссылкой",
                                    file_types=None, sources=["upload"], submit_btn=False, show_label=False, lines=1,
                                    scale=5, elem_id="source",
                                )
                                search_btn = gr.Button("Найти", icon=design.icon("search"), variant="secondary", scale=0,
                                                       min_width=110)
                            source_card = gr.HTML("", elem_id="source-card", container=False, padding=False)
                            trim = gr.HTML("", elem_id="trim", container=False, padding=False)
                            trim_dl = gr.Button("Скачать для точной обрезки", icon=design.icon("download"), variant="secondary",
                                                size="sm", visible=False, elem_id="trim-dl")
                            trim_src = gr.State("")
                            with gr.Row(elem_id="cut-row"):
                                start = gr.Textbox(label="Начало фрагмента", placeholder="1:02", min_width=110, elem_id="cut-start")
                                end = gr.Textbox(label="Конец фрагмента", placeholder="1:45", min_width=110, elem_id="cut-end")
                                recut_btn = gr.Button("Обрезать заново", icon=design.icon("scissors"), variant="secondary",
                                                      scale=0, min_width=170, elem_id="recut-btn")
                            gr.Markdown("Скачивается только выбранный фрагмент (с запасом в 3 с), срез точный до кадра. "
                                        "Скачанное хранится в кэше: новая обрезка внутри него работает без интернета.",
                                        elem_classes="hint")
                            with gr.Accordion("Доступ к YouTube и yt-dlp", open=False, elem_id="access-acc"):
                                gr.Markdown("Если YouTube просит войти («подтвердите, что вы не бот») или ролик доступен только "
                                            "вашему аккаунту, yt-dlp может взять cookies из вашего браузера на этом компьютере. "
                                            "Приложение их не читает и не хранит — в .env записывается только выбор.",
                                            elem_classes="hint")
                                with gr.Row(elem_classes="cdm-pair"):
                                    acc_browser = gr.Dropdown(ui_download.CHOICES_BROWSER, value=config.YTDLP_COOKIES_FROM_BROWSER,
                                                              label="Cookies из браузера", elem_id="acc-browser")
                                    acc_file = gr.Textbox(label="…или файл cookies.txt", value=config.YTDLP_COOKIES,
                                                          max_lines=1, placeholder="/путь/к/cookies.txt", elem_id="acc-file")
                                acc_status = gr.Markdown(ingest.cookies_status() + f" · yt-dlp {ingest.ytdlp_version()}",
                                                         elem_classes="hint", elem_id="acc-status")
                                with gr.Row(elem_classes="cdm-actions"):
                                    acc_save = gr.Button("Сохранить", icon=design.icon("check"), variant="secondary",
                                                         elem_id="acc-save", scale=0)
                                    acc_update = gr.Button("Обновить yt-dlp", icon=design.icon("refresh"), variant="secondary",
                                                           elem_id="acc-update", scale=0)

                            gr.Markdown("## Игроки\nКто играет белыми и кто чёрными. Аватарки найдутся в видео сами.",
                                        elem_id="sec-players", elem_classes="sec")
                            with gr.Row():
                                white = gr.Textbox(label="Белые", placeholder="Гус Фринг", min_width=140)
                                black = gr.Textbox(label="Чёрные", placeholder="Хэнк", min_width=140)
                            with gr.Accordion("Аватарки и контекст сцены", open=False):
                                with gr.Row():
                                    wav = gr.Image(label="Аватар белых", type="filepath", height=130, sources=["upload"])
                                    bav = gr.Image(label="Аватар чёрных", type="filepath", height=130, sources=["upload"])
                                ctx = gr.Textbox(label="Контекст сцены", lines=2,
                                                 placeholder="Во все тяжкие: Хэнк допрашивает Гуса в УБН")
                            cast_btn = gr.Button("Персонажи в кадре", icon=design.icon("edit"), variant="secondary",
                                                 size="sm", elem_id="cast-btn")
                            sumui = ui_summary.build(gr)

                            gr.Markdown("## Нейросеть\nКто пишет разбор ходов. Выбор применяется сразу, без перезапуска.",
                                        elem_id="sec-llm", elem_classes="sec")
                            with gr.Row(equal_height=True, elem_id="llm-row"):
                                provider = gr.Dropdown(PROVIDERS, value=saved["llm_provider"], label="Провайдер", scale=3,
                                                       min_width=160)
                                model = gr.Dropdown([saved["llm_model"]], value=saved["llm_model"], label="Модель",
                                                    allow_custom_value=True, filterable=True, scale=4, min_width=180)
                                refresh_btn = gr.Button("", icon=design.icon("refresh"), variant="secondary", scale=0,
                                                        min_width=44, elem_id="refresh-btn")
                            key_info = gr.Markdown(provider_info(saved["llm_provider"]), elem_classes="hint")
                            with gr.Row(equal_height=True, visible=llm.SPECS[saved["llm_provider"]].needs_key,
                                        elem_id="key-row") as key_row:
                                key_box = gr.Textbox(show_label=False, type="password", container=False, scale=4,
                                                     placeholder="Вставьте ключ API — он проверится и сохранится в .env")
                                key_save = gr.Button("Проверить и сохранить", icon=design.icon("check"), variant="secondary",
                                                     scale=0, min_width=210)
                                key_link = gr.Button("Получить ключ", icon=design.icon("key"), variant="secondary", scale=0,
                                                     min_width=160, link=llm.SPECS[saved["llm_provider"]].key_page or None,
                                                     link_target="_blank")
                            pull_btn = gr.Button("Скачать модель в Ollama", icon=design.icon("download"), variant="secondary",
                                                 size="sm", visible=saved["llm_provider"] == "ollama", elem_id="pull-btn")

                            gr.Markdown("## Видео и звук\nСтоп-кадр с печатью разбора, звуки и формат ролика.",
                                        elem_id="sec-video", elem_classes="sec")
                            with gr.Row(equal_height=True, elem_id="toggles"):
                                pause = gr.Checkbox(value=saved["pause_on_move"], label="Пауза на разборе хода", min_width=170)
                                sfx = gr.Checkbox(value=saved["sfx_enabled"], label="Звуковые эффекты", min_width=150)
                                fmt = gr.Dropdown(FORMATS, value=saved["output_format"], label="Формат", min_width=170)
                            with gr.Accordion("Пауза и звуки", open=False):
                                with gr.Row():
                                    cps = gr.Slider(8, 80, value=saved["typing_cps"], step=1,
                                                    label="Скорость печати, символов в секунду")
                                    hold = gr.Slider(0, 8, value=saved["read_hold"], step=0.25,
                                                     label="Время на чтение после печати, с")
                                hint = gr.Markdown(pause_hint(saved["typing_cps"], saved["read_hold"]), elem_classes="hint")
                                sfx_vol = gr.Slider(0, 1, value=saved["sfx_volume"], step=0.05, label="Громкость звуков")
                                with gr.Row():
                                    sfx_pause = gr.Checkbox(value=saved["sfx_pause"], label="Звук паузы", min_width=120)
                                    sfx_unpause = gr.Checkbox(value=saved["sfx_unpause"], label="Звук продолжения",
                                                              min_width=140)
                                    sfx_typing = gr.Checkbox(value=saved["sfx_typing"], label="Звук печати", min_width=120)
                                apply_btn = gr.Button("Применить и перерендерить", icon=design.icon("rotate"),
                                                      variant="secondary")
                                gr.Markdown("Перерендер не трогает распознавание речи и разбор нейросети — только собирает "
                                            "видео заново.", elem_classes="hint")
                            review_chk = gr.Checkbox(value=saved["review_before_analysis"], elem_id="review-chk",
                                                     label="Перед разбором показать найденных персонажей и сводку сцены")
                            create_btn = gr.Button("Создать видео", icon=design.icon("film", "dark"), variant="primary",
                                                   size="lg", elem_id="create-btn")
                            with gr.Row(elem_id="manual-start", elem_classes="cdm-actions"):
                                prep_btn = gr.Button("Подготовить для ручного разбора", icon=design.icon("edit"),
                                                     variant="secondary", elem_id="prep-btn", scale=0)
                                prep_asr = gr.Checkbox(value=True, label="распознать речь как черновик реплик",
                                                       elem_id="prep-asr", scale=0, min_width=240)
                            gr.Markdown("Ручной разбор не запускает нейросеть: видео скачается и обрежется, а реплики, "
                                        "оценки и ходы вы составите сами на странице «Ручной разбор».", elem_classes="hint")

                        # ------------------------------------------------------- aside: preview & publish
                        with gr.Column(elem_id="aside", min_width=0):
                            gr.HTML(f'<div class="outline-title">{design.svg("film", size=14)} Предпросмотр</div>',
                                    container=False, padding=False)
                            video = gr.Video(show_label=False, height="clamp(320px, calc(100vh - 520px), 560px)",
                                             interactive=False, buttons=["download"], elem_id="preview")
                            with gr.Accordion("Ходы (JSON)", open=False):
                                moves = gr.Code(language="json", lines=14, max_lines=22, show_label=False,
                                                elem_id="moves-code")
                                with gr.Row():
                                    rerender_btn = gr.Button("Перерендерить", icon=design.icon("rotate"),
                                                             variant="secondary", size="sm")
                                    reanalyze_btn = gr.Button("Новый разбор нейросетью", icon=design.icon("sparkles"),
                                                              variant="secondary", size="sm")
                            gr.HTML(f'<div class="outline-title">{design.svg("send", size=14)} Публикация</div>',
                                    container=False, padding=False)
                            with gr.Column(elem_id="publish-block", min_width=0):
                                pub_title = gr.Textbox(label="Название оригинала", max_lines=2, elem_id="pub-title",
                                                       placeholder="подставится из ролика — можно поправить")
                                pub_kind = gr.Dropdown(describe.KIND_CHOICES, value="", label="Первоисточник",
                                                       elem_id="pub-kind")
                                description = gr.Textbox(label="Описание и хэштеги для TikTok", lines=3,
                                                         elem_id="description", placeholder="Описание… #шахматы #разбор")
                                desc_hint_md = gr.Markdown("", elem_id="desc-hint", elem_classes="hint")
                                with gr.Row(elem_classes="cdm-actions"):
                                    regen_btn = gr.Button("Предложить заново", icon=design.icon("sparkles"),
                                                          variant="secondary", size="sm", scale=0)
                                    refine_btn = gr.Button("Уточнить нейросетью", icon=design.icon("edit"),
                                                           variant="secondary", size="sm", scale=0)
                                    replace_btn = gr.Button("Заменить предложением", icon=design.icon("check"),
                                                            variant="secondary", size="sm", scale=0, visible=False,
                                                            elem_id="replace-btn")
                            publish_btn = gr.Button("Опубликовать в TikTok", icon=design.icon("send", "dark"),
                                                    variant="primary", elem_id="publish-btn")

                settings = [pause, sfx, sfx_vol, fmt, cps, hold, sfx_pause, sfx_unpause, sfx_typing]
                setting_keys = ["pause_on_move", "sfx_enabled", "sfx_volume", "output_format", "typing_cps", "read_hold",
                                "sfx_pause", "sfx_unpause", "sfx_typing"]
                llm_inputs = [provider, model]
                outs = [job_id, video, moves, description, status, bar, tech, jobs_nav, journal, page_title, *settings,
                        *llm_inputs, key_info, key_row, key_link, key_box, pull_btn, source, start, end, white, black, ctx,
                        source_card, pub_title, pub_kind, desc_auto, desc_hint_md, replace_btn, picked, trim, trim_dl,
                        trim_src, *sumui.outputs, *ai.outputs, *castui.outputs, *dlg.outputs]

                def options_from(values) -> dict:
                    opts = dict(zip(setting_keys + ["llm_provider", "llm_model"], values))
                    opts["llm_model"] = (opts["llm_model"] or "").strip()
                    prefs.save(**opts)
                    return opts

                def provider_ui(name):
                    """Key row / pull button / description for the selected provider."""
                    s = llm.SPECS[name]
                    return {
                        key_info: provider_info(name),
                        key_row: gr.update(visible=s.needs_key),
                        key_link: gr.update(link=s.key_page or None),
                        key_box: gr.update(value=""),
                        pull_btn: gr.update(visible=s.kind == "ollama"),
                    }

                def view(job: Job, msg: str) -> dict:
                    meta = ingest.source_meta(job.source) if job.source else {}
                    return {
                        job_id: job.id,
                        video: str(job.final) if job.final.exists() else None,
                        moves: job.moves_file.read_text() if job.moves_file.exists() else "",
                        description: publisher.build_description(job.caption, job.hashtags),
                        status: msg,
                        jobs_nav: gr.update(choices=job_choices(), value=job.id),
                        page_title: job_title(job),
                        source: {"text": job.source, "files": []},
                        start: job.start or "", end: job.end or "",
                        white: job.names["white"], black: job.names["black"], ctx: job.context,
                        **{c: getattr(job, k) for c, k in zip(settings, setting_keys)},
                        provider: job.llm_provider,
                        model: job.llm_model or llm.default_model(job.llm_provider),
                        **provider_ui(job.llm_provider),
                        source_card: source_card_html(job.source_title or meta.get("title", ""), job.source,
                                                      job.source_kind, job.source_channel or meta.get("channel", ""),
                                                      meta.get("duration")),
                        pub_title: job.source_title, pub_kind: job.work_kind, desc_auto: job.caption_generated,
                        desc_hint_md: desc_hint(pipeline.suggest_caption(job), job.caption_edited),
                        replace_btn: gr.update(visible=False), picked: None,
                        **trim_view(job.source, job.start or "", job.end or "", meta.get("duration")),
                        **sumui.load(job.id),
                    }

                def trim_view(src_text: str, a: str = "", b: str = "", duration=None) -> dict:
                    try:
                        html = ui_trim.trim_html(src_text, a, b, duration)
                    except Exception as e:  # the preview is a help, never a reason to fail
                        log.debug("trim preview: %s", e)
                        html = ""
                    return {trim: html, trim_dl: gr.update(visible=ui_trim.needs_download(src_text)), trim_src: src_text or ""}

                def live(action, done: str, retry: str = "Продолжить выбранное", finish=None, ai_op: dict | None = None):
                    """Generator: run `action(run)` in the background, stream status + journal, then show the result
                    (`finish(job)` -> updates; by default the create page). `retry` names the button to press again.
                    When a neural-network request fails and `ai_op` describes the request, the AI dialog opens with
                    other models to repeat it with. Returns the LiveRun (`run = yield from live(...)`)."""
                    run = LiveRun(action)
                    yield {status: run.status(), bar: run.bar(), tech: ""}
                    while run.thread.is_alive():
                        yield {status: run.status(), bar: run.bar(), journal: run.journal()}
                        time.sleep(0.5)
                    tail = {bar: "", journal: run.journal(), tech: run.details()}
                    msg = done if run.error is None else run.failure(retry)
                    if run.job is not None:
                        try:
                            # a page's own result view only after success; a failure keeps the form as it was
                            res = finish(run.job) if finish and run.error is None else view(run.job, msg)
                        except Exception as e:  # the result view must never hide the outcome
                            log.debug("result view: %s", e)
                            res = {}
                        extra = {}
                        if run.error is not None and ai_op is not None and errors.is_ai(run.error):
                            op = {**ai_op, "job": run.job.id, "stage": getattr(run.error, "ai_stage", None) or ai_op.get("stage")}
                            extra = ai.open(run.error, op, Job.load(run.job.id))
                        elif run.error is not None and isinstance(run.error, ingest.DownloadError):
                            extra = dlg.open(run.error, {**(ai_op or {"op": "run"}), "job": run.job.id}, Job.load(run.job.id))
                        yield {**res, status: msg, jobs_nav: gr.update(choices=job_choices(), value=run.job.id), **tail, **extra}
                    else:
                        yield {status: msg, **tail}
                    return run

                def need_job(jid) -> Job:
                    if not jid:
                        raise ValueError("Сначала создайте видео или выберите прошлое задание.")
                    return Job.load(jid)

                shell = SimpleNamespace(gr=gr, job_id=job_id, status=status, bar=bar, tech=tech, journal=journal,
                                        dock=[status, bar, tech, journal, jobs_nav], outs=outs, view=view, live=live,
                                        need_job=need_job, jobs_nav=jobs_nav,
                                        nav=lambda jid: gr.update(choices=job_choices(), value=jid))

                def flow(jid, stage: str):
                    """The rest of creating a video after a review stop: "summary" (who speaks, sounds and the scene
                    summary, then the summary review) or "analysis" (moves, avatars, render)."""
                    if stage == "summary":
                        def action(run):
                            run.job = need_job(jid)
                            pipeline.run_all(run.job, run.progress, until="summary")

                        run = yield from live(action, "📝 Проверьте сводку сцены и нажмите «Принять и продолжить разбор»",
                                              "Продолжить выбранное", ai_op={"op": "flow", "stage": "summary", "job": jid},
                                              finish=lambda job: {**view(job, ""), **go("create")})
                        if run.error is None and not pipeline.needs_summary_review(run.job):
                            yield from flow(jid, "analysis")  # no speech, nothing to review: go on
                        return

                    def action2(run):
                        run.job = need_job(jid)
                        pipeline.run_all(run.job, run.progress)

                    yield from live(action2, "✅ Готово — проверьте видео и разбор", ai_op={"op": "flow", "stage": "analysis",
                                                                                           "job": jid})

                shell.flow = flow

                # ============================================================ page: moves
                with gr.Tab("Ходы", id="moves"):
                    moves_page = ui_moves.build(shell)
                # ============================================================ page: manual analysis
                with gr.Tab("Ручной разбор", id="manual"):
                    manual_page = ui_manual.build(shell)
                # ============================================================ page: sounds
                with gr.Tab("Звуки", id="sounds"):
                    sounds_page = ui_sounds.build(shell)
                # ============================================================ page: video style
                with gr.Tab("Стиль видео", id="style"):
                    style_page = ui_style.build(shell)

        # ---------------------------------------------------------------- bottom dock: status + progress, details on demand
        with gr.Column(elem_id="dock"):
            with gr.Column(elem_id="dock-details"):
                with gr.Row(equal_height=False):
                    journal.render()
                    tech.render()
            with gr.Row(elem_id="dock-bar", equal_height=True):
                status.render()
                bar.render()
                gr.HTML('<button class="cdm-details-toggle" type="button" aria-expanded="false">Подробности'
                        f'{design.svg("chevron-up", size=14)}</button>', container=False, padding=False, elem_id="details-btn")

        # ---------------------------------------------------------------- search dialog: videos | Shorts
        with gr.Column(visible=False, elem_id="search-modal") as modal:
            with gr.Column(elem_id="search-card"):
                search_kind = gr.Radio([("Видео", "video"), ("Shorts", "short")], value="video", show_label=False,
                                       container=False, elem_id="search-kind")
                with gr.Row(equal_height=True):
                    query = gr.Textbox(show_label=False, placeholder="Название фильма, сцены или фраза из диалога",
                                       scale=6, container=False, elem_id="search-query")
                    query_btn = gr.Button("Найти", variant="primary", scale=0, min_width=96)
                    close_btn = gr.Button("", icon=design.icon("x"), variant="secondary", scale=0, min_width=44,
                                          elem_id="search-close")
                with gr.Row(elem_id="shorts-filters", visible=False) as shorts_filters:
                    shorts_dur = gr.Dropdown([("Любая длительность", 0), ("До 60 секунд", 60), ("До 3 минут", 180)],
                                             value=0, label="Длительность", elem_id="shorts-dur")
                    shorts_sort = gr.Dropdown([("По релевантности", "relevance"), ("По просмотрам", "views"),
                                               ("Сначала новые", "date")], value="relevance", label="Порядок",
                                              elem_id="shorts-sort")
                found = gr.Markdown(elem_classes="hint", elem_id="search-found")
                gallery = gr.Gallery(show_label=False, columns=3, height=560, allow_preview=False, buttons=[],
                                     object_fit="cover", preview=False, elem_id="video-gallery")
                shorts_gallery = gr.Gallery(show_label=False, columns=5, height=600, allow_preview=False, buttons=[],
                                            object_fit="cover", preview=False, elem_id="shorts-gallery", visible=False)

        # ================================================================ handlers: create page
        def suggestion_for_form(title, kind, channel, names, meta=None):
            return describe.suggest(title or "", kind=kind or "", channel=channel or "", meta=meta or {}, names=names)

        def _new_job(src, start_v, end_v, white_v, black_v, wav_v, bav_v, ctx_v, pick, desc, auto, title_v, kind_v, values,
                     mode="auto"):
            """(job, None) or (None, updates explaining a wrong input); a search query opens the picker."""
            text, is_file = _source_from(src)
            if not is_file and _looks_like_query(text):
                return None, "search"
            opts = options_from(values)
            meta = _picked_meta(pick, text)
            try:
                job = pipeline.create_job(
                    text, start_v, end_v, white_v or "", black_v or "", wav_v, bav_v, ctx_v or "",
                    source_title=(title_v or "").strip() or meta.get("title", ""), source_kind=meta.get("kind", ""),
                    source_channel=meta.get("channel", ""), work_kind=kind_v or "",
                    caption=desc if _edited(desc, auto) else "", mode=mode, **opts)
            except Exception as e:  # a wrong source or timecode: nothing is created, the form stays as it is
                why = errors.explain(e)
                return None, {status: f"❌ **{why.title}**" + (f"  \n{why.hint}" if why.hint else ""),
                              source_card: f"<div class='cdm-msg cdm-msg-error'>✗ {up.esc(why.title)}</div>"
                                           if isinstance(e, ingest.SourceError) else gr.update()}
            return job, None

        def create_flow(jid):
            """Creating the video of a job: with the review stops (characters, summary) or straight through."""
            review_first = prefs.load().get("review_before_analysis", True)

            def action(run: LiveRun):
                run.job = need_job(jid)
                pipeline.run_all(run.job, run.progress, until="faces" if review_first else None)

            op = {"op": "create", "job": jid}
            if not review_first:
                yield from live(action, "✅ Готово — проверьте видео и разбор", ai_op=op)
                return
            run = yield from live(action, "👤 Лица найдены — проверьте персонажей", ai_op=op)
            if run.error is not None or run.job is None:
                return
            if pipeline.needs_cast(run.job):
                yield {**castui.open(run.job.id, "flow"), status: "👤 Проверьте, кто в кадре: назовите персонажей и "
                                                                  "выберите стороны — или нажмите «Пропустить»"}
                return
            yield from flow(run.job.id, "summary")

        def prepare_flow(jid, asr: bool = True):
            def action(run: LiveRun):
                run.job = need_job(jid)
                pipeline.prepare_manual(run.job, transcribe=bool(asr), progress=run.progress)

            def finish(job):
                return {**view(job, ""), **go("manual"), **manual_page.load(job.id)}

            run = yield from live(action, "✅ Видео готово к ручному разбору — отметьте ключевые реплики и соберите ходы",
                                  "Подготовить для ручного разбора", finish=finish,
                                  ai_op={"op": "prepare", "job": jid, "asr": bool(asr)})
            if run.error is None and run.job is not None and pipeline.needs_cast(run.job):
                yield {**castui.open(run.job.id, "edit"), status: "👤 Проверьте, кто в кадре — имена и стороны "
                                                                  "пригодятся для реплик и аватарок"}

        def on_create(src, start_v, end_v, white_v, black_v, wav_v, bav_v, ctx_v, pick, desc, auto, title_v, kind_v, *values):
            job, problem = _new_job(src, start_v, end_v, white_v, black_v, wav_v, bav_v, ctx_v, pick, desc, auto, title_v,
                                    kind_v, values)
            if problem == "search":  # a search query, not a link: open the picker
                for upd in open_search(_source_from(src)[0], "video"):
                    yield {**upd, status: "🔍 Это не ссылка — ищу на YouTube, выберите ролик"}
                return
            if job is None:
                yield problem
                return
            yield {job_id: job.id, jobs_nav: gr.update(choices=job_choices(), value=job.id)}
            yield from create_flow(job.id)

        def on_prepare(src, start_v, end_v, white_v, black_v, wav_v, bav_v, ctx_v, pick, desc, auto, title_v, kind_v, asr,
                       *values):
            job, problem = _new_job(src, start_v, end_v, white_v, black_v, wav_v, bav_v, ctx_v, pick, desc, auto, title_v,
                                    kind_v, values, mode="manual")
            if problem == "search":
                for upd in open_search(_source_from(src)[0], "video"):
                    yield {**upd, status: "🔍 Это не ссылка — ищу на YouTube, выберите ролик"}
                return
            if job is None:
                yield problem
                return
            yield {job_id: job.id, jobs_nav: gr.update(choices=job_choices(), value=job.id)}
            yield from prepare_flow(job.id, asr)

        def _edited(desc, auto) -> bool:
            return bool((desc or "").strip()) and (desc or "").strip() != (auto or "").strip()

        def _picked_meta(pick, text) -> dict:
            return pick if pick and pick.get("url") == text else {}

        def on_resume(jid, *values):
            opts = options_from(values)

            def action(run: LiveRun):
                run.job = need_job(jid)
                run.job.set_options(**opts)
                run.job.save()
                pipeline.run_all(run.job, run.progress)

            yield from live(action, "✅ Задание завершено", ai_op={"op": "run"})

        def on_rerender(jid, moves_json, *values):
            opts = options_from(values)

            def action(run: LiveRun):
                run.job = need_job(jid)
                run.job.set_options(**opts)
                run.job.save()
                if moves_json and moves_json.strip():
                    try:
                        pipeline.update_moves_json(run.job, moves_json)
                    except Exception as e:
                        raise ValueError(f"Некорректный JSON разбора: {e}") from e
                pipeline.step_render(run.job, run.progress)

            yield from live(action, "✅ Перерендерено с новыми настройками", "Перерендерить")

        def on_reanalyze(jid, *values):
            opts = options_from(values)

            def action(run: LiveRun):
                run.job = need_job(jid)
                run.job.set_options(**opts)
                run.job.mode = "auto"
                run.job.save()
                run.progress(0.1, f"Разбор нейросетью ({run.job.llm_label})")
                pipeline.step_analyze(run.job)
                pipeline.step_render(run.job, lambda f, d: run.progress(0.4 + 0.6 * f, d))

            yield from live(action, "✅ Новый разбор готов", "Новый разбор нейросетью", ai_op={"op": "reanalyze"})

        def on_publish(jid, moves_json, desc):
            def action(run: LiveRun):
                run.job = need_job(jid)
                if json.loads(moves_json or "null") != json.loads(run.job.moves_file.read_text()):
                    raise ValueError("Ходы изменены, но видео не перерендерено — нажмите «Перерендерить».")
                run.progress(0.3, "Публикация в TikTok (откроется браузер)")
                approve_and_publish(run.job, desc or "", "")

            yield from live(action, "✅ Опубликовано в TikTok", "Опубликовать в TikTok")

        def on_recut(jid, start_v, end_v, *values):
            opts = options_from(values)

            def action(run: LiveRun):
                run.job = need_job(jid)
                run.job.set_options(**opts)
                if not pipeline.recut(run.job, start_v, end_v):
                    log.info("Обрезка не изменилась — пересобираю только то, чего не хватает")
                if run.job.mode == "manual" and not run.job.moves_file.exists():
                    pipeline.prepare_manual(run.job, transcribe=run.job.transcript.exists(), progress=run.progress)
                else:
                    pipeline.run_all(run.job, run.progress)

            yield from live(action, "✅ Готово — фрагмент обрезан заново", "Обрезать заново", ai_op={"op": "recut", "job": jid})

        def on_new():
            return {job_id: None, video: None, moves: "", description: "", status: "", journal: "", tech: "", bar: "",
                    page_title: NEW_TITLE, jobs_nav: gr.update(choices=job_choices(), value=None),
                    source: {"text": "", "files": []}, start: "", end: "", white: "", black: "", ctx: "",
                    source_card: "", pub_title: "", pub_kind: "", desc_auto: "", desc_hint_md: "",
                    replace_btn: gr.update(visible=False), picked: None, **trim_view(""), **sumui.load(None), **go("create")}

        # ---------------------------------------------------------------- description
        def on_pub_change(jid, title_v, kind_v, desc, auto, pick, white_v, black_v):
            names = {"white": white_v or "Белые", "black": black_v or "Чёрные"}
            if jid:
                job = Job.load(jid)
                job.source_title, job.work_kind = (title_v or "").strip(), kind_v or ""
                if (desc or "").strip() != publisher.build_description(job.caption, job.hashtags).strip():
                    job.caption, job.hashtags = (desc or "").strip(), ""   # the field already holds the hashtags
                s, applied = pipeline.refresh_caption(job)
                job.save()
            else:
                s = suggestion_for_form(title_v, kind_v, (pick or {}).get("channel", ""), names)
                applied = not _edited(desc, auto)
            return offer(s, not applied, auto)

        def offer(s, edited: bool, auto: str) -> dict:
            """Apply a suggestion — or, next to a hand-edited description, offer it (if it is something new)."""
            if not edited:
                return applied_view(s)
            if s.text == (auto or "").strip():
                return {desc_hint_md: desc_hint(s, True), replace_btn: gr.update(visible=False), proposal: ""}
            return proposal_view(s)

        def applied_view(s) -> dict:
            return {description: s.text, desc_auto: s.text, desc_hint_md: desc_hint(s, False),
                    replace_btn: gr.update(visible=False), proposal: ""}

        def proposal_view(s) -> dict:
            """A new suggestion next to a hand-edited description, which stays as it is."""
            return {proposal: s.text, replace_btn: gr.update(visible=True),
                    desc_hint_md: f"<div class='cdm-proposal'>Новое предложение: <b>{up.esc(s.text)}</b><br>"
                                  "Ваше описание изменено вручную и осталось как есть — нажмите «Заменить предложением», "
                                  "чтобы применить новое.</div>"}

        def on_desc_blur(jid, desc, auto):
            if jid:
                job = Job.load(jid)
                job.caption = (desc or "").strip()
                job.hashtags = ""
                job.save()
                return {desc_hint_md: desc_hint(pipeline.suggest_caption(job), job.caption_edited)}
            return {desc_hint_md: desc_hint(None, True) if _edited(desc, auto) else ""}

        def on_regen(jid, title_v, kind_v, desc, auto, pick, white_v, black_v):
            return on_pub_change(jid, title_v, kind_v, desc, auto, pick, white_v, black_v)

        def on_replace(jid, text):
            if not text:
                return {}
            if jid:
                job = Job.load(jid)
                job.caption = job.caption_generated = text
                job.hashtags = ""
                job.save()
            return {description: text, desc_auto: text, proposal: "", replace_btn: gr.update(visible=False),
                    desc_hint_md: "✨ Описание заменено предложением. Его можно править.", status: "✅ Описание обновлено"}

        def on_refine(jid, title_v, kind_v, desc, auto, pick, white_v, black_v, prov, mdl):
            title_v = (title_v or "").strip()
            if not title_v:
                return {status: "⚠️ Сначала укажите название оригинала"}
            job = Job.load(jid) if jid else None
            meta = ingest.source_meta(job.source) if job else {}
            try:
                work, kind2 = describe.refine_with_llm(title_v, meta, llm.get_provider(prov, (mdl or "").strip() or None))
            except Exception as e:
                res = {status: f"❌ **{errors.explain(e).title}** {errors.explain(e).hint}"}
                if errors.is_ai(e):
                    res.update(ai.open(e, {"op": "refine", "job": jid, "provider": prov, "model": (mdl or "").strip(),
                                           "args": [title_v, kind_v, desc, auto, pick, white_v, black_v]}, job))
                return res
            new_kind = kind_v or kind2
            names = {"white": white_v or "Белые", "black": black_v or "Чёрные"}
            s = describe.suggest(title_v, kind=new_kind, channel=(pick or {}).get("channel", "") or (job.source_channel if job else ""),
                                 meta=meta, names=names, work=work)
            if job:
                job.work_kind = new_kind
                job.save()
            found_txt = ", ".join(x for x in (f"произведение «{work}»" if work else "", describe.KINDS.get(kind2, ("",))[0].lower()
                                                if kind2 else "") if x) or "ничего уверенного"
            res = {pub_kind: new_kind, status: f"🤖 Нейросеть определила: {found_txt}"}
            if not _edited(desc, auto):
                if job:
                    job.caption = job.caption_generated = s.text
                    job.save()
                res.update({description: s.text, desc_auto: s.text, desc_hint_md: desc_hint(s, False)})
            else:
                res.update({proposal: s.text, replace_btn: gr.update(visible=True),
                            desc_hint_md: f"<div class='cdm-proposal'>Новое предложение: <b>{up.esc(s.text)}</b></div>"})
            return res

        # ---------------------------------------------------------------- navigation between pages
        def go(page: str) -> dict:
            return {pages: gr.Tabs(selected=page), pages_nav: page, page_anchors: anchors_html(page)}

        def page_data(page: str, jid) -> dict:
            if page == "moves":
                return moves_page.load(jid)
            if page == "manual":
                return manual_page.load(jid)
            if page == "sounds":
                return sounds_page.load(jid)
            if page == "style":
                return style_page.load(jid)
            if jid:
                try:
                    return view(Job.load(jid), "")
                except FileNotFoundError:
                    return {}
            return {}

        def on_page(page, jid):
            return {**go(page), **page_data(page, jid)}

        def on_select(jid, page):
            try:
                job = need_job(jid)
            except Exception as e:
                return {status: f"❌ {short_error(e)}"}
            msg = (f"❌ **Задание остановилось:** {job.error}  \nИсправьте причину и нажмите «Продолжить выбранное»."
                   if job.status == "failed" and job.error else "")
            res = view(job, msg)
            if page in ("moves", "manual", "sounds", "style"):
                res.update(page_data(page, jid))
            return res

        def on_page_load():
            """Fresh values on every page open (not the ones frozen when the server started)."""
            p = prefs.load()
            res = {k: v for k, v in load_models(p["llm_provider"], p["llm_model"], False).items() if k is not status}
            return {**res, provider: p["llm_provider"], **{c: p[k] for c, k in zip(settings, setting_keys)},
                    hint: pause_hint(p["typing_cps"], p["read_hold"]), jobs_nav: gr.update(choices=job_choices())}

        # ---------------------------------------------------------------- LLM provider
        def load_models(name, current, user_action: bool):
            """Fill the model list for a provider; keeps the current model if it is available."""
            ui_bits = provider_ui(name)
            try:
                pairs = llm.list_models(name)
            except Exception as e:
                why = errors.explain(e)
                return {**ui_bits, model: gr.update(choices=[current] if current else []),
                        status: f"⚠️ **{why.title}** {why.hint}"}
            names = [v for _, v in pairs]
            default = llm.default_model(name)
            keep = current if current in names else (default if default in names else (names[0] if names else default))
            if user_action:
                prefs.save(llm_provider=name, llm_model=keep)
            label = llm.SPECS[name].label
            msg = f"🤖 {label}: моделей в списке — {len(names)}" if names else f"⚠️ {label}: список моделей пуст"
            return {**ui_bits, model: gr.update(choices=pairs or [keep], value=keep), status: msg}

        def on_key_save(name, key, current):
            try:
                msg = llm.save_key(name, key or "")
            except Exception as e:
                why = errors.explain(e)
                return {status: f"❌ **{why.title}** {why.hint}", key_info: provider_info(name)}
            return {**load_models(name, current, False), status: msg, key_box: ""}

        def on_pull(name, model_name):
            def action(run: LiveRun):
                if not (model_name or "").strip():
                    raise ValueError("Выберите или впишите модель, которую нужно скачать.")
                llm.OllamaProvider.pull(model_name.strip(), run.progress)

            run = LiveRun(action)
            while run.thread.is_alive():
                yield {status: run.status(), bar: run.bar(), journal: run.journal()}
                time.sleep(0.5)
            if run.error is not None:
                yield {status: run.failure("Скачать модель в Ollama"), bar: "", journal: run.journal(), tech: run.details()}
            else:
                yield {**load_models(name, model_name, True), status: f"✅ Модель {model_name} скачана — можно запускать разбор",
                       bar: "", journal: run.journal()}

        # ---------------------------------------------------------------- search: videos | Shorts
        def open_search(q, kind="video", max_dur=0, sort="relevance"):
            """Generator: the dialog at once (right gallery, «ищу…»), then the results."""
            q = (q or "").strip()
            is_short = kind == "short"
            target, other = (shorts_gallery, gallery) if is_short else (gallery, shorts_gallery)

            def dialog(msg: str, vids=()) -> dict:
                # one update per gallery: value and visibility together (a second key would override the first)
                return {modal: gr.update(visible=True), query: q, search_kind: kind, found: msg,
                        shorts_filters: gr.update(visible=is_short), other: gr.update(visible=False),
                        target: gr.update(value=[(v.thumb, v.caption) for v in vids], visible=True),
                        results: [v.to_dict() for v in vids]}

            if not q:
                yield dialog("Введите название, фразу или ключевые слова. Shorts канала: «@канал слова»." if is_short
                             else "Введите запрос и нажмите «Найти».")
                return
            yield dialog(f"🔍 Ищу {'Shorts' if is_short else 'ролики'} по запросу «{q}»…")
            try:
                vids = (search.search_shorts(q, max_duration=float(max_dur or 0) or None, sort=sort or "relevance")
                        if is_short else search.search_videos(q))
            except Exception as e:
                yield dialog(f"❌ {errors.explain(e)}")
                return
            if not vids:
                yield dialog(f"По запросу «{q}» ничего не нашлось — попробуйте иначе.")
                return
            yield dialog(f"Найдено {'Shorts' if is_short else 'роликов'}: {len(vids)} — кликните, чтобы выбрать источником", vids)

        def on_search_btn(src, kind):
            text, is_file = _source_from(src)
            yield from open_search("" if is_file or search.is_url(text) else text, kind or "video")

        def on_pick(vids, desc, auto, white_v, black_v, jid, evt: gr.SelectData):
            v = search.Video.from_dict(vids[evt.index])
            dur = fmt_duration(v.duration) if v.duration else "?"
            names = {"white": white_v or "Белые", "black": black_v or "Чёрные"}
            res = {
                source: {"text": v.url, "files": []},
                modal: gr.update(visible=False),
                picked: v.to_dict(),
                source_card: source_card_html(v.title, v.url, v.kind, v.channel, v.duration, v.views, v.thumb),
                **trim_view(v.url, "", "", v.duration),
                pub_title: v.title,
                status: f"📌 Выбрано{' Shorts' if v.is_short else ''}: **{v.title}** ({dur}). Укажите начало и конец "
                        "диалога и нажмите «Создать видео» или «Подготовить для ручного разбора».",
                **go("create"),
            }
            s = describe.suggest(v.title, channel=v.channel, names=names)
            if jid:
                # a new source starts a new video: the form lets go of the selected job (which keeps its own
                # title and description), so later edits of the publication fields cannot land in that job
                res.update({job_id: None, video: None, moves: "", page_title: NEW_TITLE, start: "", end: "",
                            jobs_nav: gr.update(choices=job_choices(), value=None), **applied_view(s)})
                res[status] += f" Это новое видео — задание `{jid}` осталось в списке слева."
            else:  # the description follows the chosen title unless it was edited by hand
                res.update(offer(s, _edited(desc, auto), auto))
            return res

        on_pick.__annotations__["evt"] = gr.SelectData  # string annotations can't see the local `gr`

        def on_source_submit(src, kind):
            text, is_file = _source_from(src)
            if not is_file and _looks_like_query(text):
                yield from open_search(text, kind or "video")
                return
            card = source_card_html("", text, "", "") if search.is_url(text) else ""
            yield {status: "Источник принят — нажмите «Создать видео»." if text else "", source_card: card, **trim_view(text)}

        def on_source_change(src, current):
            """A link or a file in the source field: check it and show it in the trimming editor (once per source).
            A text that is neither (a search query) is left alone."""
            text, is_file = _source_from(src)
            looks_local = is_file or (bool(text) and not search.is_url(text) and (
                Path(text).expanduser().exists() or text.startswith(("/", "~", "./"))))
            if text == current or not (looks_local or search.is_url(text) or ingest._BARE_URL.match(text or "")):
                return {}
            try:
                found = ingest.inspect_source(text)
            except ingest.SourceError as e:
                return {source_card: f"<div class='cdm-msg cdm-msg-error'>✗ {up.esc(e.title)} {up.esc(e.hint)}</div>",
                        trim: "", trim_dl: gr.update(visible=False), trim_src: text}
            card = source_card_html("", found.value, "", "") if found.kind == "url" else source_card_html(
                Path(found.value).stem, found.value, "file", "")
            if found.note:
                card += f"<div class='cdm-msg cdm-msg-ok'>✓ {up.esc(found.note.capitalize())}</div>"
            return {source_card: card, **trim_view(found.value), trim_src: text}

        def on_trim_download(src, start_v, end_v):
            text, _ = _source_from(src)
            if not search.is_url(text):
                yield {status: "⚠️ Скачать для обрезки можно только ссылку"}
                return
            if text and not ui_trim.needs_download(text):
                yield {status: "Видео уже скачано", **trim_view(text, start_v, end_v)}
                return

            def action(run):
                ui_trim.download(text, run.progress)

            run = LiveRun(action)
            yield {status: run.status(), bar: run.bar(), tech: ""}
            while run.thread.is_alive():
                yield {status: run.status(), bar: run.bar(), journal: run.journal()}
                time.sleep(0.5)
            msg = "✅ Видео скачано — обрезайте точно по кадрам, без интернета" if run.error is None else run.failure("Скачать для точной обрезки")
            extra = dlg.open(run.error, {"op": "trim", "url": text}) if isinstance(run.error, ingest.DownloadError) else {}
            yield {status: msg, bar: "", journal: run.journal(), tech: run.details(), **trim_view(text, start_v, end_v), **extra}

        # ---------------------------------------------------------------- download problems: the person decides
        def resume(ctx: dict, src_now=None, start_now="", end_now=""):
            op, jid = ctx.get("op"), ctx.get("job")
            if op == "trim":
                yield from on_trim_download({"text": ctx.get("url", ""), "files": []}, start_now, end_now)
            elif not jid:
                yield {status: "⚠️ Задание не найдено — создайте видео заново"}
            elif op == "create":
                yield from create_flow(jid)
            elif op == "prepare":
                yield from prepare_flow(jid, ctx.get("asr", True))
            elif op == "flow":
                yield from flow(jid, ctx.get("stage") or "summary")
            else:
                def action(run):
                    run.job = need_job(jid)
                    pipeline.run_all(run.job, run.progress)

                yield from live(action, "✅ Задание завершено", ai_op={"op": "run"})

        def on_dl_full(ctx, start_now, end_now):
            ctx = dict(ctx or {})
            yield {dlg.modal: gr.update(visible=False)}
            if ctx.get("job"):
                job = Job.load(ctx["job"])
                job.allow_full_download = True
                job.save()
                log.info("Разрешено скачать весь ролик для задания %s", job.id)
            elif ctx.get("op") == "trim":
                yield from on_trim_download({"text": ctx.get("url", ""), "files": []}, start_now, end_now)
                return
            yield from resume(ctx, None, start_now, end_now)

        def on_dl_retry(ctx, start_now, end_now):
            yield {dlg.modal: gr.update(visible=False)}
            yield from resume(dict(ctx or {}), None, start_now, end_now)

        def on_dl_update(ctx, start_now, end_now):
            yield {dlg.modal: gr.update(visible=False), status: "🔄 **Обновляю yt-dlp…**"}
            try:
                old, new = ingest.update_ytdlp()
            except Exception as e:
                why = errors.explain(e)
                yield {status: f"❌ **{why.title}** {why.hint}"}
                return
            yield {status: f"✅ yt-dlp {old} → {new}" if old != new else f"yt-dlp уже последней версии ({new}) — повторяю",
                   acc_status: ingest.cookies_status() + f" · yt-dlp {new}"}
            yield from resume(dict(ctx or {}), None, start_now, end_now)

        def on_dl_cookies(ctx, browser_v, file_v, start_now, end_now):
            try:
                note = ingest.set_cookies(browser_v, file_v)
            except ingest.SourceError as e:
                yield {status: f"⚠️ {e.title} {e.hint}"}
                return
            yield {dlg.modal: gr.update(visible=False), status: f"🔑 {note} — повторяю", acc_status: note,
                   acc_browser: config.YTDLP_COOKIES_FROM_BROWSER, acc_file: config.YTDLP_COOKIES}
            yield from resume(dict(ctx or {}), None, start_now, end_now)

        def on_dl_range():
            return {dlg.modal: gr.update(visible=False), **go("create"),
                    status: "✂ Выберите начало и конец диалога в редакторе обрезки, затем нажмите «Обрезать заново» — "
                            "скачается только этот фрагмент"}

        def on_access_save(browser_v, file_v):
            try:
                note = ingest.set_cookies(browser_v, file_v)
            except ingest.SourceError as e:
                return {status: f"⚠️ {e.title} {e.hint}"}
            return {acc_status: note + f" · yt-dlp {ingest.ytdlp_version()}", status: f"✅ {note}"}

        def on_access_update():
            yield {status: "🔄 **Обновляю yt-dlp…**"}
            try:
                old, new = ingest.update_ytdlp()
            except Exception as e:
                why = errors.explain(e)
                yield {status: f"❌ **{why.title}** {why.hint}"}
                return
            yield {acc_status: ingest.cookies_status() + f" · yt-dlp {new}",
                   status: f"✅ yt-dlp обновлён: {old} → {new}" if old != new else f"✅ yt-dlp уже последней версии ({new})"}

        def on_cast_open(jid):
            if not jid:
                return {status: "⚠️ Сначала создайте видео или выберите задание — лица ищутся в скачанном клипе"}
            job = Job.load(jid)
            if not job.clip.exists():
                return {status: "⚠️ Клип ещё не готов — лица появятся после скачивания"}
            res = castui.open(jid, "edit")
            if not res.get(castui.modal, {}) or res.get(castui.modal) == gr.update(visible=False):
                return {status: "В кадре не нашлось лиц (или анализ лиц недоступен)"}
            return {**res, status: "👤 Персонажи задания"}

        # ---------------------------------------------------------------- AI failure: repeat the same request
        def on_ai_retry(ctx, choice, switch: bool, *values):
            ctx = dict(ctx or {})
            jid = ctx.get("job")
            yield {ai.modal: gr.update(visible=False)}
            updates = {}
            if switch:
                if not choice:
                    yield {status: "⚠️ Выберите модель из списка"}
                    return
                prov, mdl = choice.split("|", 1)
                ctx["provider"], ctx["model"] = prov, mdl
                prefs.save(llm_provider=prov, llm_model=mdl)
                updates = {**{k: v for k, v in load_models(prov, mdl, False).items() if k is not status}, provider: prov}
                if jid:
                    job = Job.load(jid)
                    job.llm_provider, job.llm_model = prov, mdl
                    job.save()
                yield {**updates, status: f"🔁 Повторяю с {llm.SPECS[prov].label} · {mdl}"}
            op = ctx.get("op")
            if op == "refine":
                title_v, kind_v, desc, auto, pick, white_v, black_v = ctx.get("args") or [""] * 7
                yield on_refine(jid, title_v, kind_v, desc, auto, pick, white_v, black_v, ctx.get("provider"), ctx.get("model"))
                return
            if not jid:
                yield {status: "⚠️ Задание не найдено — запустите заново"}
                return
            if op == "create":
                yield from create_flow(jid)
            elif op == "prepare":
                yield from prepare_flow(jid, ctx.get("asr", True))
            elif op == "flow":
                yield from flow(jid, ctx.get("stage") or "summary")
            elif op == "summary":
                def action(run):
                    run.job = need_job(jid)
                    pipeline.step_summary(run.job)

                yield from live(action, "✅ Сводка готова — проверьте её и примите", "Сгенерировать заново",
                                finish=lambda job: sumui.load(job.id), ai_op={"op": "summary", "job": jid})
            elif op == "reanalyze":
                def action(run):
                    run.job = need_job(jid)
                    pipeline.step_analyze(run.job)
                    pipeline.step_render(run.job, lambda f, d: run.progress(0.4 + 0.6 * f, d))

                yield from live(action, "✅ Новый разбор готов", "Новый разбор нейросетью", ai_op={"op": "reanalyze"})
            else:
                def action(run):
                    run.job = need_job(jid)
                    pipeline.run_all(run.job, run.progress)

                yield from live(action, "✅ Задание завершено", ai_op={"op": "run"})

        # ================================================================ wiring
        hid = dict(show_progress="hidden")
        nav_outs = [pages, pages_nav, page_anchors]
        page_outs = list(dict.fromkeys(nav_outs + outs + moves_page.outputs + manual_page.outputs + sounds_page.outputs
                                       + style_page.outputs))
        hash_js = "(p) => { history.replaceState(null, '', p === 'create' ? location.pathname : '#' + p); return []; }"
        modal_outs = [modal, query, found, gallery, shorts_gallery, results, status, search_kind, shorts_filters]
        form_in = [source, start, end, white, black, wav, bav, ctx, picked, description, desc_auto, pub_title, pub_kind]

        create_btn.click(on_create, [*form_in, *settings, *llm_inputs], outs + [c for c in modal_outs if c not in outs], **hid)
        prep_btn.click(on_prepare, [*form_in, prep_asr, *settings, *llm_inputs],
                       list(dict.fromkeys(page_outs + modal_outs)), **hid).then(None, [pages_nav], None, js=hash_js)
        resume_btn.click(on_resume, [jobs_nav, *settings, *llm_inputs], outs, **hid)
        rerender_btn.click(on_rerender, [job_id, moves, *settings, *llm_inputs], outs, **hid)
        apply_btn.click(on_rerender, [job_id, moves, *settings, *llm_inputs], outs, **hid)
        reanalyze_btn.click(on_reanalyze, [job_id, *settings, *llm_inputs], outs, **hid)
        publish_btn.click(on_publish, [job_id, moves, description], outs, **hid)
        jobs_nav.input(on_select, [jobs_nav, pages_nav], page_outs, **hid)
        new_btn.click(on_new, None, list(dict.fromkeys(outs + nav_outs)), **hid).then(None, [pages_nav], None, js=hash_js)
        recut_btn.click(on_recut, [job_id, start, end, *settings, *llm_inputs], outs, **hid)
        hdr_search.submit(open_search, [hdr_search, search_kind], modal_outs, **hid)  # keeps the last mode: videos | Shorts
        pages_nav.input(on_page, [pages_nav, job_id], page_outs, **hid)
        manual_page.to_moves.click(lambda jid: on_page("moves", jid), [job_id], page_outs, **hid) \
            .then(None, [pages_nav], None, js=hash_js)
        manual_page.goto_create.click(lambda: go("create"), None, nav_outs, **hid).then(None, [pages_nav], None, js=hash_js)

        pub_in = [job_id, pub_title, pub_kind, description, desc_auto, picked, white, black]
        pub_out = [description, desc_auto, desc_hint_md, replace_btn, proposal, status]
        pub_title.blur(on_pub_change, pub_in, pub_out, **hid)
        pub_title.submit(on_pub_change, pub_in, pub_out, **hid)
        pub_kind.input(on_pub_change, pub_in, pub_out, **hid)
        regen_btn.click(on_regen, pub_in, pub_out, **hid)
        replace_btn.click(on_replace, [job_id, proposal], pub_out, **hid)
        description.blur(on_desc_blur, [job_id, description, desc_auto], [desc_hint_md], **hid)
        refine_btn.click(on_refine, [*pub_in, provider, model], pub_out + [pub_kind] + ai.outputs, **hid)

        provider_outs = [model, status, key_info, key_row, key_link, key_box, pull_btn]
        provider.input(lambda n, m: load_models(n, None, True), [provider, model], provider_outs, **hid)
        refresh_btn.click(lambda n, m: load_models(n, m, False), [provider, model], provider_outs, **hid)
        model.input(lambda n, m: prefs.save(llm_provider=n, llm_model=m), [provider, model], None, **hid)
        ui.load(on_page_load, None, provider_outs + [provider, hint, jobs_nav, *settings], **hid)
        key_save.click(on_key_save, [provider, key_box, model], provider_outs, **hid)
        key_box.submit(on_key_save, [provider, key_box, model], provider_outs, **hid)
        pull_btn.click(on_pull, [provider, model], provider_outs + [journal, bar, tech], **hid)

        for c in (cps, hold):
            c.change(pause_hint, [cps, hold], hint, **hid)

        search_btn.click(on_search_btn, [source, search_kind], modal_outs, **hid)
        source.submit(on_source_submit, [source, search_kind], modal_outs + [source_card, trim, trim_dl, trim_src], **hid)
        source.change(on_source_change, [source, trim_src], [source_card, trim, trim_dl, trim_src], **hid)
        trim_dl.click(on_trim_download, [source, start, end], [status, bar, tech, journal, trim, trim_dl, trim_src, *dlg.outputs],
                      **hid)
        cast_btn.click(on_cast_open, [job_id], castui.outputs + [status], **hid)
        review_chk.change(lambda v: prefs.save(review_before_analysis=bool(v)), [review_chk], None, **hid)
        dl_outs = list(dict.fromkeys(outs + nav_outs + [acc_status, acc_browser, acc_file]))
        dlg.full.click(on_dl_full, [dlg.ctx, start, end], dl_outs, **hid)
        dlg.retry.click(on_dl_retry, [dlg.ctx, start, end], dl_outs, **hid)
        dlg.update.click(on_dl_update, [dlg.ctx, start, end], dl_outs, **hid)
        dlg.cookies.click(on_dl_cookies, [dlg.ctx, dlg.browser, dlg.cfile, start, end], dl_outs, **hid)
        dlg.range.click(on_dl_range, None, [dlg.modal, status, *nav_outs], **hid)
        acc_save.click(on_access_save, [acc_browser, acc_file], [acc_status, status], **hid)
        acc_update.click(on_access_update, None, [acc_status, status], **hid)
        flow_outs = list(dict.fromkeys(outs + nav_outs))
        ui_cast.wire(castui, shell, flow, flow_outs)
        ui_summary.wire(sumui, shell, flow, flow_outs, ai)
        retry_outs = list(dict.fromkeys(outs + nav_outs + pub_out + [pub_kind] + provider_outs + [provider]))
        ai.retry.click(lambda ctx, *v: (yield from on_ai_retry(ctx, None, False, *v)), [ai.ctx], retry_outs, **hid)
        ai.switch.click(lambda ctx, ch, *v: (yield from on_ai_retry(ctx, ch, True, *v)), [ai.ctx, ai.alts], retry_outs, **hid)
        query_btn.click(open_search, [query, search_kind, shorts_dur, shorts_sort], modal_outs, **hid)
        query.submit(open_search, [query, search_kind, shorts_dur, shorts_sort], modal_outs, **hid)
        search_kind.input(open_search, [query, search_kind, shorts_dur, shorts_sort], modal_outs, **hid)
        for c in (shorts_dur, shorts_sort):  # .change: a click on an option fires .input twice (Gradio 6)
            c.change(open_search, [query, search_kind, shorts_dur, shorts_sort], modal_outs, **hid)
        close_btn.click(lambda: gr.update(visible=False), None, modal, **hid)
        pick_out = [source, modal, picked, source_card, pub_title, status, description, desc_auto, desc_hint_md, replace_btn,
                    proposal, job_id, video, moves, jobs_nav, page_title, start, end, trim, trim_dl, trim_src, *nav_outs]
        for g in (gallery, shorts_gallery):
            g.select(on_pick, [results, description, desc_auto, white, black, job_id], pick_out, **hid) \
                .then(None, [pages_nav], None, js=hash_js)
    log.debug("handlers protected: %d", harden(ui, status))
    return ui


def harden(ui, status) -> int:
    """Every event handler of `ui` turns an unexpected exception into a readable message (in the
    status line when the handler writes it, else a warning toast) and leaves its other outputs as
    they are — one failing handler never breaks the page. Its results are made valid for its
    declared outputs: an empty dict ("nothing to update", which Gradio 6 would take for a single
    value) becomes a skip for every output, and keys that are not outputs are dropped instead of
    failing the event. Returns the number of handlers wrapped."""
    import functools
    import inspect

    import gradio as gr

    n = 0
    for bf in ui.fns.values():
        fn = bf.fn
        if fn is None or getattr(fn, "_cdm_safe", False):
            continue
        outs = list(bf.outputs)
        idx = next((i for i, o in enumerate(outs) if o is status), None)
        fn_name = getattr(fn, "__name__", "handler")

        def fallback(e: BaseException, outs=outs, idx=idx, name=fn_name):
            why = errors.explain(e)
            log.error("%s %s", why.title, why.hint)
            log.debug("handler %s failed", name, exc_info=e)
            if not outs:
                return None
            vals = [gr.skip()] * len(outs)
            if idx is not None:
                vals[idx] = f"❌ **{why.title}**" + (f"  \n{why.hint}" if why.hint else "")
            else:
                gr.Warning(why.title)
            return vals if len(outs) > 1 else vals[0]

        def fit(pred, outs=outs, name=fn_name):
            if not isinstance(pred, dict) or (pred and not all(hasattr(k, "_id") for k in pred)):
                return pred  # a plain value / tuple, or a value that happens to be a dict (gr.update)
            if not pred:
                return None if not outs else ([gr.skip()] * len(outs) if len(outs) > 1 else gr.skip())
            ids = {o._id for o in outs}
            extra = [k for k in pred if k._id not in ids]
            if extra:
                log.debug("%s: dropped updates for %d components that are not its outputs", name, len(extra))
                pred = {k: v for k, v in pred.items() if k._id in ids}
                if not pred:
                    return None if not outs else ([gr.skip()] * len(outs) if len(outs) > 1 else gr.skip())
            return pred

        if inspect.isgeneratorfunction(fn):
            @functools.wraps(fn)
            def safe(*args, _fn=fn, _fb=fallback, _fit=fit, **kw):
                try:
                    for pred in _fn(*args, **kw):
                        yield _fit(pred)
                except Exception as e:  # noqa: BLE001 — the page must survive any handler
                    yield _fb(e)
        else:
            @functools.wraps(fn)
            def safe(*args, _fn=fn, _fb=fallback, _fit=fit, **kw):
                try:
                    return _fit(_fn(*args, **kw))
                except Exception as e:  # noqa: BLE001
                    return _fb(e)
        safe._cdm_safe = True
        bf.fn = safe
        n += 1
    return n


def launch_ui(host: str = "127.0.0.1", port: int = 7860, share: bool = False) -> None:
    config.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    log.info("Веб-интерфейс: http://%s:%d", host, port)
    build_ui().queue().launch(
        server_name=host, server_port=port, share=share, quiet=True,
        allowed_paths=[str(config.JOBS_DIR), str(design.UI_DIR)],
        theme=design.theme(), css=design.css(), head=design.HEAD,
    )
