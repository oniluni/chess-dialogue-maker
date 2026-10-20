"""«Звуки» page: the notable sound events of a clip (sounds.py) — vocal emotions and
nonverbal sounds, changes in speech delivery, music, ambient noise — on a timeline and
in a list, with a form to correct them, add missing ones and choose which appear in
the video (a tag over the picture) and in the analysis.

An edited event is marked `edited`: a new detection keeps it. Uncertain events are
shown with a dashed outline and «предположение»; nothing is put into the video unless
it is reliable or the person ticked «Показать в видео».
"""
from __future__ import annotations

import logging
import time
from types import SimpleNamespace

import design
import moves_edit as me
import pipeline
import sounds as snd_mod
import ui_parts as up
from pipeline import Job

log = logging.getLogger(__name__)
PLAYER = "#sounds-player"
LIST = "#sounds-list"
CONFIRM_S = 6.0
FILTERS = [("Все", "all"), ("В видео", "included"), ("Голос", "voice"), ("Подача речи", "delivery"),
           ("Музыка", "music"), ("Фон", "ambient"), ("Предположения", "unsure")]


def _who(job: Job) -> list[tuple[str, str]]:
    return [("Не определён", ""), (f"Белые · {job.names['white']}", "white"), (f"Чёрные · {job.names['black']}", "black")]


def _label(e: snd_mod.Event, names: dict[str, str]) -> str:
    who = names.get(e.speaker or "", "")
    return up.SEP.join([f"{me.fmt_ts(e.start)}–{me.fmt_ts(e.end)}", e.category, e.label, who or "-",
                        "" if e.certain or e.edited else "?", "в видео" if e.include else ""])


def build(shell) -> SimpleNamespace:
    gr = shell.gr
    sel = gr.State(None)          # event id | "new" | None
    draft = gr.State(None)        # the new event being typed (dict) until saved
    clip_state = gr.State("")
    confirm = gr.State(None)
    t_now = gr.Number(value=0, visible="hidden", elem_id="snd-t")

    title = gr.Markdown("# Звуки", elem_id="sounds-title", elem_classes="page-title")
    empty = gr.HTML(up.empty_state("Нет задания", "Выберите задание слева."), container=False, padding=False)
    with gr.Column(visible=False, elem_id="sounds-work") as work:
        with gr.Row(elem_id="sounds-toolbar", elem_classes="cdm-toolbar"):
            detect_btn = gr.Button("Найти звуки", icon=design.icon("sparkles"), variant="secondary",
                                   elem_id="snd-detect", scale=0)
            add_btn = gr.Button("Добавить в точке плеера", icon=design.icon("plus", "text"), variant="secondary",
                                elem_id="snd-add", scale=0)
            all_btn = gr.Button("Показать все надёжные", icon=design.icon("check"), variant="secondary",
                                elem_id="snd-all", scale=0)
            info = gr.Markdown("", elem_classes="hint", elem_id="sounds-info")
            render_btn = gr.Button("Собрать видео", icon=design.icon("film", "dark"), variant="primary",
                                   elem_id="snd-render", scale=0)
        timeline = gr.HTML("", elem_id="sounds-timeline", container=False, padding=False)
        gr.HTML(up.transport(PLAYER), container=False, padding=False)
        with gr.Row(elem_id="sounds-grid", equal_height=False):
            with gr.Column(elem_id="sounds-left", min_width=0):
                player = gr.Video(show_label=False, interactive=False, elem_id="sounds-player", buttons=[],
                                  height="clamp(260px, calc(100vh - 460px), 520px)")
                flt = gr.Radio(FILTERS, value="all", show_label=False, container=False, elem_id="sounds-filter",
                               elem_classes="cdm-seg")
                listing = gr.Radio([], show_label=False, container=False, elem_id="sounds-list")
            with gr.Column(elem_id="sounds-form", elem_classes="cdm-form", min_width=0):
                head = gr.Markdown("", elem_id="sounds-head")
                with gr.Row(elem_classes="cdm-time-row"):
                    s_start = gr.Textbox(label="Начало", elem_id="e-start", min_width=90, max_lines=1)
                    take_s = gr.Button("", icon=design.icon("clock"), variant="secondary", elem_classes="cdm-icon-btn",
                                       scale=0, min_width=40)
                    s_end = gr.Textbox(label="Конец", elem_id="e-end", min_width=90, max_lines=1)
                    take_e = gr.Button("", icon=design.icon("clock"), variant="secondary", elem_classes="cdm-icon-btn",
                                       scale=0, min_width=40)
                    play_btn = gr.Button("Проиграть", icon=design.icon("play"), variant="secondary", scale=0, min_width=120)
                s_kind = gr.Dropdown(snd_mod.KIND_CHOICES, value="laughter", label="Что звучит", elem_id="e-kind")
                with gr.Row(elem_classes="cdm-pair"):
                    s_label = gr.Textbox(label="Подпись в видео", max_lines=1, elem_id="e-label",
                                         placeholder="Смех, Музыка, Пауза 2 с…")
                    s_who = gr.Radio([], label="Чей голос", elem_id="e-who", elem_classes="cdm-seg")
                s_desc = gr.Textbox(label="Описание (для разбора)", lines=2, max_lines=4, elem_id="e-desc",
                                    placeholder="что слышно и что это значит в сцене")
                with gr.Row(elem_classes="cdm-pair"):
                    s_include = gr.Checkbox(label="Показать в видео и учесть в разборе", elem_id="e-include")
                    s_certain = gr.Checkbox(label="Уверен, что это так (не предположение)", elem_id="e-certain")
                s_meta = gr.Markdown("", elem_classes="hint", elem_id="sounds-meta")
                errs = gr.Markdown("", elem_id="sounds-errors")
                with gr.Row(elem_classes="cdm-actions"):
                    save_btn = gr.Button("Сохранить", icon=design.icon("check", "dark"), variant="primary",
                                         elem_id="snd-save", scale=0)
                    del_btn = gr.Button("Удалить", icon=design.icon("trash"), variant="secondary", scale=0,
                                        elem_classes="cdm-danger", elem_id="snd-delete")

    form = [s_start, s_end, s_kind, s_label, s_who, s_desc, s_include, s_certain]
    outputs = [title, empty, work, timeline, player, clip_state, listing, sel, draft, head, *form, s_meta, errs, info,
               confirm, detect_btn]

    # ---------------------------------------------------------------- state -> components
    def shown(s: snd_mod.Sounds, f: str) -> list[snd_mod.Event]:
        ev = sorted(s.events, key=lambda e: e.start)
        if f == "included":
            return [e for e in ev if e.include]
        if f == "unsure":
            return [e for e in ev if not e.certain and not e.edited]
        if f in snd_mod.CATEGORIES:
            return [e for e in ev if e.category == f]
        return ev

    def load(jid, select=None, f: str = "all", new: dict | None = None, msg: str = "", clip_now: str = "") -> dict:
        if not jid:
            return {title: "# Звуки\nЭмоции в голосе, смех, музыка и шумы — с таймкодами.",
                    empty: gr.update(visible=True, value=up.empty_state("Нет задания", "Выберите задание слева.")),
                    work: gr.update(visible=False)}
        job = Job.load(jid)
        if not job.clip.exists():
            return {title: f"# Звуки\n{job.names['white']} vs {job.names['black']} · `{job.id}`",
                    empty: gr.update(visible=True, value=up.empty_state(
                        "Клип ещё не готов", "Звуки появятся, когда видео скачано и обрезано.")),
                    work: gr.update(visible=False)}
        s = snd_mod.Sounds.load(job.dir)
        dur = job.clip_duration()
        items = shown(s, f)
        if select == "new" and new is not None:
            cur, e = "new", snd_mod.Event(**new)
        else:
            e = s.event(select) or (items[0] if items else None)
            cur = e.id if e else None
        lanes = []
        for cat, name in snd_mod.CATEGORIES.items():
            lane = [{"start": x.start, "end": max(x.end, x.start + 0.3),
                     "cls": f"snd-{cat}" + (" is-key" if x.include else "") + ("" if x.certain or x.edited else " is-unsure"),
                     "label": x.label[:14], "title": f"{me.fmt_ts(x.start)} {x.label}: {x.description}", "value": x.id,
                     "list": LIST, "selected": e is not None and x.id == e.id} for x in s.events if x.category == cat]
            if lane:
                lanes.append((name, lane))
        n_inc = sum(1 for x in s.events if x.include)
        if not snd_mod.Sounds.exists(job.dir):
            info_md = "Звуки ещё не искали — нажмите «Найти звуки»."
        else:
            sure = sum(1 for x in s.events if x.certain or x.edited)
            info_md = (f"{len(s.events)} событий · надёжных {sure} · в видео {n_inc}"
                       + (f" · {s.notes[0]}" if s.notes else ""))
        if cur == "new":
            head_md = "### Новое событие\nВыберите, что звучит, проверьте время и сохраните."
        elif e is not None:
            k = snd_mod.KINDS.get(e.kind)
            rel = "надёжно" if e.certain or e.edited else "предположение"
            src = "исправлено вами" if e.edited else ("добавлено вами" if e.source == "user" else "найдено автоматически")
            head_md = f"### {e.label}\n{snd_mod.CATEGORIES.get(e.category, '')} · {rel} · {src}" + \
                      (f" · уверенность {e.confidence:.0%}" if e.source == "auto" and k and k.classes else "")
        else:
            head_md = "### Событий нет\nНажмите «Найти звуки» или добавьте событие в нужной точке видео."
        res = {
            title: f"# Звуки\n{job.names['white']} vs {job.names['black']} · `{job.id}`",
            empty: gr.update(visible=False), work: gr.update(visible=True),
            timeline: up.timeline(PLAYER, dur, lanes) if lanes else up.timeline(PLAYER, dur, [("События", [])]),
            listing: gr.update(choices=[(_label(x, job.names), x.id) for x in items],
                               value=cur if cur not in (None, "new") and any(x.id == cur for x in items) else None),
            sel: cur, draft: new if cur == "new" else None, head: head_md, errs: msg, info: info_md, confirm: None,
            detect_btn: gr.update(value="Найти звуки заново" if snd_mod.Sounds.exists(job.dir) else "Найти звуки"),
        }
        if e is not None:
            vals = [me.fmt_ts(e.start), me.fmt_ts(e.end), e.kind, e.label, e.speaker or "", e.description, e.include,
                    e.certain or e.edited]
            meta = "" if e.source == "user" or e.edited else \
                "Сохранение отметит событие как проверенное вами: повторный поиск его не тронет."
        else:
            vals, meta = ["", "", "laughter", "", "", "", False, False], ""
        for comp, v in zip(form, vals):
            res[comp] = v
        res[s_who] = gr.update(choices=_who(job), value=vals[4])
        res[s_meta] = meta
        if str(job.clip) != (clip_now or ""):
            res[player] = str(job.clip)
            res[clip_state] = str(job.clip)
        return res

    def read(vals, base: snd_mod.Event) -> tuple[snd_mod.Event | None, list[str]]:
        start, end, kind, label, who, desc, include, certain = vals
        t0, e0 = me.parse_field("Начало", start)
        t1, e1 = me.parse_field("Конец", end)
        if e0 or e1:
            return None, [x for x in (e0, e1) if x]
        k = snd_mod.KINDS.get(kind)
        label = " ".join((label or "").split()) or (k.label if k else "")
        errs = snd_mod.validate(t0, t1, kind, label, None)
        if errs:
            return None, errs
        ev = snd_mod.Event(base.id, round(t0, 2), round(t1, 2), kind, label, " ".join((desc or "").split()) or k.describe,
                           k.category, base.confidence, bool(certain), who or None, bool(include), base.source, True)
        return ev, []

    def same(a: snd_mod.Event, b: snd_mod.Event) -> bool:
        keys = ("kind", "label", "description", "speaker", "include")
        return all(getattr(a, k) == getattr(b, k) for k in keys) and abs(a.start - b.start) < 0.05 and \
            abs(a.end - b.end) < 0.05 and a.certain == (b.certain or b.edited)

    def commit(job: Job, s: snd_mod.Sounds, cur, vals, new_d) -> tuple[bool, list[str]]:
        if cur is None:
            return False, []
        base = snd_mod.Event(**new_d) if cur == "new" and new_d else s.event(cur)
        if base is None:
            return False, []
        ev, errs = read(vals, base)
        if ev is None:
            return False, errs
        if cur != "new" and same(ev, base):
            return False, []
        dur = job.clip_duration()
        if dur and ev.start >= dur:
            return False, [f"Начало ({me.fmt_ts(ev.start)}) дальше конца клипа ({me.fmt_ts(dur)})."]
        if cur == "new":
            s.events.append(ev)
        else:
            s.events = [ev if x.id == ev.id else x for x in s.events]
        if not s.method:
            s.method = "manual"
        s.save(job.dir)
        return True, []

    # ---------------------------------------------------------------- handlers
    errs_out = errs  # the Markdown; `errs` inside the handlers is a list of messages

    def up_msgs(errs_l: list[str], ok: str = "") -> str:
        out = [f"<div class='cdm-msg cdm-msg-error'>✗ {up.esc(e)}</div>" for e in errs_l]
        if ok:
            out.append(f"<div class='cdm-msg cdm-msg-ok'>✓ {up.esc(ok)}</div>")
        return "".join(out)

    def on_pick(jid, value, cur, f, clip_now, new_d, *vals):
        job = Job.load(jid)
        s = snd_mod.Sounds.load(job.dir)
        ok, errs = commit(job, s, cur, vals, new_d)
        if errs:
            return {listing: gr.update(value=None if cur == "new" else cur), errs_out: up_msgs(errs),
                    shell.status: "⚠️ Правки события не сохранены — исправьте ошибки"}
        return {**load(jid, value, f, clip_now=clip_now), shell.status: "✅ Событие сохранено" if ok else ""}

    def on_filter(jid, f, cur, clip_now):
        return load(jid, cur if cur != "new" else None, f, clip_now=clip_now)

    def on_add(jid, t, cur, f, clip_now, new_d, *vals):
        job = Job.load(jid)
        s = snd_mod.Sounds.load(job.dir)
        ok, errs = commit(job, s, cur, vals, new_d)
        if errs:
            return {errs_out: up_msgs(errs), shell.status: "⚠️ Сначала сохраните или исправьте текущее событие"}
        t = float(t or 0)
        dur = job.clip_duration() or t + 1.5
        k = snd_mod.KINDS["laughter"]
        ev = snd_mod.Event(snd_mod.new_id(), round(t, 2), round(min(dur, t + 1.5), 2), "laughter", k.label, k.describe,
                           k.category, 0.0, True, None, True, "user", True)
        from dataclasses import asdict

        return {**load(jid, "new", f, new=asdict(ev), clip_now=clip_now),
                shell.status: f"✚ Новое событие с {me.fmt_ts(t)} — выберите, что звучит, и сохраните"}

    def on_save(jid, cur, f, clip_now, new_d, *vals):
        job = Job.load(jid)
        s = snd_mod.Sounds.load(job.dir)
        if cur is None:
            return {shell.status: "Выберите событие или добавьте новое"}
        base = snd_mod.Event(**new_d) if cur == "new" and new_d else s.event(cur)
        ev, errs = read(vals, base) if base is not None else (None, ["Событие не найдено"])
        if ev is None:
            return {errs_out: up_msgs(errs), shell.status: f"❌ **Событие не сохранено:** {errs[0]}"}
        if cur == "new":
            s.events.append(ev)
        else:
            s.events = [ev if x.id == ev.id else x for x in s.events]
        s.method = s.method or "manual"
        s.save(job.dir)
        return {**load(jid, ev.id, f, clip_now=clip_now, msg=up_msgs([], "Сохранено")), shell.status: "✅ Событие сохранено"}

    def on_kind(kind, label, desc):
        """Picking a kind fills an empty / still automatic label and description."""
        k = snd_mod.KINDS.get(kind)
        if k is None:
            return gr.update(), gr.update()
        auto_labels = {x.label for x in snd_mod.KINDS.values()}
        auto_desc = {x.describe for x in snd_mod.KINDS.values()}
        return (k.label if not label or label in auto_labels else gr.update(),
                k.describe if not desc or desc in auto_desc else gr.update())

    def on_delete(jid, cur, f, clip_now, conf):
        if cur in (None, "new"):
            return {**load(jid, None, f, clip_now=clip_now), shell.status: "Черновик события отброшен"}
        if not (conf and conf[0] == "delete" and conf[1] == cur and time.time() - conf[2] < CONFIRM_S):
            return {confirm: ("delete", cur, time.time()),
                    shell.status: f"⚠️ Нажмите «Удалить» ещё раз в течение {CONFIRM_S:.0f} с, чтобы удалить событие"}
        job = Job.load(jid)
        s = snd_mod.Sounds.load(job.dir)
        s.events = [x for x in s.events if x.id != cur]
        s.save(job.dir)
        return {**load(jid, None, f, clip_now=clip_now), shell.status: "🗑 Событие удалено"}

    def on_all(jid, f, cur, clip_now):
        job = Job.load(jid)
        s = snd_mod.Sounds.load(job.dir)
        n = 0
        for x in s.events:
            if (x.certain or x.edited) and not x.include and x.kind != "pause":
                x.include, n = True, n + 1
        s.save(job.dir)
        return {**load(jid, cur if cur != "new" else None, f, clip_now=clip_now),
                shell.status: f"✅ Добавлено в видео: {n}" if n else "Все надёжные события уже в видео"}

    def on_detect(jid, f, clip_now):
        def action(run):
            run.job = shell.need_job(jid)
            pipeline.step_sounds(run.job)

        yield from shell.live(action, "✅ Звуки найдены — проверьте их и отметьте, что показать в видео", "Найти звуки",
                              finish=lambda job: load(job.id, None, f, clip_now=clip_now))

    def on_render(jid, clip_now):
        def action(run):
            run.job = shell.need_job(jid)
            if not run.job.moves_file.exists():
                raise ValueError("Ходов ещё нет: сначала создайте разбор.")
            pipeline.step_render(run.job, run.progress)

        yield from shell.live(action, "✅ Видео собрано — оно в предпросмотре на странице «Создание видео»", "Собрать видео",
                              finish=lambda job: load(job.id, clip_now=clip_now))

    # ---------------------------------------------------------------- wiring
    hid = dict(show_progress="hidden")
    st = [shell.status]
    seek = f"(s) => {{ window.cdm && cdm.seek('{PLAYER}', s); return []; }}"
    now = f"() => [window.cdm ? cdm.now('{PLAYER}') : '']"
    with_t = f"(jid, t, ...rest) => [jid, window.cdm ? cdm.nowSec('{PLAYER}') : 0, ...rest]"
    listing.input(on_pick, [shell.job_id, listing, sel, flt, clip_state, draft, *form], outputs + st, **hid) \
        .then(None, [s_start], None, js=seek)
    flt.input(on_filter, [shell.job_id, flt, sel, clip_state], outputs, **hid)
    add_btn.click(on_add, [shell.job_id, t_now, sel, flt, clip_state, draft, *form], outputs + st, js=with_t, **hid)
    save_btn.click(on_save, [shell.job_id, sel, flt, clip_state, draft, *form], outputs + st, **hid)
    del_btn.click(on_delete, [shell.job_id, sel, flt, clip_state, confirm], outputs + st, **hid)
    all_btn.click(on_all, [shell.job_id, flt, sel, clip_state], outputs + st, **hid)
    s_kind.change(on_kind, [s_kind, s_label, s_desc], [s_label, s_desc], **hid)
    take_s.click(None, None, [s_start], js=now)
    take_e.click(None, None, [s_end], js=now)
    play_btn.click(None, [s_start, s_end], None, js=f"(a, b) => {{ window.cdm && cdm.playRange('{PLAYER}', a, b); return []; }}")
    detect_btn.click(on_detect, [shell.job_id, flt, clip_state], shell.dock + outputs, **hid)
    render_btn.click(on_render, [shell.job_id, clip_state], shell.outs + outputs, **hid)

    return SimpleNamespace(load=lambda jid: load(jid, clip_now=""), outputs=outputs)
