"""«Ходы» page: manual management of a job's moves — the clip with a timeline, the
list of moves and a form with validation and a live preview of the card (rendered by
the same code as the video). Works for automatic analyses and hand-made moves alike.

Rules that keep work safe: switching to another move saves the current one if it is
valid (an invalid edit blocks the switch instead of being lost), deleting and
restoring need a second click, and the first edit keeps the original analysis
(moves.orig.json) so it can be restored.
"""
from __future__ import annotations

import logging
import time
from types import SimpleNamespace

import design
import moves_edit as me
import pipeline
import renderer
import ui_parts as up
from pipeline import Job

log = logging.getLogger(__name__)
PLAYER = "#moves-player"
LIST = "#moves-list"
CONFIRM_S = 6.0


def _speakers(job: Job) -> list[tuple[str, str]]:
    return [(f"Белые · {job.names['white']}", "white"), (f"Чёрные · {job.names['black']}", "black")]


def _untouched(form: me.MoveForm) -> bool:
    return not (form.title or "").strip() and not (form.explanation or "").strip() and not (form.gist or "").strip()


def _same(a: me.MoveForm, b: me.MoveForm) -> bool:
    norm = lambda f: (f.start.strip(), f.end.strip(), f.speaker, f.move_type, round(float(f.eval_score or 0), 1),  # noqa: E731
                      " ".join(f.title.split()), " ".join(f.gist.split()), " ".join(f.explanation.split()))
    return norm(a) == norm(b)


def _find(moves, move) -> int:
    return min(range(len(moves)), key=lambda i: abs(moves[i].start - move.start) + (0 if moves[i].title == move.title else 5))


def _errors_md(errs: list[str], warns: list[str] | None = None) -> str:
    out = [f"<div class='cdm-msg cdm-msg-error'>✗ {up.esc(e)}</div>" for e in errs]
    out += [f"<div class='cdm-msg cdm-msg-warn'>⚠ {up.esc(w)}</div>" for w in (warns or [])]
    return "".join(out)


def build(shell) -> SimpleNamespace:
    gr = shell.gr
    sel = gr.State(None)          # index of the edited move | "new" | None
    clip_state = gr.State("")     # clip shown in the player: reloading it would reset the playhead
    confirm = gr.State(None)      # (action, index, time) of a pending destructive click
    t_now = gr.Number(value=0, visible="hidden", elem_id="mv-t")

    title = gr.Markdown("# Ходы", elem_id="moves-title", elem_classes="page-title")
    empty = gr.HTML(up.empty_state("Нет задания", "Выберите задание слева или создайте новое на странице «Создание видео»."),
                    container=False, padding=False)
    with gr.Column(visible=False, elem_id="moves-work") as work:
        with gr.Row(elem_id="moves-toolbar", elem_classes="cdm-toolbar"):
            add_btn = gr.Button("Добавить ход в точке плеера", icon=design.icon("plus", "text"), variant="secondary",
                                elem_id="mv-add", scale=0)
            restore_btn = gr.Button("Вернуть исходные ходы", icon=design.icon("rotate"), variant="secondary",
                                    elem_id="mv-restore", scale=0)
            info = gr.Markdown("", elem_classes="hint", elem_id="moves-info")
            render_btn = gr.Button("Собрать видео", icon=design.icon("film", "dark"), variant="primary",
                                   elem_id="mv-render", scale=0)
        timeline = gr.HTML("", elem_id="moves-timeline", container=False, padding=False)
        gr.HTML(up.transport(PLAYER), container=False, padding=False)
        with gr.Row(elem_id="moves-grid", equal_height=False):
            with gr.Column(elem_id="moves-left", min_width=0):
                player = gr.Video(show_label=False, interactive=False, elem_id="moves-player", buttons=[],
                                  height="clamp(280px, calc(100vh - 420px), 560px)")
                gr.HTML('<div class="outline-title">Ходы по порядку</div>', container=False, padding=False)
                listing = gr.Radio([], show_label=False, container=False, elem_id="moves-list")
            with gr.Column(elem_id="moves-form", elem_classes="cdm-form", min_width=0):
                head = gr.Markdown("", elem_id="moves-head")
                with gr.Row(elem_id="mv-time-row", elem_classes="cdm-time-row"):
                    m_start = gr.Textbox(label="Начало", elem_id="m-start", min_width=90, max_lines=1)
                    take_s = gr.Button("", icon=design.icon("clock"), variant="secondary", elem_classes="cdm-icon-btn",
                                       scale=0, min_width=40)
                    m_end = gr.Textbox(label="Конец", elem_id="m-end", min_width=90, max_lines=1)
                    take_e = gr.Button("", icon=design.icon("clock"), variant="secondary", elem_classes="cdm-icon-btn",
                                       scale=0, min_width=40)
                    play_btn = gr.Button("Проиграть", icon=design.icon("play"), variant="secondary", scale=0, min_width=120)
                m_speaker = gr.Radio([], label="Ход делает", elem_id="m-speaker", elem_classes="cdm-seg")
                with gr.Row(elem_classes="cdm-pair"):
                    m_type = gr.Dropdown(me.TYPE_CHOICES, value="Best", label="Тип хода", elem_id="m-type", min_width=160)
                    m_eval = gr.Slider(-10, 10, value=0, step=0.1, label="Оценка после хода: + белые, − чёрные", buttons=[],
                                       elem_id="m-eval", min_width=200)
                m_title = gr.Textbox(label="Заголовок карточки", max_lines=2, elem_id="m-title",
                                     placeholder="Крючок с вердиктом: «… — и это мат»")
                m_gist = gr.Textbox(label="Подзаголовок — суть хода (необязательно)", max_lines=1, elem_id="m-gist",
                                    placeholder="ложная забота, вилка из двух вопросов…")
                m_expl = gr.Textbox(label="Объяснение", lines=3, max_lines=6, elem_id="m-expl",
                                    placeholder="Что происходит под словами и почему это работает или проваливается")
                errs = gr.Markdown("", elem_id="moves-errors")
                with gr.Row(elem_id="mv-actions", elem_classes="cdm-actions"):
                    save_btn = gr.Button("Сохранить ход", icon=design.icon("check", "dark"), variant="primary",
                                         elem_id="mv-save", scale=0)
                    up_btn = gr.Button("Раньше", icon=design.icon("arrow-up"), variant="secondary", scale=0)
                    down_btn = gr.Button("Позже", icon=design.icon("arrow-down"), variant="secondary", scale=0)
                    del_btn = gr.Button("Удалить", icon=design.icon("trash"), variant="secondary", scale=0,
                                        elem_classes="cdm-danger")
                gr.HTML('<div class="outline-title">Карточка в видео</div>', container=False, padding=False)
                preview = gr.Image(show_label=False, interactive=False, type="pil", elem_id="moves-preview", buttons=[])

    form = [m_start, m_end, m_speaker, m_type, m_eval, m_title, m_gist, m_expl]
    outputs = [title, empty, work, timeline, player, clip_state, listing, sel, head, *form, errs, preview, info,
               restore_btn, confirm]

    # ---------------------------------------------------------------- state -> components
    def _preview(job: Job, moves, idx, form_v: me.MoveForm | None, dur):
        try:
            items, at = list(moves), idx
            if form_v is not None:
                mv, e, _ = me.validate(form_v, moves, None if idx == "new" else idx, job.names, dur)
                if mv is not None:
                    items, at = me.upsert(moves, None if idx == "new" else idx, mv, dur)
                elif idx == "new":
                    return None
            if not items or at == "new" or at is None:
                return None
            return renderer.preview_card(items, at, job.names, pipeline.preview_avatars(job), job.output_format)
        except Exception as e:
            log.debug("card preview: %s", e)
            return None

    def load(jid, select=None, draft: me.MoveForm | None = None, msg: str = "", clip_now: str = "") -> dict:
        if not jid:
            return {title: "# Ходы\nРучное управление ходами разбора.",
                    empty: gr.update(visible=True, value=up.empty_state(
                        "Нет задания", "Выберите задание слева или создайте новое на странице «Создание видео».")),
                    work: gr.update(visible=False)}
        job = Job.load(jid)
        if not job.clip.exists():
            return {title: f"# Ходы\n{job.names['white']} vs {job.names['black']} · `{job.id}`",
                    empty: gr.update(visible=True, value=up.empty_state(
                        "Клип ещё не готов", "Ходы появятся, когда видео скачано и обрезано: нажмите «Продолжить выбранное» "
                        "или подготовьте видео для ручного разбора.")),
                    work: gr.update(visible=False)}
        moves = job.load_moves() if job.moves_file.exists() else []
        dur = job.clip_duration()
        if select == "new":
            idx = "new"
        elif isinstance(select, int) and 0 <= select < len(moves):
            idx = select
        else:
            idx = 0 if moves else "new"
        f = draft or (me.form_of(moves[idx]) if idx != "new" else me.default_form(moves, 0.0, dur))
        lane = [{"start": m.start, "end": m.end, "cls": f"mt-{m.move_type}", "label": str(i + 1),
                 "title": f"{i + 1}. {m.title} ({me.fmt_ts(m.start)}–{me.fmt_ts(m.end)})", "value": str(i), "list": LIST,
                 "selected": i == idx} for i, m in enumerate(moves)]
        if idx == "new":
            head_md = "### Новый ход\nЗаполните заголовок и объяснение — время можно взять из плеера кнопкой ⏱ или клавишами I / O."
        else:
            head_md = f"### Ход {idx + 1} из {len(moves)}"
        total = sum(m.end - m.start for m in moves)
        res = {
            title: f"# Ходы\n{job.names['white']} vs {job.names['black']} · {len(moves)} ходов · `{job.id}`",
            empty: gr.update(visible=False), work: gr.update(visible=True),
            timeline: up.timeline(PLAYER, dur, [("Ходы", lane)]),
            listing: gr.update(choices=[(up.move_label(i, m), str(i)) for i, m in enumerate(moves)],
                               value=str(idx) if idx != "new" else None),
            sel: idx, head: head_md,
            m_start: f.start, m_end: f.end, m_speaker: gr.update(choices=_speakers(job), value=f.speaker),
            m_type: f.move_type, m_eval: f.eval_score, m_title: f.title, m_gist: f.gist, m_expl: f.explanation,
            errs: msg, preview: _preview(job, moves, idx, draft, dur) if draft or idx == "new" else _preview(job, moves, idx, None, dur),
            info: f"{len(moves)} ходов · реплики занимают {me.fmt_ts(total)} из {me.fmt_ts(dur)}" if moves else
                  "Ходов пока нет — добавьте первый в нужной точке видео.",
            restore_btn: gr.update(visible=me.has_backup(job.dir)), confirm: None,
        }
        if str(job.clip) != (clip_now or ""):
            res[player] = str(job.clip)
            res[clip_state] = str(job.clip)
        return res

    def _commit(job: Job, moves, cur, form_v: me.MoveForm, dur):
        """Save the edited move if it changed. Returns (moves, saved_move | None, errors, warnings)."""
        if cur is None:
            return moves, None, [], []
        if cur == "new":
            if _untouched(form_v):
                return moves, None, [], []
        elif _same(form_v, me.form_of(moves[cur])):
            return moves, None, [], []
        mv, e, w = me.validate(form_v, moves, None if cur == "new" else cur, job.names, dur)
        if mv is None:
            return moves, None, e, w
        moves, _ = me.upsert(moves, None if cur == "new" else cur, mv, dur)
        pipeline.save_manual_moves(job, moves)
        return moves, mv, [], w

    # ---------------------------------------------------------------- handlers
    def on_pick(jid, value, cur, clip_now, *vals):
        job = Job.load(jid)
        moves = job.load_moves() if job.moves_file.exists() else []
        dur = job.clip_duration()
        target = moves[int(value)] if value not in (None, "") and int(value) < len(moves) else None
        moves, saved, e, w = _commit(job, moves, cur, me.MoveForm(*vals), dur)
        if e:
            return {listing: gr.update(value=None if cur == "new" else str(cur)), errs: _errors_md(e, w),
                    shell.status: "⚠️ Правки текущего хода не сохранены — исправьте ошибки или верните значения"}
        idx = _find(moves, target) if target is not None and moves else (0 if moves else "new")
        res = {**load(jid, idx, clip_now=clip_now), shell.status: "✅ Изменения хода сохранены" if saved else ""}
        return {**res, shell.jobs_nav: shell.nav(jid)} if saved else res

    def on_add(jid, t, clip_now, cur, *vals):
        job = Job.load(jid)
        moves = job.load_moves() if job.moves_file.exists() else []
        dur = job.clip_duration()
        moves, saved, e, w = _commit(job, moves, cur, me.MoveForm(*vals), dur)
        if e:
            return {errs: _errors_md(e, w), shell.status: "⚠️ Сначала сохраните или исправьте текущий ход"}
        draft = me.default_form(moves, float(t or 0), dur)
        res = {**load(jid, "new", draft=draft, clip_now=clip_now),
               shell.status: f"✚ Новый ход с {draft.start}: заполните заголовок и объяснение и нажмите «Сохранить ход»"}
        return {**res, shell.jobs_nav: shell.nav(jid)} if saved else res

    def on_save(jid, cur, clip_now, *vals):
        job = Job.load(jid)
        moves = job.load_moves() if job.moves_file.exists() else []
        dur = job.clip_duration()
        form_v = me.MoveForm(*vals)
        mv, e, w = me.validate(form_v, moves, None if cur in (None, "new") else cur, job.names, dur)
        if mv is None:
            return {errs: _errors_md(e, w), shell.status: f"❌ **Ход не сохранён:** {e[0]}"}
        moves, idx = me.upsert(moves, None if cur in (None, "new") else cur, mv, dur)
        pipeline.save_manual_moves(job, moves)
        msg = f"✅ Ход {idx + 1} сохранён" + (f" · {w[0]}" if w else "")
        return {**load(jid, idx, msg=_errors_md([], w), clip_now=clip_now), shell.status: msg, shell.jobs_nav: shell.nav(jid)}

    def on_swap(step):
        def handler(jid, cur, clip_now, *vals):
            if not isinstance(cur, int):
                return {shell.status: "⚠️ Сначала сохраните ход, потом его можно переставить"}
            job = Job.load(jid)
            moves = job.load_moves()
            dur = job.clip_duration()
            moves, _, e, w = _commit(job, moves, cur, me.MoveForm(*vals), dur)
            if e:
                return {errs: _errors_md(e, w), shell.status: "⚠️ Исправьте ошибки хода перед перестановкой"}
            new_moves, j = me.swap(moves, cur, step, dur)
            if j == cur:
                return {shell.status: "Ход уже " + ("первый" if step < 0 else "последний")}
            pipeline.save_manual_moves(job, new_moves)
            return {**load(jid, j, clip_now=clip_now), shell.jobs_nav: shell.nav(jid),
                    shell.status: f"↕ Ход переставлен на место {j + 1}: он занял время соседнего хода"}
        return handler

    def on_delete(jid, cur, clip_now, conf):
        if cur in (None, "new"):
            return {**load(jid, None, clip_now=clip_now), shell.status: "Черновик нового хода отброшен"}
        if not (conf and conf[0] == "delete" and conf[1] == cur and time.time() - conf[2] < CONFIRM_S):
            return {confirm: ("delete", cur, time.time()),
                    shell.status: f"⚠️ Нажмите «Удалить» ещё раз в течение {CONFIRM_S:.0f} с, чтобы удалить ход {cur + 1}"}
        job = Job.load(jid)
        moves = me.delete(job.load_moves(), cur, job.clip_duration())
        pipeline.save_manual_moves(job, moves)
        return {**load(jid, min(cur, len(moves) - 1) if moves else None, clip_now=clip_now),
                shell.jobs_nav: shell.nav(jid), shell.status: f"🗑 Ход {cur + 1} удалён"}

    def on_restore(jid, clip_now, conf):
        if not (conf and conf[0] == "restore" and time.time() - conf[2] < CONFIRM_S):
            return {confirm: ("restore", None, time.time()),
                    shell.status: "⚠️ Нажмите ещё раз, чтобы вернуть ходы, какими они были до ручных правок"}
        job = Job.load(jid)
        me.restore_backup(job.dir)
        return {**load(jid, 0, clip_now=clip_now), shell.jobs_nav: shell.nav(jid), shell.status: "↺ Возвращены исходные ходы"}

    def on_preview(jid, cur, *vals):
        if not jid or cur is None:
            return {}
        job = Job.load(jid)
        moves = job.load_moves() if job.moves_file.exists() else []
        dur = job.clip_duration()
        form_v = me.MoveForm(*vals)
        _, e, w = me.validate(form_v, moves, None if cur == "new" else cur, job.names, dur)
        return {preview: _preview(job, moves, cur, form_v, dur), errs: _errors_md(e if not _untouched(form_v) else [], w)}

    def on_render(jid, cur, clip_now, *vals):
        form_v = me.MoveForm(*vals)

        def action(run):
            run.job = shell.need_job(jid)
            moves = run.job.load_moves() if run.job.moves_file.exists() else []
            dur = run.job.clip_duration()
            _, _, e, _ = _commit(run.job, moves, cur, form_v, dur)
            if e:
                raise ValueError(f"Текущий ход не сохранён: {e[0]}")
            if not run.job.moves_file.exists():
                raise ValueError("Ходов нет — добавьте хотя бы один ход.")
            pipeline.step_render(run.job, run.progress)

        def finish(job):
            return load(job.id, cur if isinstance(cur, int) else None, clip_now=clip_now)

        yield from shell.live(action, "✅ Видео собрано — оно в предпросмотре на странице «Создание видео»", "Собрать видео",
                              finish=finish)

    # ---------------------------------------------------------------- wiring
    seek_js = f"(s) => {{ window.cdm && cdm.seek('{PLAYER}', s); return []; }}"
    common = dict(show_progress="hidden")
    st = [shell.status, shell.jobs_nav]
    listing.input(on_pick, [shell.job_id, listing, sel, clip_state, *form], outputs + st, **common) \
        .then(None, [m_start], None, js=seek_js)
    add_btn.click(on_add, [shell.job_id, t_now, clip_state, sel, *form], outputs + st,
                  js=f"(jid, t, ...rest) => [jid, window.cdm ? cdm.nowSec('{PLAYER}') : 0, ...rest]", **common)
    save_btn.click(on_save, [shell.job_id, sel, clip_state, *form], outputs + st, **common)
    up_btn.click(on_swap(-1), [shell.job_id, sel, clip_state, *form], outputs + st, **common)
    down_btn.click(on_swap(+1), [shell.job_id, sel, clip_state, *form], outputs + st, **common)
    del_btn.click(on_delete, [shell.job_id, sel, clip_state, confirm], outputs + st, **common)
    restore_btn.click(on_restore, [shell.job_id, clip_state, confirm], outputs + st, **common)
    take_s.click(None, None, [m_start], js=f"() => [window.cdm ? cdm.now('{PLAYER}') : '']")
    take_e.click(None, None, [m_end], js=f"() => [window.cdm ? cdm.now('{PLAYER}') : '']")
    play_btn.click(None, [m_start, m_end], None, js=f"(a, b) => {{ window.cdm && cdm.playRange('{PLAYER}', a, b); return []; }}")
    for c in (m_type, m_speaker):
        c.change(on_preview, [shell.job_id, sel, *form], [preview, errs], **common)
    m_eval.release(on_preview, [shell.job_id, sel, *form], [preview, errs], **common)
    for c in (m_title, m_gist, m_expl, m_start, m_end):
        c.blur(on_preview, [shell.job_id, sel, *form], [preview, errs], **common)
    render_btn.click(on_render, [shell.job_id, sel, clip_state, *form], shell.outs + outputs, **common)

    return SimpleNamespace(load=lambda jid: load(jid, clip_now=""), load_kw=load, outputs=outputs, clip_state=clip_state)

