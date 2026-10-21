"""The «ответ не прошёл проверку» window (recovery.py): every answer of the model is kept;
the person sees which steps (moves) failed the checks and why, picks a version for each
step from the different answers, may correct it, and continues — without repeating the
work done before the analysis. Closing keeps the choices; the next «Разобрать диалог» /
«Продолжить выбранное» opens the window again without asking the model.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import config
import design
import moves_edit
import pipeline
import recovery
import ui_parts as up
from pipeline import Job

CONFIRM_S = 6.0
TYPES = [(f"{st.glyph} {st.label_ru} · {key}", key) for key, st in config.MOVE_TYPES.items()]


def _issues_html(items: list[str], ok: str = "") -> str:
    if not items:
        return f"<div class='cdm-msg cdm-msg-ok'>✓ {up.esc(ok)}</div>" if ok else ""
    return "".join(f"<div class='cdm-msg cdm-msg-warn'>⚠ {up.esc(x)}</div>" for x in items)


def _step_label(st: dict, left: dict) -> str:
    k = st["index"]
    probs = left["moves"].get(k, [])
    mark = f"⚠ {len(probs)}" if probs else "✓"
    _, mv = recovery.current(st)
    return f"Ход {k + 1} · {moves_edit.fmt_ts(st['start'])}–{moves_edit.fmt_ts(st['end'])} · {mark} — {mv['move_type']}: «{mv['title']}»"


def _alt_label(a: dict) -> str:
    m = a["move"]
    mark = f" · ⚠ {len(a['issues'])}" if a["issues"] else " · ✓"
    return f"{a['label']}: {m['move_type']} — «{m['title']}»{mark}"


def build(gr) -> SimpleNamespace:
    ctx = gr.State(None)       # {"job", "op", "until"}: what to continue with after the choice
    confirm = gr.State(None)   # time of the first click on «Разобрать заново» (the answers are discarded)
    cur = gr.State(None)       # the step shown in the form (its edits are saved on the next switch)
    with gr.Column(visible=False, elem_id="rec-modal") as modal:
        with gr.Column(elem_id="rec-card"):
            gr.Markdown("### Ответ нейросети не прошёл проверку", elem_id="rec-title")
            intro = gr.HTML("", elem_id="rec-intro", container=False, padding=False)
            base = gr.Radio([], label="Основа разбора — как диалог разбит на ходы", elem_id="rec-base",
                            elem_classes="cdm-chips")
            with gr.Row(elem_id="rec-grid", equal_height=False):
                steps = gr.Radio([], label="Ходы", elem_id="rec-steps", min_width=0)
                with gr.Column(elem_id="rec-form", min_width=0):
                    lines = gr.HTML("", elem_id="rec-lines", container=False, padding=False)
                    alts = gr.Radio([], label="Варианты этого хода от нейросети", elem_id="rec-alts")
                    with gr.Row(elem_classes="cdm-pair"):
                        mtype = gr.Dropdown(TYPES, label="Тип хода", elem_id="rec-type")
                        side = gr.Radio([], label="Чей ход", elem_id="rec-side")
                    gist = gr.Textbox(label="Суть хода — 2–5 слов для подписи «Имя · суть»", max_lines=1, elem_id="rec-gist")
                    title = gr.Textbox(label="Заголовок", max_lines=2, elem_id="rec-title-box")
                    expl = gr.Textbox(label="Объяснение", lines=3, max_lines=8, elem_id="rec-expl")
                    form_msg = gr.HTML("", elem_id="rec-form-msg", container=False, padding=False)
            gr.Markdown("Выбор сохраняется сразу: окно можно закрыть и вернуться к нему кнопкой «Разобрать диалог» "
                        "или «Продолжить выбранное» — нейросеть второй раз не спрашивается.", elem_classes="hint")
            with gr.Row(elem_classes="cdm-actions", elem_id="rec-actions"):
                apply = gr.Button("Применить и продолжить", icon=design.icon("check", "dark"), variant="primary",
                                  elem_id="rec-apply", scale=0)
                more = gr.Button("Запросить ещё вариант", icon=design.icon("sparkles"), variant="secondary",
                                 elem_id="rec-more", scale=0)
                restart = gr.Button("Разобрать заново", icon=design.icon("rotate"), variant="secondary",
                                    elem_id="rec-restart", scale=0)
                close = gr.Button("Закрыть", variant="secondary", elem_id="rec-close", scale=0)
    form = [alts, mtype, side, gist, title, expl]
    outputs = [modal, intro, base, steps, lines, *form, form_msg, ctx, cur]

    def view(jid, k: int | None = None, msg: str = "") -> dict:
        """The window for a job's pending answers, showing step `k` (default: the first one with problems)."""
        job = Job.load(jid)
        rec = recovery.pending(job.dir)
        if rec is None:
            return {modal: gr.update(visible=False)}
        segs, hints, names = pipeline.recovery_inputs(job)
        sts = recovery.steps(rec, segs, hints, names)
        left = recovery.remaining(rec, segs, hints, names)
        if k is None or not 0 <= k < len(sts):
            k = next((s["index"] for s in sts if left["moves"].get(s["index"])), 0)
        st = sts[k]
        alt_id, mv = recovery.current(st)
        n_left = len(left["general"]) + sum(len(v) for v in left["moves"].values())
        models = sorted({a.get("model") for a in rec.attempts if a.get("model")})
        bad = [a for a in rec.attempts if not a.get("moves")]
        html = (f"<p>Нейросеть ({up.esc(', '.join(models) or job.llm_label)}) дала вариантов: {len(rec.attempts)}"
                + (f", из них не прочитались: {len(bad)}" if bad else "")
                + (". Ни один не прошёл проверку целиком" if not recovery.clean(rec) else "")
                + " — выберите лучший вариант для каждого хода и при необходимости поправьте его. Распознавание речи, "
                  "лица, звуки и сводка уже готовы и не повторяются.</p>")
        good = [i for i in recovery.clean(rec) if i != rec.base]
        if good:
            html += (f"<div class='cdm-msg cdm-msg-ok'>✓ {up.esc(rec.attempt_label(good[-1]))} прошёл проверку целиком — "
                     "его можно выбрать основой выше.</div>")
        html += _issues_html(left["general"])
        html += ("<div class='cdm-msg cdm-msg-ok'>✓ С текущим выбором замечаний нет — можно продолжать.</div>" if not n_left
                 else f"<p class='rec-left'>С текущим выбором осталось замечаний: <b>{n_left}</b>. Продолжить можно и с ними.</p>")
        base_choices = [(f"{rec.attempt_label(i)} · ходов {len(rec.attempts[i]['moves'])} · "
                         f"замечаний {len(rec.attempts[i].get('issues') or [])}", str(i)) for i in rec.valid()]
        line_html = (f"<div class='rec-lines'><div class='rec-when'>Реплики {moves_edit.fmt_ts(st['start'])}–"
                     f"{moves_edit.fmt_ts(st['end'])}</div><blockquote>{up.esc(st['lines'])}</blockquote></div>"
                     + _issues_html(left["moves"].get(k, []), "У выбранного варианта этого хода замечаний нет."))
        return {
            modal: gr.update(visible=True), intro: html, cur: k,
            base: gr.update(choices=base_choices, value=str(rec.base), visible=len(base_choices) > 1),
            steps: gr.update(choices=[(_step_label(s, left), str(s["index"])) for s in sts], value=str(k)),
            lines: line_html,
            alts: gr.update(choices=[(_alt_label(a), a["id"]) for a in st["alts"]], value=alt_id),
            mtype: mv["move_type"],
            side: gr.update(choices=[(names["white"] + " · белые", "white"), (names["black"] + " · чёрные", "black")],
                            value=mv["speaker"]),
            gist: mv.get("gist", ""), title: mv["title"], expl: mv["explanation"], form_msg: msg,
        }

    def open_(jid, op: dict | None = None) -> dict:
        """The window for a job with answers waiting for a choice ({} when there are none)."""
        if not jid or recovery.pending(Job.load(jid).dir) is None:
            return {}
        return {**view(jid), ctx: {**(op or {}), "job": jid}}

    def save_form(jid, k, alt_id, mt, sd, gs, tt, ex) -> list[str]:
        """The form into the step's choice (the version + what the person changed)."""
        if k is None or not jid:
            return []
        job = Job.load(jid)
        rec = recovery.pending(job.dir)
        if rec is None:
            return []
        segs, hints, names = pipeline.recovery_inputs(job)
        sts = recovery.steps(rec, segs, hints, names)
        if not 0 <= int(k) < len(sts):
            return []
        return recovery.set_choice(job.dir, rec, sts[int(k)], alt_id,
                                   {"move_type": mt, "speaker": sd, "gist": gs or "", "title": tt or "", "explanation": ex or ""})

    def errors_html(errs: list[str]) -> str:
        return "".join(f"<div class='cdm-msg cdm-msg-error'>✗ {up.esc(e)}</div>" for e in errs)

    return SimpleNamespace(restart=restart, confirm=confirm, modal=modal, intro=intro, base=base, steps=steps, lines=lines, alts=alts, mtype=mtype, side=side,
                           gist=gist, title=title, expl=expl, form_msg=form_msg, ctx=ctx, cur=cur, apply=apply, more=more, close=close,
                           form=form, outputs=outputs, view=view, open=open_, save_form=save_form, errors_html=errors_html)


def wire(ns, shell, cont, restart) -> None:
    """Handlers of the window. `cont(jid, ctx)` is the generator that goes on after the choice
    (review.py: the render, or stop before it in the guided mode); `restart(jid, ctx)` drops the
    answers and analyses again."""
    gr = shell.gr
    hid = dict(show_progress="hidden")
    base_out = list(dict.fromkeys(ns.outputs + [shell.status]))

    def jid_of(c):
        return (c or {}).get("job")

    def on_step(c, k_new, k_old, *form):
        jid = jid_of(c)
        errs = ns.save_form(jid, k_old, *form)
        if errs:  # an invalid edit keeps the step on screen
            return {ns.steps: str(k_old), ns.form_msg: ns.errors_html(errs), shell.status: f"⚠️ {errs[0]}"}
        return ns.view(jid, int(k_new))

    def on_alt(c, k, alt_id, mt, sd, gs, tt, ex):
        """Another version of the step: chosen at once. The form's edits stay with the version they were
        made on (and come back when it is picked again)."""
        jid = jid_of(c)
        job = Job.load(jid)
        rec = recovery.pending(job.dir)
        if rec is None or k is None:
            return {}
        segs, hints, names = pipeline.recovery_inputs(job)
        st = recovery.steps(rec, segs, hints, names)[int(k)]
        prev, _ = recovery.current(st)
        if prev != alt_id:  # an invalid edit of the previous version is simply not kept
            recovery.set_choice(job.dir, rec, st, prev, {"move_type": mt, "speaker": sd, "gist": gs or "", "title": tt or "",
                                                         "explanation": ex or ""})
        st = recovery.steps(rec, segs, hints, names)[int(k)]
        recovery.pick(job.dir, rec, st, alt_id)
        return ns.view(jid, int(k))

    def on_close(c, k, *form):
        """Closing keeps the choices — also the edit of the step on screen."""
        errs = ns.save_form(jid_of(c), k, *form)
        if errs:
            return {ns.form_msg: ns.errors_html(errs), shell.status: f"⚠️ {errs[0]} — исправьте поле или выберите вариант"}
        return {ns.modal: gr.update(visible=False),
                shell.status: "🧩 Выбор сохранён. Вернуться к вариантам — «Разобрать диалог» или «Продолжить выбранное»."}

    def on_restart(c, k, conf, *form):
        jid = jid_of(c)
        if not (conf and time.time() - conf < CONFIRM_S):
            yield {ns.confirm: time.time(),
                   ns.form_msg: "<div class='cdm-msg cdm-msg-warn'>⚠ Все ответы и ваш выбор будут удалены, нейросеть "
                                f"разберёт диалог заново. Нажмите «Разобрать заново» ещё раз в течение {CONFIRM_S:g} с.</div>"}
            return
        yield {ns.modal: gr.update(visible=False), ns.confirm: None}
        yield from restart(jid, c or {})

    def on_base(c, i):
        jid = jid_of(c)
        job = Job.load(jid)
        rec = recovery.pending(job.dir)
        if rec is None or i is None or int(i) == rec.base:
            return {}
        recovery.set_base(job.dir, rec, int(i))
        return {**ns.view(jid, None, "<div class='cdm-msg cdm-msg-warn'>⚠ Основа сменилась — выбор по ходам начат заново.</div>"),
                shell.status: f"🧩 Основа разбора: {rec.attempt_label(int(i))}"}

    def on_apply(c, k, *form):
        jid = jid_of(c)
        if not jid:
            yield {ns.modal: gr.update(visible=False)}
            return
        errs = ns.save_form(jid, k, *form)
        if errs:
            yield {ns.form_msg: ns.errors_html(errs), shell.status: f"⚠️ {errs[0]}"}
            return
        pipeline.resolve_recovery(Job.load(jid))
        yield {ns.modal: gr.update(visible=False), shell.status: "✅ Варианты приняты — ходы собраны"}
        yield from cont(jid, c or {})

    def on_more(c, k, *form):
        jid = jid_of(c)
        errs = ns.save_form(jid, k, *form)
        if errs:
            yield {ns.form_msg: ns.errors_html(errs), shell.status: f"⚠️ {errs[0]}"}
            return

        def action(run):
            run.job = shell.need_job(jid)
            run.progress(0.2, f"Ещё один вариант ({run.job.llm_label})")
            pipeline.recovery_more(run.job)

        run = yield from shell.live(action, "🧩 Новый вариант добавлен — выберите лучший для каждого хода", "Запросить ещё вариант",
                                    finish=lambda job: {})
        if run.error is None:
            rec = recovery.pending(Job.load(jid).dir)
            readable = bool(rec and rec.attempts[-1].get("moves"))
            yield {**ns.view(jid, int(k) if k is not None else None),
                   shell.status: "🧩 Новый вариант добавлен в окно" if readable else
                   "⚠️ Новый ответ нейросети не удалось прочитать — выберите из имеющихся вариантов или поправьте вручную"}
        else:
            yield {**ns.view(jid, int(k) if k is not None else None,
                             ns.errors_html(["Ещё один вариант получить не удалось — выберите из имеющихся или поправьте "
                                             "вручную."]))}

    ns.steps.input(on_step, [ns.ctx, ns.steps, ns.cur, *ns.form], base_out, **hid)
    ns.alts.input(on_alt, [ns.ctx, ns.cur, ns.alts, *ns.form[1:]], base_out, **hid)
    ns.close.click(on_close, [ns.ctx, ns.cur, *ns.form], base_out, **hid)
    ns.restart.click(on_restart, [ns.ctx, ns.cur, ns.confirm, *ns.form],
                     list(dict.fromkeys(base_out + [ns.confirm] + shell.cont_outs)), **hid)
    ns.base.input(on_base, [ns.ctx, ns.base], base_out, **hid)
    ns.apply.click(on_apply, [ns.ctx, ns.cur, *ns.form], list(dict.fromkeys(base_out + shell.cont_outs)), **hid)
    ns.more.click(on_more, [ns.ctx, ns.cur, *ns.form], list(dict.fromkeys(base_out + shell.dock)), **hid)
