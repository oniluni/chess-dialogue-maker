"""The «скачивание не получилось» dialog: what happened with the source, what can be done,
and buttons that do exactly that — download the whole video (only when the person says
so), retry, update yt-dlp, set up the person's own browser cookies, or choose a fragment.
The job keeps its source and timecodes; review.py maps the stored request back to its
action, so a retry continues where it stopped.

Restricted videos (private, members-only, region-locked, DRM) only get an explanation:
nothing here tries to get around a restriction.
"""
from __future__ import annotations

from types import SimpleNamespace

import config
import design
import ingest
import ui_parts as up

CHOICES_BROWSER = [("— не использовать —", "")] + [(b.capitalize(), b) for b in ingest.BROWSERS]
ACTIONS = {
    "partial": ("full", "retry"),
    "range": ("full", "range"),
    "auth": ("cookies", "retry"),
    "age": ("cookies",),
    "forbidden": ("update", "retry", "full"),
    "outdated": ("update", "retry"),
    "nofile": ("update", "retry"),
    "other": ("retry", "update"),
    "expired": ("retry",),
    "network": ("retry",),
    "rate": ("retry",),
}


def build(gr) -> SimpleNamespace:
    ctx = gr.State(None)   # {"op", "job", "url", "kind"}
    with gr.Column(visible=False, elem_id="dl-modal") as modal:
        with gr.Column(elem_id="dl-card"):
            title = gr.Markdown("", elem_id="dl-title")
            body = gr.HTML("", elem_id="dl-body", container=False, padding=False)
            with gr.Row(visible=False, elem_id="dl-cookies", elem_classes="cdm-pair") as cookies_row:
                browser = gr.Dropdown(CHOICES_BROWSER, value="", label="Браузер, где вы вошли в YouTube", elem_id="dl-browser")
                cfile = gr.Textbox(label="…или файл cookies.txt", placeholder="/путь/к/cookies.txt", max_lines=1,
                                   elem_id="dl-cookies-file")
            with gr.Row(elem_classes="cdm-actions"):
                full = gr.Button("Скачать весь ролик", icon=design.icon("download", "dark"), variant="primary",
                                 elem_id="dl-full", scale=0)
                cookies = gr.Button("Сохранить и повторить", icon=design.icon("key", "dark"), variant="primary",
                                    elem_id="dl-cookies-save", scale=0)
                update = gr.Button("Обновить yt-dlp и повторить", icon=design.icon("refresh"), variant="secondary",
                                   elem_id="dl-update", scale=0)
                retry = gr.Button("Повторить", icon=design.icon("rotate"), variant="secondary", elem_id="dl-retry", scale=0)
                rng = gr.Button("Указать фрагмент", icon=design.icon("scissors"), variant="secondary", elem_id="dl-range",
                                scale=0)
                close = gr.Button("Закрыть", variant="secondary", elem_id="dl-close", scale=0)
    buttons = {"full": full, "cookies": cookies, "update": update, "retry": retry, "range": rng}
    outputs = [modal, title, body, cookies_row, browser, cfile, ctx, *buttons.values()]

    def open_(e: BaseException, op: dict, job=None) -> dict:
        kind = getattr(e, "kind", "other")
        html = f"<div class='cdm-msg cdm-msg-error'>✗ {up.esc(e.title)}</div><p>{up.esc(getattr(e, 'hint', ''))}</p>"
        if job is not None:
            sel = f"{job.start or '0:00'}–{job.end or 'конец'}"
            html += (f"<div class='cdm-msg cdm-msg-ok'>✓ Задание <code>{up.esc(job.id)}</code> сохранено: источник и фрагмент "
                     f"{up.esc(sel)} не потеряются — повтор продолжит с этого места.</div>")
        if kind in ("auth", "age"):
            html += ("<p class='hint'>Cookies читает yt-dlp прямо из вашего браузера на этом компьютере; приложение их не "
                     "хранит и никуда не отправляет — в .env записывается только название браузера или путь к файлу.</p>")
        if kind == "partial":
            html += ("<p class='hint'>Весь ролик скачается один раз в кэш (jobs/_sources) — следующие обрезки этого ролика "
                     "пойдут без интернета.</p>")
        detail = getattr(e, "detail", "")
        if detail:
            html += f"<details><summary>Ответ yt-dlp</summary><pre>{up.esc(detail[-900:])}</pre></details>"
        acts = ACTIONS.get(kind, ())
        res = {modal: gr.update(visible=True), title: f"### {'Скачивание не получилось' if kind != 'range' else 'Скачать весь ролик?'}",
               body: html, cookies_row: gr.update(visible="cookies" in acts),
               browser: config.YTDLP_COOKIES_FROM_BROWSER or "", cfile: config.YTDLP_COOKIES or "",
               ctx: {**(op or {}), "kind": kind, "job": (op or {}).get("job") or (job.id if job else None)}}
        for name, b in buttons.items():
            res[b] = gr.update(visible=name in acts)
        size = getattr(e, "size", None)
        dur = getattr(e, "duration", None)
        bits = " · ".join(x for x in (ingest.fmt_duration(dur) if dur else "", f"≈ {size / 1e6:.0f} МБ" if size else "") if x)
        res[full] = gr.update(visible="full" in acts, value=f"Скачать весь ролик{f' ({bits})' if bits else ''}")
        return res

    close.click(lambda: gr.update(visible=False), None, modal, show_progress="hidden")
    return SimpleNamespace(modal=modal, title=title, body=body, browser=browser, cfile=cfile, ctx=ctx, open=open_,
                           outputs=outputs, close=close, **buttons)
