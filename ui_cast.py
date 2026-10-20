"""The characters dialog: the faces found in the clip, to be named, assigned to a side,
removed (posters, extras, wrong detections) or merged (one character seen as two).

It opens after the face analysis of a new video (and any time from «Персонажи в кадре»).
Names are suggested from the characters confirmed in earlier videos (cast.Registry);
confirming remembers them for the whole project and makes the speaker analysis, the
reframing, the avatars and the player names follow the confirmed identities.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import cast
import design
import pipeline
import ui_parts as up
import vision
from pipeline import Job

log = logging.getLogger(__name__)
SLOTS = vision.MAX_PERSONS
SIDE_CHOICES = [("Белые", "white"), ("Чёрные", "black"), ("Не играет", "")]


def build(gr) -> SimpleNamespace:
    """Components only (they must exist before the pages' output lists); `wire` adds the logic."""
    job_st = gr.State(None)
    mode_st = gr.State("edit")      # "flow": part of creating a video (continue after it) | "edit"
    with gr.Column(visible=False, elem_id="cast-modal") as modal:
        with gr.Column(elem_id="cast-card"):
            head = gr.Markdown("### Кто в кадре", elem_id="cast-head")
            gr.Markdown("Назовите персонажей и выберите, кто играет белыми и кто чёрными. Лишние лица уберите, а если один "
                        "человек найден дважды — объедините. Имена запомнятся для следующих видео.", elem_classes="hint")
            rows = []
            for i in range(SLOTS):
                with gr.Row(visible=False, elem_classes="cast-row", equal_height=False) as row:
                    img = gr.Image(show_label=False, interactive=False, type="filepath", height=112, width=112,
                                   buttons=[], elem_classes="cast-face", min_width=112, scale=0)
                    with gr.Column(min_width=0, elem_classes="cast-fields"):
                        info = gr.Markdown("", elem_classes="hint cast-info")
                        with gr.Row(elem_classes="cdm-pair"):
                            name = gr.Dropdown([], label="Имя", allow_custom_value=True, filterable=True,
                                               elem_classes="cast-name")
                            side = gr.Radio(SIDE_CHOICES, value="", label="Играет за", elem_classes="cdm-seg")
                        with gr.Row(elem_classes="cdm-pair"):
                            removed = gr.Checkbox(label="Не персонаж — убрать", elem_classes="cast-removed")
                            merge = gr.Dropdown([("—", "")], value="", label="Это тот же, что…", elem_classes="cast-merge")
                rows.append(SimpleNamespace(row=row, img=img, info=info, name=name, side=side, removed=removed, merge=merge))
            errs = gr.Markdown("", elem_id="cast-errors")
            with gr.Row(elem_classes="cdm-actions"):
                confirm = gr.Button("Подтвердить", icon=design.icon("check", "dark"), variant="primary", elem_id="cast-confirm",
                                    scale=0)
                swap = gr.Button("Поменять стороны", icon=design.icon("arrow-down"), variant="secondary", scale=0)
                skip = gr.Button("Пропустить", variant="secondary", elem_id="cast-skip", scale=0)
                cancel = gr.Button("Отмена", variant="secondary", elem_id="cast-cancel", scale=0)
    fields = [c for r in rows for c in (r.name, r.side, r.removed, r.merge)]
    outputs = [modal, head, errs, job_st, mode_st] + [c for r in rows for c in (r.row, r.img, r.info, r.name, r.side,
                                                                                 r.removed, r.merge)]
    ns = SimpleNamespace(modal=modal, head=head, errs=errs, job_st=job_st, mode_st=mode_st, rows=rows, fields=fields,
                         confirm=confirm, swap=swap, skip=skip, cancel=cancel, outputs=outputs)

    def open_(jid, mode: str = "edit") -> dict:
        """Show the dialog for a job (a fresh proposal if the faces were never analysed)."""
        job = Job.load(jid)
        jc = cast.JobCast.load(job.dir) or pipeline.step_faces(job)
        if jc is None or not jc.persons:
            return {modal: gr.update(visible=False)}
        registry = cast.Registry.load()
        names = registry.names()
        res = {modal: gr.update(visible=True), errs: "", job_st: jid, mode_st: mode,
               head: f"### Кто в кадре · {len(jc.persons)} лиц" + (" · подтверждено" if jc.confirmed else "")}
        for i, r in enumerate(rows):
            p = jc.persons[i] if i < len(jc.persons) else None
            if p is None:
                res[r.row] = gr.update(visible=False)
                continue
            crop = vision.person_crop(job.dir, p.person)
            bits = [f"Лицо №{p.person + 1}", f"в кадре {p.screen:.0f} с"]
            if p.suggestion:
                bits.append(f"похож на «{up.esc(p.suggestion)}» ({p.similarity:.0%})")
            if p.guessed and not jc.confirmed:
                bits.append("предложено автоматически — проверьте")
            others = [(f"Лицо №{q.person + 1}" + (f" · {q.name}" if q.name else ""), str(q.person))
                      for q in jc.persons if q.person != p.person]
            res.update({
                r.row: gr.update(visible=True), r.img: str(crop) if crop else None, r.info: " · ".join(bits),
                r.name: gr.update(choices=sorted(set(names + ([p.name] if p.name else []))), value=p.name or None),
                r.side: p.side, r.removed: p.removed,
                r.merge: gr.update(choices=[("—", "")] + others, value=str(p.merged_into) if p.merged_into is not None else ""),
            })
        return res

    ns.open = open_
    return ns


def read(job: Job, vals) -> cast.JobCast:
    jc = cast.JobCast.load(job.dir) or cast.JobCast()
    for i, p in enumerate(jc.persons[:SLOTS]):
        name, side, removed, merge = vals[i * 4 : i * 4 + 4]
        p.name = " ".join((name or "").split())
        p.side = side or ""
        p.removed = bool(removed)
        p.merged_into = int(merge) if merge not in (None, "") else None
        if p.removed or p.merged_into is not None:
            p.side = ""
    return jc


def wire(ns, shell, flow, flow_outs: list) -> None:
    """`flow(jid, stage)` continues creating the video after the dialog (a generator of updates)."""
    gr = shell.gr

    def on_confirm(jid, mode, *vals):
        job = Job.load(jid)
        jc = read(job, vals)
        try:
            errs_l = pipeline.confirm_cast(job, jc)
        except Exception as e:
            log.debug("cast confirm", exc_info=True)
            errs_l = [str(e)]
        if errs_l:
            yield {ns.errs: "".join(f"<div class='cdm-msg cdm-msg-error'>✗ {up.esc(e)}</div>" for e in errs_l),
                   shell.status: f"⚠️ {errs_l[0]}"}
            return
        yield {ns.modal: gr.update(visible=False)}
        if mode == "flow":
            yield from flow(jid, "summary")
            return
        job = Job.load(jid)
        again = " Видео покажет новые имена и лица после пересборки — «Применить и перерендерить»." if job.final.exists() else ""
        yield {**shell.view(job, f"✅ Персонажи подтверждены: {cast.context_text(jc).split(': ', 1)[-1]}{again}"),
               shell.jobs_nav: shell.nav(jid)}

    def on_skip(jid, mode):
        if jid:
            pipeline.skip_cast(Job.load(jid))
        yield {ns.modal: gr.update(visible=False)}
        if jid and mode == "flow":
            yield from flow(jid, "summary")

    def on_swap(*vals):
        out = []
        for i in range(SLOTS):
            side = vals[i]
            out.append({"white": "black", "black": "white"}.get(side, side))
        return out

    def on_cancel(mode):
        msg = ("Персонажи не подтверждены — откройте их кнопкой «Персонажи в кадре» или нажмите «Продолжить выбранное», "
               "чтобы создать видео с автоматическими догадками" if mode == "flow" else "")
        return {ns.modal: gr.update(visible=False), shell.status: msg}

    hid = dict(show_progress="hidden")
    outs = list(dict.fromkeys([ns.modal, ns.errs, shell.status, shell.jobs_nav, *flow_outs]))
    ns.cancel.click(on_cancel, [ns.mode_st], [ns.modal, shell.status], **hid)
    ns.confirm.click(on_confirm, [ns.job_st, ns.mode_st, *ns.fields], outs, **hid)
    ns.skip.click(on_skip, [ns.job_st, ns.mode_st], outs, **hid)
    ns.swap.click(on_swap, [r.side for r in ns.rows], [r.side for r in ns.rows], **hid)
