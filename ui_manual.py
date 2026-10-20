"""«Ручной разбор» page: a workspace for analysing a dialogue by hand.

    video + timeline   watch, scrub, jump to any line or key moment
    lines              who says what and when — typed in, or drafted from the speech
                       recognition (and an existing automatic analysis)
    ratings            key line ★, strength −3…+3, accuracy / persuasiveness, tactics,
                       chess motif, position after the line, a note
    structure          key moments = the moves of the review, with titles and
                       explanations; the board shows the sequence before rendering
    build              moments -> moves.json (then fine-tune on «Ходы» or render)

Everything is saved to dialogue.json on each change; nothing here needs the LLM.
"""
from __future__ import annotations

import logging
import time
from types import SimpleNamespace

import dialogue as dlg
import design
import moves_edit as me
import pipeline
import renderer
import ui_parts as up
from pipeline import Job

log = logging.getLogger(__name__)
PLAYER = "#manual-player"
CONFIRM_S = 6.0
FILTERS = [("Все", "all"), ("★ Ключевые", "key"), ("Без говорящего", "nospk"), ("Вне моментов", "loose")]
NEW_MOMENT = "__new__"


def _speakers(job: Job, allow_none: bool) -> list[tuple[str, str]]:
    out = [(f"Белые · {job.names['white']}", "white"), (f"Чёрные · {job.names['black']}", "black")]
    return out + [("Не определён", "")] if allow_none else [("Авто — по репликам", "")] + out


def _with_lines(d: dlg.Dialogue) -> set[str]:
    return {m.id for m in d.moments if d.moment_lines(m.id)}


def _orphaned(d: dlg.Dialogue, before: set[str]) -> str:
    """A status suffix for moments that lost their last line (they block «Собрать ходы»)."""
    gone = [m for m in d.moments if m.id in before and not d.moment_lines(m.id)]
    if not gone:
        return ""
    names = ", ".join(f"«{dlg.short(m.title or 'без заголовка', 40)}»" for m in gone)
    return f" · ⚠ момент {names} остался без реплик — удалите его или привяжите к нему реплику"


def _msgs(errs: list[str], warns: list[str] | None = None, ok: str = "") -> str:
    out = [f"<div class='cdm-msg cdm-msg-error'>✗ {up.esc(e)}</div>" for e in errs]
    out += [f"<div class='cdm-msg cdm-msg-warn'>⚠ {up.esc(w)}</div>" for w in (warns or [])]
    if ok:
        out.append(f"<div class='cdm-msg cdm-msg-ok'>✓ {up.esc(ok)}</div>")
    return "".join(out)


def build(shell) -> SimpleNamespace:
    gr = shell.gr
    line_sel = gr.State(None)       # line id | "new" | None
    moment_sel = gr.State(None)     # moment id | None
    draft_state = gr.State(None)    # the new line being typed (dict) until it is saved
    clip_state = gr.State("")
    confirm = gr.State(None)
    t_now = gr.Number(value=0, visible="hidden", elem_id="ln-t")

    title = gr.Markdown("# Ручной разбор", elem_id="manual-title", elem_classes="page-title")
    empty = gr.HTML("", container=False, padding=False)
    goto_create = gr.Button("Перейти к созданию видео", icon=design.icon("plus", "text"), variant="secondary",
                            elem_id="md-goto-create", visible=False)
    with gr.Column(visible=False, elem_id="manual-work") as work:
        with gr.Row(elem_id="manual-toolbar", elem_classes="cdm-toolbar"):
            import_btn = gr.Button("Реплики из распознанной речи", icon=design.icon("sparkles"), variant="secondary",
                                   elem_id="md-import", scale=0)
            add_btn = gr.Button("Добавить реплику в точке плеера", icon=design.icon("plus", "text"), variant="secondary",
                                elem_id="ln-add", scale=0)
            saved = gr.Markdown("", elem_classes="hint", elem_id="manual-saved")
            render_btn = gr.Button("Собрать видео", icon=design.icon("film", "dark"), variant="primary",
                                   elem_id="md-render", scale=0)
        gr.HTML('<div id="sec-m-video" class="sec-anchor"></div>', container=False, padding=False)
        timeline = gr.HTML("", elem_id="manual-timeline", container=False, padding=False)
        gr.HTML(up.transport(PLAYER), container=False, padding=False)
        with gr.Row(elem_id="manual-grid", equal_height=False):
            with gr.Column(elem_id="manual-left", min_width=0):
                player = gr.Video(show_label=False, interactive=False, elem_id="manual-player", buttons=[],
                                  height="clamp(260px, calc(100vh - 460px), 520px)")
                gr.HTML('<div id="sec-m-lines" class="sec-anchor"></div><div class="outline-title">Реплики</div>',
                        container=False, padding=False)
                flt = gr.Radio(FILTERS, value="all", show_label=False, container=False, elem_id="lines-filter",
                               elem_classes="cdm-seg")
                lines_list = gr.Radio([], show_label=False, container=False, elem_id="lines-list")
            with gr.Column(elem_id="line-form", elem_classes="cdm-form", min_width=0):
                l_head = gr.Markdown("", elem_id="line-head")
                with gr.Row(elem_classes="cdm-time-row"):
                    l_start = gr.Textbox(label="Начало", elem_id="l-start", min_width=90, max_lines=1)
                    take_s = gr.Button("", icon=design.icon("clock"), variant="secondary", elem_classes="cdm-icon-btn",
                                       scale=0, min_width=40)
                    l_end = gr.Textbox(label="Конец", elem_id="l-end", min_width=90, max_lines=1)
                    take_e = gr.Button("", icon=design.icon("clock"), variant="secondary", elem_classes="cdm-icon-btn",
                                       scale=0, min_width=40)
                    play_line = gr.Button("Проиграть", icon=design.icon("play"), variant="secondary", scale=0, min_width=120)
                l_speaker = gr.Radio([], label="Говорит", elem_id="l-speaker", elem_classes="cdm-seg")
                l_text = gr.Textbox(label="Текст реплики", lines=2, max_lines=5, elem_id="l-text",
                                    placeholder="Что сказано — дословно или кратко")
                l_key = gr.Checkbox(label="★ Ключевая реплика — войдёт в разбор", elem_id="l-key")
                with gr.Row(elem_classes="cdm-trio"):
                    l_strength = gr.Slider(-3, 3, value=0, step=1, label="Сила хода", elem_id="l-strength",
                                           min_width=150, buttons=[])
                    l_accuracy = gr.Slider(0, 5, value=0, step=1, label="Точность", elem_id="l-accuracy",
                                           min_width=140, buttons=[])
                    l_persuasion = gr.Slider(0, 5, value=0, step=1, label="Убедительность", elem_id="l-persuasion",
                                             min_width=150, buttons=[])
                gr.Markdown("Сила: −3 провал · 0 нейтрально · +3 блестяще. Точность и убедительность — от 1 до 5, 0 — не оценено.",
                            elem_classes="hint", elem_id="rating-hint")
                l_tactics = gr.CheckboxGroup(dlg.TACTICS, label="Приёмы", elem_id="l-tactics", elem_classes="cdm-chips")
                with gr.Row(elem_classes="cdm-pair"):
                    l_motif = gr.Dropdown([("— нет —", "")] + [(m, m) for m in dlg.MOTIFS], value="",
                                          label="Шахматный мотив", elem_id="l-motif", min_width=150)
                    with gr.Column(min_width=180):
                        l_eval_on = gr.Checkbox(label="Задать позицию после реплики", elem_id="l-eval-on")
                        l_eval = gr.Slider(-10, 10, value=0, step=0.5, label="Позиция: + белые, − чёрные",
                                           elem_id="l-eval", buttons=[])
                l_note = gr.Textbox(label="Пояснение к реплике", lines=2, max_lines=4, elem_id="l-note",
                                    placeholder="Почему это сильно или слабо — пойдёт в объяснение хода")
                l_moment = gr.Dropdown([], label="Ключевой момент (ход разбора)", elem_id="l-moment")
                l_errs = gr.Markdown("", elem_id="line-errors")
                with gr.Row(elem_classes="cdm-actions"):
                    l_save = gr.Button("Сохранить реплику", icon=design.icon("check", "dark"), variant="primary",
                                       elem_id="ln-save", scale=0)
                    l_split = gr.Button("Разделить в точке плеера", icon=design.icon("scissors"), variant="secondary", scale=0)
                    l_merge = gr.Button("Склеить со следующей", icon=design.icon("merge"), variant="secondary", scale=0)
                    l_delete = gr.Button("Удалить", icon=design.icon("trash"), variant="secondary", scale=0,
                                         elem_classes="cdm-danger")

        gr.Markdown("## Структура разбора\nКлючевые моменты — это ходы будущего видео. Проверьте последовательность, "
                    "заголовки и объяснения, потом соберите ходы.", elem_id="sec-m-structure", elem_classes="sec")
        board = gr.HTML("", elem_id="structure-board", container=False, padding=False)
        with gr.Row(elem_id="moment-grid", equal_height=False):
            with gr.Column(elem_id="moment-left", min_width=0):
                moments_list = gr.Radio([], show_label=False, container=False, elem_id="moments-list")
                build_btn = gr.Button("Собрать ходы из разбора", icon=design.icon("sparkles", "dark"), variant="primary",
                                      elem_id="md-build")
                report = gr.Markdown("", elem_id="build-report")
                to_moves = gr.Button("Открыть в «Ходах»", icon=design.icon("list"), variant="secondary",
                                     elem_id="md-to-moves")
            with gr.Column(elem_id="moment-form", elem_classes="cdm-form", min_width=0):
                mo_head = gr.Markdown("", elem_id="moment-head")
                mo_title = gr.Textbox(label="Заголовок карточки", max_lines=2, elem_id="mo-title",
                                      placeholder="Крючок с вердиктом: «… — зевок ферзя»")
                mo_gist = gr.Textbox(label="Подзаголовок — суть хода", max_lines=1, elem_id="mo-gist",
                                     placeholder="если пусто — из мотива и приёмов реплик")
                mo_expl = gr.Textbox(label="Объяснение", lines=3, max_lines=6, elem_id="mo-expl",
                                     placeholder="если пусто — из пояснений к репликам")
                with gr.Row(elem_classes="cdm-pair"):
                    mo_type = gr.Dropdown([("Авто — по силе реплик", "")] + me.TYPE_CHOICES, value="",
                                          label="Тип хода", elem_id="mo-type", min_width=170)
                    mo_speaker = gr.Radio([], label="Чей ход", elem_id="mo-speaker", elem_classes="cdm-seg")
                with gr.Row(elem_classes="cdm-pair"):
                    mo_eval_on = gr.Checkbox(label="Задать оценку вручную", elem_id="mo-eval-on", min_width=150)
                    mo_eval = gr.Slider(-10, 10, value=0, step=0.1, label="Оценка после хода", elem_id="mo-eval", buttons=[],
                                        min_width=200)
                mo_errs = gr.Markdown("", elem_id="moment-errors")
                with gr.Row(elem_classes="cdm-actions"):
                    mo_save = gr.Button("Сохранить момент", icon=design.icon("check", "dark"), variant="primary", scale=0)
                    mo_delete = gr.Button("Удалить момент", icon=design.icon("trash"), variant="secondary", scale=0,
                                          elem_classes="cdm-danger")
                gr.HTML('<div class="outline-title">Карточка в видео</div>', container=False, padding=False)
                mo_preview = gr.Image(show_label=False, interactive=False, type="pil", elem_id="moment-preview", buttons=[])

    lform = [l_start, l_end, l_speaker, l_text, l_key, l_strength, l_accuracy, l_persuasion, l_tactics, l_motif,
             l_eval_on, l_eval, l_note, l_moment]
    mform = [mo_title, mo_gist, mo_expl, mo_type, mo_speaker, mo_eval_on, mo_eval]
    outputs = [title, empty, goto_create, work, saved, timeline, player, clip_state, lines_list, line_sel, l_head, *lform,
               l_errs, board, moments_list, moment_sel, report, mo_head, *mform, mo_errs, mo_preview, confirm, draft_state,
               import_btn]

    # ---------------------------------------------------------------- form <-> data
    def line_values(ln: dlg.Line) -> list:
        return [me.fmt_ts(ln.start), me.fmt_ts(ln.end), ln.speaker or "", ln.text, ln.key, ln.strength or 0,
                ln.accuracy or 0, ln.persuasion or 0, list(ln.tactics), ln.motif or "", ln.eval is not None,
                ln.eval if ln.eval is not None else 0, ln.note, ln.moment or ""]

    def read_line(vals, base: dlg.Line) -> tuple[dlg.Line | None, list[str]]:
        """(line, errors); the line is None when a timecode is empty or does not parse — such an
        edit is never "unchanged", so switching to another line cannot drop it silently."""
        (start, end, speaker, text, key, strength, accuracy, persuasion, tactics, motif, eval_on, eval_v, note,
         moment) = vals
        t0, e0 = me.parse_field("Начало", start)
        t1, e1 = me.parse_field("Конец", end)
        if e0 or e1:
            return None, [e for e in (e0, e1) if e]
        if t0 is None or t1 is None:
            return None, dlg.validate_line(t0, t1, text, None)
        ln = dlg.Line(id=base.id, start=t0, end=t1,
                      speaker=speaker or None, text=" ".join((text or "").split()), key=bool(key),
                      strength=int(strength) or None, accuracy=int(accuracy) or None, persuasion=int(persuasion) or None,
                      tactics=list(tactics or []), motif=motif or "", eval=float(eval_v) if eval_on else None,
                      note=(note or "").strip(), moment=moment or None)
        return ln, dlg.validate_line(t0, t1, ln.text, None)

    def same_line(a: dlg.Line, b: dlg.Line) -> bool:
        keys = ("start", "end", "speaker", "text", "key", "strength", "accuracy", "persuasion", "tactics", "motif",
                "eval", "note", "moment")
        return all(getattr(a, k) == getattr(b, k) for k in keys if k not in ("start", "end")) and \
            abs(a.start - b.start) < 0.05 and abs(a.end - b.end) < 0.05

    def moment_values(mo: dlg.Moment | None) -> list:
        if mo is None:
            return ["", "", "", "", "", False, 0]
        return [mo.title, mo.gist, mo.explanation, mo.move_type or "", mo.speaker or "", mo.eval is not None,
                mo.eval if mo.eval is not None else 0]

    def read_moment(vals, base: dlg.Moment) -> dlg.Moment:
        t, g, e, typ, spk, eval_on, eval_v = vals
        return dlg.Moment(id=base.id, title=" ".join((t or "").split()), gist=" ".join((g or "").split()),
                          explanation=" ".join((e or "").split()), move_type=typ or None, speaker=spk or None,
                          eval=float(eval_v) if eval_on else None)

    def same_moment(a: dlg.Moment, b: dlg.Moment) -> bool:
        return (a.title, a.gist, a.explanation, a.move_type, a.speaker, a.eval) == \
               (b.title, b.gist, b.explanation, b.move_type, b.speaker, b.eval)

    def apply_new_moment(d: dlg.Dialogue, ln: dlg.Line) -> dlg.Moment | None:
        if ln.moment == NEW_MOMENT:
            mo = dlg.Moment(id=dlg.new_id("m"), title="", speaker=None)
            d.moments.append(mo)
            ln.moment = mo.id
            ln.key = True
            return mo
        return None

    # ---------------------------------------------------------------- state -> components
    def visible_lines(d: dlg.Dialogue, f: str) -> list[dlg.Line]:
        lines = sorted(d.lines, key=lambda x: x.start)
        if f == "key":
            return [x for x in lines if x.key]
        if f == "nospk":
            return [x for x in lines if not x.speaker]
        if f == "loose":
            return [x for x in lines if not x.moment]
        return lines

    def moment_preview(job: Job, d: dlg.Dialogue, mid: str | None):
        if not mid:
            return None
        try:
            moves, index = dlg.preview_moves(d, job.names)
            if mid not in index:
                return None
            return renderer.preview_card(moves, index[mid], job.names, pipeline.preview_avatars(job), job.output_format)
        except Exception as e:
            log.debug("moment preview: %s", e)
            return None

    def load(jid, line=None, moment=None, f: str = "all", draft: dlg.Line | None = None, line_msg: str = "",
             moment_msg: str = "", report_msg: str | None = None, clip_now: str = "") -> dict:
        if not jid:
            return {title: "# Ручной разбор\nСамостоятельный разбор диалога: реплики, оценки, ключевые моменты.",
                    empty: gr.update(visible=True, value=up.empty_state(
                        "Нет задания", "Выберите задание слева — или подготовьте новое видео на странице «Создание видео» "
                        "кнопкой «Подготовить для ручного разбора»: ролик скачается и обрежется без нейросети.")),
                    goto_create: gr.update(visible=True), work: gr.update(visible=False)}
        job = Job.load(jid)
        head_title = f"# Ручной разбор\n{job.names['white']} vs {job.names['black']} · `{job.id}`"
        if not job.clip.exists():
            return {title: head_title,
                    empty: gr.update(visible=True, value=up.empty_state(
                        "Клип ещё не готов", "Нажмите «Продолжить выбранное» слева или подготовьте видео заново.")),
                    goto_create: gr.update(visible=True), work: gr.update(visible=False)}
        d = dlg.Dialogue.load(job.dir)
        dur = job.clip_duration()
        planned = dlg.plan(d)
        numbers = {p.moment.id: p.number for p in planned}
        shown = visible_lines(d, f)
        # which line is in the form
        if line == "new" and draft is not None:
            cur, ln = "new", draft
        else:
            ln = d.line(line) or (shown[0] if shown else None)
            cur = ln.id if ln else ("new" if draft is not None else None)
            if ln is None and draft is not None:
                ln = draft
        mo = d.moment(moment) or (d.moment(ln.moment) if ln and ln.moment else None) or \
            (planned[0].moment if planned else None)
        moment_choices = [("— не входит ни в один момент —", ""), ("＋ Новый ключевой момент", NEW_MOMENT)] + \
            [(f"{p.number} · {p.title or 'без заголовка'}", p.moment.id) for p in planned]
        lanes = [
            ("Моменты", [{"start": p.start, "end": p.end, "cls": f"mt-{p.move_type}" + (" has-problems" if p.problems else ""),
                          "label": str(p.number), "title": f"Ход {p.number}: {p.title or 'без заголовка'}",
                          "value": p.moment.id, "list": "#moments-list", "selected": mo is not None and p.moment.id == mo.id}
                         for p in planned if p.lines]),
            ("Реплики", [{"start": x.start, "end": x.end, "cls": f"spk-{x.speaker or 'none'}" + (" is-key" if x.key else ""),
                          "label": "★" if x.key else "", "title": f"{me.fmt_ts(x.start)} {dlg.short(x.text, 90)}",
                          "value": x.id, "list": "#lines-list", "selected": ln is not None and x.id == ln.id}
                         for x in sorted(d.lines, key=lambda y: y.start)]),
        ]
        if cur == "new":
            l_head_md = "### Новая реплика\nВпишите текст и нажмите «Сохранить реплику»."
        elif ln is not None:
            pos = next((i for i, x in enumerate(sorted(d.lines, key=lambda y: y.start), 1) if x.id == ln.id), 0)
            l_head_md = f"### Реплика {pos} из {len(d.lines)}"
        else:
            l_head_md = "### Реплик пока нет\nДобавьте первую в нужной точке видео или возьмите черновик из распознанной речи."
        if mo is not None:
            n = numbers.get(mo.id, 0)
            mo_head_md = f"### Момент {n}" + (" · без реплик" if not d.moment_lines(mo.id) else "")
        else:
            mo_head_md = "### Момент не выбран\nОтметьте реплику ★ и выберите «＋ Новый ключевой момент»."
        if report_msg is None:
            report_msg = _report(job, d)
        stamp = time.strftime("%H:%M:%S", time.localtime(d.updated)) if d.updated else ""
        keys = sum(1 for x in d.lines if x.key)
        res = {
            title: head_title, empty: gr.update(visible=False), goto_create: gr.update(visible=False),
            work: gr.update(visible=True),
            saved: (f"Сохранено в {stamp} · {len(d.lines)} реплик · ★ {keys} · моментов {len(planned)}" if stamp
                    else "Разбор ещё пуст — начните с реплик"),
            timeline: up.timeline(PLAYER, dur, lanes),
            lines_list: gr.update(choices=[(up.line_label(x, numbers.get(x.moment or "", 0)), x.id) for x in shown],
                                  value=cur if cur not in (None, "new") and any(x.id == cur for x in shown) else None),
            line_sel: cur, l_head: l_head_md,
            l_errs: line_msg,
            board: up.board(planned, job.names, mo.id if mo else None),
            moments_list: gr.update(choices=[(up.moment_label(p), p.moment.id) for p in planned],
                                    value=mo.id if mo else None),
            moment_sel: mo.id if mo else None, report: report_msg, mo_head: mo_head_md, mo_errs: moment_msg,
            mo_preview: moment_preview(job, d, mo.id if mo else None), confirm: None,
            draft_state: None if cur != "new" else _as_dict(ln),
            import_btn: gr.update(value="Реплики из распознанной речи" if job.transcript.exists()
                                  else "Распознать речь и взять реплики"),
        }
        values = line_values(ln) if ln is not None else ["", "", "", "", False, 0, 0, 0, [], "", False, 0, "", ""]
        for comp, v in zip(lform, values):
            res[comp] = v
        res[l_speaker] = gr.update(choices=_speakers(job, True), value=values[2])
        res[l_moment] = gr.update(choices=moment_choices, value=values[13] if values[13] in {c[1] for c in moment_choices} else "")
        for comp, v in zip(mform, moment_values(mo)):
            res[comp] = v
        res[mo_speaker] = gr.update(choices=_speakers(job, False), value=(mo.speaker or "") if mo else "")
        if str(job.clip) != (clip_now or ""):
            res[player] = str(job.clip)
            res[clip_state] = str(job.clip)
        return res

    def _as_dict(ln):
        from dataclasses import asdict

        return asdict(ln) if ln is not None else None

    def _report(job: Job, d: dlg.Dialogue) -> str:
        if not job.moves_file.exists():
            return "<div class='cdm-msg'>Ходов ещё нет — соберите их из моментов.</div>"
        stale = d.updated and d.updated > job.moves_file.stat().st_mtime + 1
        n = len(job.load_moves())
        txt = f"В видео сейчас {n} ходов."
        if stale:
            return _msgs([], [f"{txt} Разбор изменён после последней сборки — нажмите «Собрать ходы из разбора»."])
        return f"<div class='cdm-msg cdm-msg-ok'>✓ {txt}</div>"

    def commit_line(job: Job, d: dlg.Dialogue, cur, vals, draft_d) -> tuple[bool, list[str], dlg.Line | None]:
        """Save the line in the form if it changed. (saved, errors, line)."""
        if cur is None:
            return False, [], None
        if cur == "new":
            base = dlg.Line(**draft_d) if draft_d else dlg.Line(id=dlg.new_id("l"), start=0, end=1)
        else:
            base = d.line(cur)
            if base is None:
                return False, [], None
        ln, errs = read_line(vals, base)
        if ln is None:
            return False, errs, None
        if cur == "new" and not ln.text and not ln.note:
            return False, [], None  # an untouched draft: nothing to keep
        if cur != "new" and same_line(ln, base):
            return False, [], base
        if errs:
            return False, errs, None
        apply_new_moment(d, ln)
        if cur == "new":
            d.lines.append(ln)
        else:
            d.lines = [ln if x.id == ln.id else x for x in d.lines]
        d.save(job.dir)
        return True, [], ln

    def commit_moment(job: Job, d: dlg.Dialogue, mid, vals) -> bool:
        base = d.moment(mid)
        if base is None:
            return False
        mo = read_moment(vals, base)
        if same_moment(mo, base):
            return False
        d.moments = [mo if x.id == mo.id else x for x in d.moments]
        d.save(job.dir)
        return True

    # ---------------------------------------------------------------- handlers
    def on_line_pick(jid, value, cur, f, mid, clip_now, draft_d, *vals):
        job = Job.load(jid)
        d = dlg.Dialogue.load(job.dir)
        before = _with_lines(d)
        ok, errs, _ = commit_line(job, d, cur, vals, draft_d)
        if errs:
            return {lines_list: gr.update(value=None if cur == "new" else cur), l_errs: _msgs(errs),
                    shell.status: "⚠️ Правки реплики не сохранены — исправьте ошибки"}
        return {**load(jid, value, mid, f, clip_now=clip_now),
                shell.status: "✅ Реплика сохранена" + _orphaned(d, before) if ok else ""}

    def on_filter(jid, f, cur, mid, clip_now):
        return load(jid, cur if cur != "new" else None, mid, f, clip_now=clip_now)

    def on_line_add(jid, t, cur, f, mid, clip_now, draft_d, *vals):
        job = Job.load(jid)
        d = dlg.Dialogue.load(job.dir)
        ok, errs, _ = commit_line(job, d, cur, vals, draft_d)
        if errs:
            return {l_errs: _msgs(errs), shell.status: "⚠️ Сначала сохраните или исправьте текущую реплику"}
        draft = dlg.new_line(d, float(t or 0), job.clip_duration())
        return {**load(jid, "new", mid, f, draft=draft, clip_now=clip_now),
                shell.status: f"✚ Новая реплика с {me.fmt_ts(draft.start)} — впишите текст и сохраните"}

    def on_line_save(jid, cur, f, mid, clip_now, draft_d, *vals):
        job = Job.load(jid)
        d = dlg.Dialogue.load(job.dir)
        if cur is None:
            return {shell.status: "Выберите реплику или добавьте новую"}
        base = dlg.Line(**draft_d) if cur == "new" and draft_d else (d.line(cur) or dlg.Line(id=dlg.new_id("l"), start=0, end=1))
        ln, errs = read_line(vals, base)
        dur = job.clip_duration()
        if not errs and dur and ln.start >= dur:
            errs = [f"Начало ({me.fmt_ts(ln.start)}) дальше конца клипа ({me.fmt_ts(dur)})."]
        if errs:
            return {l_errs: _msgs(errs), shell.status: f"❌ **Реплика не сохранена:** {errs[0]}"}
        before = _with_lines(d)
        created = apply_new_moment(d, ln)
        if cur == "new":
            d.lines.append(ln)
        else:
            d.lines = [ln if x.id == ln.id else x for x in d.lines]
        d.save(job.dir)
        if created is not None:
            return {**load(jid, ln.id, created.id, f, clip_now=clip_now,
                           moment_msg=_msgs([], ["Новый момент: впишите заголовок и объяснение и сохраните момент."])),
                    shell.status: "✅ Реплика сохранена и стала ключевым моментом — заполните его в «Структуре разбора»"
                                  + _orphaned(d, before)}
        return {**load(jid, ln.id, mid, f, clip_now=clip_now, line_msg=_msgs([], [], "Реплика сохранена")),
                shell.status: "✅ Реплика сохранена" + _orphaned(d, before)}

    def on_split(jid, t, cur, f, mid, clip_now):
        job = Job.load(jid)
        d = dlg.Dialogue.load(job.dir)
        if cur in (None, "new"):
            return {shell.status: "Выберите сохранённую реплику, чтобы разделить её"}
        second = dlg.split_line(d, cur, float(t or 0))
        if second is None:
            ln = d.line(cur)
            span = f"{me.fmt_ts(ln.start)}–{me.fmt_ts(ln.end)}" if ln else ""
            return {shell.status: f"⚠️ Поставьте плеер внутрь реплики ({span}), не у самого края, и нажмите снова"}
        d.save(job.dir)
        return {**load(jid, second.id, mid, f, clip_now=clip_now),
                shell.status: f"✂ Реплика разделена в {me.fmt_ts(float(t))} — проверьте, как разделился текст"}

    def on_merge(jid, cur, f, mid, clip_now):
        job = Job.load(jid)
        d = dlg.Dialogue.load(job.dir)
        before = _with_lines(d)
        if cur in (None, "new") or not dlg.merge_next(d, cur):
            return {shell.status: "⚠️ Склеить можно сохранённую реплику, у которой есть следующая"}
        d.save(job.dir)
        return {**load(jid, cur, mid, f, clip_now=clip_now), shell.status: "🔗 Реплики склеены" + _orphaned(d, before)}

    def on_line_delete(jid, cur, f, mid, clip_now, conf):
        if cur in (None, "new"):
            return {**load(jid, None, mid, f, clip_now=clip_now), shell.status: "Черновик реплики отброшен"}
        if not (conf and conf[0] == "line" and conf[1] == cur and time.time() - conf[2] < CONFIRM_S):
            return {confirm: ("line", cur, time.time()),
                    shell.status: f"⚠️ Нажмите «Удалить» ещё раз в течение {CONFIRM_S:.0f} с, чтобы удалить реплику"}
        job = Job.load(jid)
        d = dlg.Dialogue.load(job.dir)
        before = _with_lines(d)
        dlg.delete_line(d, cur)
        d.save(job.dir)
        return {**load(jid, None, mid, f, clip_now=clip_now), shell.status: "🗑 Реплика удалена" + _orphaned(d, before)}

    def on_moment_pick(jid, value, mid, cur, f, clip_now, *mvals):
        job = Job.load(jid)
        d = dlg.Dialogue.load(job.dir)
        saved_m = commit_moment(job, d, mid, mvals) if mid else False
        return {**load(jid, cur if cur != "new" else None, value, f, clip_now=clip_now),
                shell.status: "✅ Момент сохранён" if saved_m else ""}

    def on_moment_save(jid, mid, cur, f, clip_now, *mvals):
        job = Job.load(jid)
        d = dlg.Dialogue.load(job.dir)
        if not mid or d.moment(mid) is None:
            return {shell.status: "Выберите момент в «Структуре разбора»"}
        commit_moment(job, d, mid, mvals)
        p = next((x for x in dlg.plan(d) if x.moment.id == mid), None)
        warn = p.problems if p else []
        return {**load(jid, cur if cur != "new" else None, mid, f, clip_now=clip_now, moment_msg=_msgs([], warn,
                       "" if warn else "Момент сохранён")),
                shell.status: "✅ Момент сохранён" + (f" · осталось: {warn[0]}" if warn else "")}

    def on_moment_delete(jid, mid, cur, f, clip_now, conf):
        if not mid:
            return {shell.status: "Выберите момент"}
        if not (conf and conf[0] == "moment" and conf[1] == mid and time.time() - conf[2] < CONFIRM_S):
            return {confirm: ("moment", mid, time.time()),
                    shell.status: "⚠️ Нажмите ещё раз, чтобы удалить момент (реплики останутся, но выйдут из разбора)"}
        job = Job.load(jid)
        d = dlg.Dialogue.load(job.dir)
        d.moments = [x for x in d.moments if x.id != mid]
        for x in d.lines:
            if x.moment == mid:
                x.moment = None
        d.save(job.dir)
        return {**load(jid, cur if cur != "new" else None, None, f, clip_now=clip_now), shell.status: "🗑 Момент удалён"}

    def on_import(jid, f, clip_now, conf):
        job = shell.need_job(jid)
        d = dlg.Dialogue.load(job.dir)
        if d.lines and not (conf and conf[0] == "import" and time.time() - conf[2] < CONFIRM_S):
            yield {confirm: ("import", None, time.time()),
                   shell.status: f"⚠️ В разборе уже {len(d.lines)} реплик. Нажмите ещё раз, чтобы заменить их черновиком "
                                 "из распознанной речи (текущий разбор сохранится в dialogue.backup.json)"}
            return

        def action(run):
            run.job = shell.need_job(jid)
            if dlg.Dialogue.exists(run.job.dir):
                (run.job.dir / "dialogue.backup.json").write_text((run.job.dir / dlg.FILE).read_text())
            if not run.job.transcript.exists():
                pipeline.prepare_manual(run.job, transcribe=True, progress=run.progress)
            pipeline.import_dialogue(run.job)

        yield from shell.live(action, "✅ Реплики взяты из распознанной речи — проверьте говорящих и отметьте ключевые",
                              "Реплики из распознанной речи", finish=lambda job: load(job.id, f="all", clip_now=clip_now))

    def on_build(jid, cur, f, mid, clip_now, conf, draft_d, *vals):
        job = Job.load(jid)
        d = dlg.Dialogue.load(job.dir)
        ok, errs, _ = commit_line(job, d, cur, vals[: len(lform)], draft_d)
        if errs:
            return {l_errs: _msgs(errs), shell.status: "⚠️ Сначала исправьте текущую реплику"}
        if mid:
            commit_moment(job, d, mid, vals[len(lform):])
        moves, errs, warns = dlg.build_moves(d, job.names, job.clip_duration())
        if errs:
            return {**load(jid, cur if cur != "new" else None, mid, f, clip_now=clip_now, report_msg=_msgs(errs, warns)),
                    shell.status: f"❌ **Ходы не собраны:** {errs[0]}"}
        replacing = job.moves_file.exists() and job.mode != "manual"
        if replacing and not (conf and conf[0] == "build" and time.time() - conf[2] < CONFIRM_S):
            return {confirm: ("build", None, time.time()),
                    shell.status: "⚠️ У задания уже есть ходы автоматического разбора. Нажмите ещё раз, чтобы заменить их "
                                  "ручными (исходные можно вернуть на странице «Ходы»)"}
        pipeline.save_manual_moves(job, moves)
        job.mode = "manual"
        job.save()
        return {**load(jid, cur if cur != "new" else None, mid, f, clip_now=clip_now,
                       report_msg=_msgs([], warns, f"Собрано ходов: {len(moves)}. Видео ещё не пересобрано.")),
                shell.jobs_nav: shell.nav(jid),
                shell.status: f"✅ Собрано ходов: {len(moves)} — проверьте их в «Ходах» или нажмите «Собрать видео»"}

    def on_render(jid, clip_now):
        def action(run):
            run.job = shell.need_job(jid)
            if not run.job.moves_file.exists():
                raise ValueError("Ходов ещё нет: нажмите «Собрать ходы из разбора».")
            pipeline.step_render(run.job, run.progress)

        yield from shell.live(action, "✅ Видео собрано — оно в предпросмотре на странице «Создание видео»", "Собрать видео",
                              finish=lambda job: load(job.id, clip_now=clip_now))

    # ---------------------------------------------------------------- wiring
    hid = dict(show_progress="hidden")
    seek = f"(s) => {{ window.cdm && cdm.seek('{PLAYER}', s); return []; }}"
    now = f"() => [window.cdm ? cdm.now('{PLAYER}') : '']"
    with_t = f"(jid, t, ...rest) => [jid, window.cdm ? cdm.nowSec('{PLAYER}') : 0, ...rest]"
    st = [shell.status]
    lines_list.input(on_line_pick, [shell.job_id, lines_list, line_sel, flt, moment_sel, clip_state, draft_state, *lform],
                     outputs + st, **hid).then(None, [l_start], None, js=seek)
    flt.input(on_filter, [shell.job_id, flt, line_sel, moment_sel, clip_state], outputs, **hid)
    add_btn.click(on_line_add, [shell.job_id, t_now, line_sel, flt, moment_sel, clip_state, draft_state, *lform],
                  outputs + st, js=with_t, **hid)
    l_save.click(on_line_save, [shell.job_id, line_sel, flt, moment_sel, clip_state, draft_state, *lform], outputs + st, **hid)
    l_split.click(on_split, [shell.job_id, t_now, line_sel, flt, moment_sel, clip_state], outputs + st, js=with_t, **hid)
    l_merge.click(on_merge, [shell.job_id, line_sel, flt, moment_sel, clip_state], outputs + st, **hid)
    l_delete.click(on_line_delete, [shell.job_id, line_sel, flt, moment_sel, clip_state, confirm], outputs + st, **hid)
    take_s.click(None, None, [l_start], js=now)
    take_e.click(None, None, [l_end], js=now)
    play_line.click(None, [l_start, l_end], None, js=f"(a, b) => {{ window.cdm && cdm.playRange('{PLAYER}', a, b); return []; }}")
    moments_list.input(on_moment_pick, [shell.job_id, moments_list, moment_sel, line_sel, flt, clip_state, *mform],
                       outputs + st, **hid)
    mo_save.click(on_moment_save, [shell.job_id, moment_sel, line_sel, flt, clip_state, *mform], outputs + st, **hid)
    mo_delete.click(on_moment_delete, [shell.job_id, moment_sel, line_sel, flt, clip_state, confirm], outputs + st, **hid)
    import_btn.click(on_import, [shell.job_id, flt, clip_state, confirm], shell.dock + outputs, **hid)
    build_btn.click(on_build, [shell.job_id, line_sel, flt, moment_sel, clip_state, confirm, draft_state, *lform, *mform],
                    outputs + st + [shell.jobs_nav], **hid)
    render_btn.click(on_render, [shell.job_id, clip_state], shell.outs + outputs, **hid)

    return SimpleNamespace(load=lambda jid: load(jid, clip_now=""), outputs=outputs, to_moves=to_moves,
                           goto_create=goto_create)
