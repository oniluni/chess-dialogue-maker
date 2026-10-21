"""«Сводка сцены» section of the create page: the structured summary of the clip
(summary.py) to accept, correct section by section or write by hand.

Reliable items and guesses look different (a badge and a dashed outline). In the edit
fields a guess is a line starting with «?»; removing the «?» makes it a fact. A section
the person changed is kept when the summary is generated again. «Принять» saves the edits
and marks the summary as checked; during the creation of a video it also continues to the
analysis.
"""
from __future__ import annotations

import logging
import time
from types import SimpleNamespace

import design
import pipeline
import summary as sm
import ui_parts as up
from pipeline import Job

log = logging.getLogger(__name__)
CONFIRM_S = 6.0


def view_html(s: sm.Summary | None) -> str:
    if s is None:
        return up.empty_state("Сводки ещё нет", "Она появится после распознавания речи — или напишите её сами.", "sparkles")
    if s.empty():
        return up.empty_state("Сводка пустая", "Заполните разделы ниже — их получит нейросеть при разборе ходов.", "edit")
    cards = []
    for sec, title in sm.SECTIONS.items():
        items = s.items(sec)
        if not items:
            continue
        rows = []
        for it in items:
            badge = ("<span class='cdm-sum-badge is-sure'>надёжно</span>" if it.certain
                     else "<span class='cdm-sum-badge is-guess'>предположение</span>")
            lead = (f"<b>{up.esc(it.name)}</b> — " if it.name else "") + \
                   (f"<span class='cdm-sum-time'>{sm._fmt_time(it.time)}</span> " if it.time is not None else "")
            rows.append(f"<li class='{'is-sure' if it.certain else 'is-guess'}'>{lead}{up.esc(it.text)} {badge}</li>")
        edited = " <span class='cdm-sum-edited'>✎ ваша правка</span>" if s.edited.get(sec) else ""
        cards.append(f"<div class='cdm-sum-sec'><div class='cdm-sum-title'>{title}{edited}</div><ul>{''.join(rows)}</ul></div>")
    return f"<div class='cdm-sum'>{''.join(cards)}</div>"


def status_md(s: sm.Summary | None) -> str:
    if s is None:
        return ""
    sure, guess = s.counts()
    state = "✓ проверена вами" if s.status == "accepted" else "черновик — проверьте и примите"
    src = "написана вручную" if s.source == "manual" else f"нейросеть: {s.model}" if s.model else ""
    return f"**{state}** · {src} · надёжных {sure}, предположений {guess}".replace(" ·  ·", " ·")


def build(gr) -> SimpleNamespace:
    gr.Markdown("## Сводка сцены\nКонтекст, персонажи, суть диалога, действия и ключевые моменты — их учтёт разбор ходов. "
                "Предположения нейросети помечены отдельно.", elem_id="sec-summary", elem_classes="sec")
    state_md = gr.Markdown("", elem_classes="hint", elem_id="summary-state")
    view = gr.HTML(view_html(None), elem_id="summary-view", container=False, padding=False)
    with gr.Accordion("Править сводку", open=False, elem_id="summary-edit") as edit_acc:
        gr.Markdown("Одна строка — один пункт. «? » в начале строки — предположение; уберите «?», если уверены. "
                    "Персонажи: «Имя — кто это», ключевые моменты: «0:12 — что происходит».", elem_classes="hint")
        boxes = {sec: gr.Textbox(label=title, lines=2 if sec in ("setting", "dialogue") else 3, max_lines=8,
                                 elem_id=f"sum-{sec}") for sec, title in sm.SECTIONS.items()}
    errs = gr.Markdown("", elem_id="summary-errors")
    with gr.Row(elem_classes="cdm-actions", elem_id="summary-actions"):
        accept = gr.Button("Принять сводку", icon=design.icon("check", "dark"), variant="primary", elem_id="sum-accept",
                           scale=0)
        regen = gr.Button("Сгенерировать заново", icon=design.icon("sparkles"), variant="secondary", elem_id="sum-regen",
                          scale=0)
        manual = gr.Button("Написать вручную", icon=design.icon("edit"), variant="secondary", elem_id="sum-manual", scale=0)
    confirm = gr.State(None)
    outputs = [state_md, view, *boxes.values(), errs, accept, edit_acc, confirm]
    ns = SimpleNamespace(state_md=state_md, view=view, boxes=boxes, errs=errs, accept=accept, regen=regen,
                         manual=manual, edit_acc=edit_acc, confirm=confirm, outputs=outputs)

    def load(jid, msg: str = "", open_edit: bool | None = None) -> dict:
        s = sm.Summary.load(Job.load(jid).dir) if jid else None
        res = {state_md: status_md(s), view: view_html(s), errs: msg, confirm: None,
               accept: gr.update(value="Принять и продолжить разбор" if jid and _in_flow(jid) else "Принять сводку",
                                 interactive=s is not None)}
        for sec, box in boxes.items():
            res[box] = sm.to_text(s, sec) if s else ""
        if open_edit is not None:
            res[edit_acc] = gr.update(open=open_edit)
        return res

    ns.load = load
    return ns


def _in_flow(jid) -> bool:
    """The video is being created: no analysis yet, so accepting continues to it."""
    job = Job.load(jid)
    return job.mode != "manual" and not job.moves_file.exists()


def wire(ns, shell, flow, flow_outs: list, ai) -> None:

    def apply_edits(job: Job, texts) -> tuple[sm.Summary, list[str]]:
        s = sm.Summary.load(job.dir) or sm.manual()
        errs = []
        for sec, text in zip(sm.SECTIONS, texts):
            errs += sm.edit(s, sec, text)
        return s, errs

    def msgs(errs: list[str]) -> str:
        return "".join(f"<div class='cdm-msg cdm-msg-error'>✗ {up.esc(e)}</div>" for e in errs)

    def on_accept(jid, *texts):
        if not jid:
            yield {shell.status: "⚠️ Сначала создайте видео или выберите задание"}
            return
        job = Job.load(jid)
        s, errs = apply_edits(job, texts)
        if errs:
            yield {ns.errs: msgs(errs), shell.status: f"⚠️ {errs[0]}"}
            return
        s.status = "accepted"
        s.save(job.dir)
        yield {**ns.load(jid, open_edit=False), shell.status: "✅ Сводка принята — ваши правки сохранены и не перезапишутся"}
        if _in_flow(jid):
            yield from flow(jid, "analysis")

    def on_regen(jid, *texts):
        if not jid:
            yield {shell.status: "⚠️ Сначала создайте видео или выберите задание"}
            return
        job = Job.load(jid)
        s, errs = apply_edits(job, texts)
        if errs:
            yield {ns.errs: msgs(errs), shell.status: f"⚠️ {errs[0]}"}
            return
        s.save(job.dir)  # the person's sections survive the new generation

        def action(run):
            run.job = shell.need_job(jid)
            pipeline.step_summary(run.job)

        yield from shell.live(action, "✅ Сводка готова — проверьте предположения и примите её", "Сгенерировать заново",
                              finish=lambda j: ns.load(j.id), ai_op={"op": "summary", "job": jid})

    def on_manual(jid, conf):
        if not jid:
            return {shell.status: "⚠️ Сначала создайте видео или выберите задание"}
        job = Job.load(jid)
        s = sm.Summary.load(job.dir)
        if s is not None and not s.empty() and not (conf and time.time() - conf < CONFIRM_S):
            return {ns.confirm: time.time(),
                    shell.status: "⚠️ Нажмите ещё раз, чтобы очистить сводку и написать её вручную (старая будет заменена)"}
        sm.manual().save(job.dir)
        return {**ns.load(jid, open_edit=True), shell.status: "✎ Сводка очищена — заполните разделы и нажмите «Принять»"}

    hid = dict(show_progress="hidden")
    texts = list(ns.boxes.values())
    base = list(dict.fromkeys([*ns.outputs, shell.status]))
    ns.accept.click(on_accept, [shell.job_id, *texts], list(dict.fromkeys(base + flow_outs)), **hid)
    ns.regen.click(on_regen, [shell.job_id, *texts], list(dict.fromkeys(base + shell.dock + ai.outputs)), **hid)
    ns.manual.click(on_manual, [shell.job_id, ns.confirm], base, **hid)
