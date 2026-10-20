"""«Стиль» page: the visual style of the generated video with a live preview.

The preview is not a mock-up: it is `renderer.Composer` — the object the render loop
uses — asked for one output frame (with the pauses, transitions, subtitles and sound
tags of the chosen moment) on top of the real frame of the clip. Without a job the
same code draws a demo analysis over a neutral picture. Presets are built in
(style.PRESETS) or saved by the person (style_presets.json); a job's style is its
style.json, applied by «Сохранить в задание» or straight away with a re-render.
"""
from __future__ import annotations

import dataclasses
import logging
import re
import tempfile
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

import config
import design
import pipeline
import prefs
import renderer
import style as vs
import ui_parts as up
from analyzer import Move, Segment, Word
from pipeline import Job

log = logging.getLogger(__name__)
FORMATS = [("TikTok 9:16", "tiktok"), ("Исходный кадр", "original"), ("Горизонтально 16:9", "landscape")]
COLORS = [("page_bg", "Фон"), ("card_bg", "Карточка"), ("players_bg", "Панель игроков"), ("text_main", "Текст"),
          ("text_muted", "Второстепенный текст"), ("eval_white", "Шкала: белые"), ("eval_black", "Шкала: чёрные"),
          ("avatar_ring", "Рамка аватарки")]
PREVIEW_W = 560


def _hex(v: str, alpha: str = "") -> str:
    """ColorPicker gives #rrggbb (or rgba(...)); keep an alpha suffix if one is wanted."""
    v = (v or "").strip()
    m = re.match(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)", v)
    if m:
        v = "#" + "".join(f"{int(float(x)):02x}" for x in m.groups())
    if re.fullmatch(r"#[0-9a-fA-F]{8}", v):
        v = v[:7]
    return (v + alpha) if alpha and re.fullmatch(r"#[0-9a-fA-F]{6}", v) else v


# ---------------------------------------------------------------- demo content (no job selected)
DEMO_MOVES = [
    Move(start=0.4, end=3.0, speaker="white", move_type="Best", title="Спокойный вопрос — лучший ход",
         explanation="Он не спорит, а заставляет собеседника объясняться — инициатива у белых.", eval_score=0.8,
         move_number="1 Белые · вопрос вместо ответа"),
    Move(start=3.6, end=6.2, speaker="black", move_type="Blunder", title="Оправдание — зевок",
         explanation="Начав оправдываться, чёрные признают, что им есть что скрывать.", eval_score=3.4,
         move_number="2 Чёрные · оправдание"),
    Move(start=6.8, end=9.4, speaker="white", move_type="Brilliant", title="«Я тот, кто стучит» — блестяще",
         explanation="Одна фраза переворачивает роли: теперь опасность — это он.", eval_score=7.5,
         move_number="3 Белые · смена ролей"),
]


def _demo_segments() -> list[Segment]:
    out = []
    for i, (t0, text) in enumerate(((0.4, "Ну и что ты мне скажешь?"), (3.6, "Я просто хотела тебя защитить"),
                                    (6.8, "Я тот кто стучит"))):
        words, t = [], t0
        for w in text.split():
            words.append(Word(start=round(t, 2), end=round(t + 0.38, 2), word=" " + w))
            t += 0.42
        out.append(Segment(id=i, start=t0, end=round(t, 2), text=text, words=words))
    return out


@lru_cache(maxsize=1)
def _demo_avatars() -> dict[str, Path]:
    import ingest

    d = Path(tempfile.mkdtemp(prefix="cdm-style-"))
    out = {}
    for side, name in (("white", "Белые"), ("black", "Чёрные")):
        out[side] = d / f"{side}.png"
        ingest.placeholder_avatar(name, side).save(out[side])
    return out


# ---------------------------------------------------------------- preview
class Preview:
    """A Composer for the selected job (or the demo), rebuilt when the style or format changes."""

    def __init__(self, job: Job | None, st: vs.VideoStyle, fmt: str):
        self.job = job
        opts = dataclasses.replace(job.render_options() if job else renderer.RenderOptions(), output_format=fmt)
        self.demo = job is None or not job.clip.exists() or not job.moves_file.exists()
        vs.ensure_font(st.font_family)
        if self.demo:
            self.src_size = (1920, 1080)
            self.comp = renderer.Composer(1920, 1080, 10.0, DEMO_MOVES, {"white": "Белые", "black": "Чёрные"},
                                          _demo_avatars(), opts, st, segments=_demo_segments(),
                                          tags=[renderer.SoundTag(7.2, 9.4, "Смех · Чёрные")])
            self.moves = DEMO_MOVES
        else:
            import ingest

            info = ingest.probe(job.clip)
            self.moves = job.load_moves()
            va = pipeline.load_vision(job) if config.VIDEO_MODE == "smart" else None
            import speakers

            self.comp = renderer.Composer(info.width, info.height, info.duration, self.moves, job.names,
                                          pipeline.preview_avatars(job), opts, st, va,
                                          speakers.speaker_at(job.load_speakers()), pipeline.render_segments(job, st),
                                          pipeline.render_tags(job, st))

    @property
    def duration(self) -> float:
        return self.comp.n_out / self.comp.fps

    def image(self, t: float) -> Image.Image:
        k = self.comp.out_frame(t)
        src_idx = int(self.comp.sched.src[k])
        if self.demo:
            frame = renderer.placeholder_frame((self.comp.dw, self.comp.dh))
        else:
            frame = renderer.decode_frame(self.job.clip, src_idx, (self.comp.dw, self.comp.dh), self.comp.fps)
        self.comp._tile_key = self.comp._ui_key = None  # a single frame: draw everything
        img = Image.fromarray(self.comp.frame(k, frame, src_idx).copy())
        scale = PREVIEW_W / max(img.width, img.height) * (1.6 if img.width > img.height else 1.0)
        return img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS) if scale < 1 else img

    def move_times(self) -> list[float]:
        """Output second where each move's card is fully on screen (the middle of its pause)."""
        sched, fps = self.comp.sched, self.comp.fps
        out = []
        for i in range(len(self.moves)):
            ks = [k for k, s in enumerate(sched.states) if s.idx == i and s.reveal is None and s.card_mix >= 1]
            out.append(round((ks[min(len(ks) - 1, 6)] if ks else 0) / fps, 2))
        return out


def build(shell) -> SimpleNamespace:
    gr = shell.gr
    presets = vs.all_presets()
    d = vs.default_style()
    cache: dict = {}

    gr.Markdown("# Стиль видео\nВнешний вид разбора: цвета, шрифты, раскладка, анимация, субтитры и звуковые метки. "
                "Превью рисует тот же код, что собирает видео.", elem_id="style-title", elem_classes="page-title")
    with gr.Row(elem_id="style-grid", equal_height=False):
        with gr.Column(elem_id="style-controls", elem_classes="cdm-form", min_width=0):
            with gr.Row(elem_classes="cdm-pair"):
                preset = gr.Dropdown(list(presets), value=d.name if d.name in presets else None, label="Пресет",
                                     elem_id="st-preset", allow_custom_value=True)
                preset_name = gr.Textbox(label="Сохранить как пресет", placeholder="Название", max_lines=1,
                                         elem_id="st-preset-name")
            with gr.Row(elem_classes="cdm-actions"):
                save_preset = gr.Button("Сохранить пресет", icon=design.icon("check"), variant="secondary", scale=0)
                del_preset = gr.Button("Удалить пресет", icon=design.icon("trash"), variant="secondary", scale=0,
                                       elem_classes="cdm-danger")
                default_btn = gr.Button("Для новых видео", icon=design.icon("sparkles"),
                                        variant="secondary", scale=0)
            with gr.Accordion("Цвета", open=True):
                with gr.Row(elem_classes="cdm-trio"):
                    colors = {k: gr.ColorPicker(getattr(d, k), label=lbl, elem_id=f"st-{k}") for k, lbl in COLORS}
                with gr.Row(elem_classes="cdm-pair"):
                    accent = gr.Radio(vs.ACCENTS, value=d.accent, label="Цвет хода на карточке", elem_classes="cdm-seg")
                    title_accent = gr.Checkbox(d.title_accent, label="Заголовок цветом хода")
                with gr.Row(elem_classes="cdm-trio"):
                    move_cols = {t: gr.ColorPicker(d.move_color(t), label=config.MOVE_TYPES[t].label_ru,
                                                   elem_id=f"st-mt-{t}") for t in config.MOVE_TYPES}
            with gr.Accordion("Шрифт", open=False):
                with gr.Row(elem_classes="cdm-pair"):
                    font = gr.Dropdown(list(vs.FONTS), value=d.font_family, label="Гарнитура (с кириллицей)")
                    title_upper = gr.Checkbox(d.title_upper, label="Заголовки заглавными")
                with gr.Row(elem_classes="cdm-pair"):
                    title_scale = gr.Slider(0.7, 1.5, value=d.title_scale, step=0.02, label="Размер заголовка", buttons=[])
                    body_scale = gr.Slider(0.7, 1.5, value=d.body_scale, step=0.02, label="Размер текста", buttons=[])
            with gr.Accordion("Раскладка", open=False):
                with gr.Row(elem_classes="cdm-pair"):
                    ui_position = gr.Radio(vs.POSITIONS, value=d.ui_position, label="Разбор в вертикальном видео",
                                           elem_classes="cdm-seg")
                    panel_side = gr.Radio(vs.PANEL_SIDES, value=d.panel_side, label="Панель в горизонтальном",
                                          elem_classes="cdm-seg")
                with gr.Row(elem_classes="cdm-pair"):
                    card_margin = gr.Slider(0, 80, value=d.card_margin, step=2, label="Отступ карточки, px", buttons=[])
                    card_radius = gr.Slider(0, 48, value=d.card_radius, step=2, label="Скругление, px", buttons=[])
                with gr.Row(elem_classes="cdm-trio"):
                    show_players = gr.Checkbox(d.show_players, label="Панель игроков")
                    show_eval = gr.Checkbox(d.show_eval, label="Шкала оценки")
                    show_icon = gr.Checkbox(d.show_icon, label="Значок хода")
            with gr.Accordion("Анимация и переходы", open=False):
                transition = gr.Radio(vs.TRANSITIONS, value=d.transition, label="Смена карточки", elem_classes="cdm-seg")
                gr.Markdown("«По смыслу хода»: блестящий ход и резкий перепад оценки — вспышка цвета хода, зевок — "
                            "встряска, смена говорящего — сдвиг с его стороны.", elem_classes="hint")
                with gr.Row(elem_classes="cdm-trio"):
                    card_fade = gr.Slider(0, 1, value=d.card_fade, step=0.02, label="Смена карточки, с", buttons=[])
                    eval_anim = gr.Slider(0, 2, value=d.eval_anim, step=0.05, label="Движение шкалы, с", buttons=[])
                    icon_anim = gr.Slider(0, 1, value=d.icon_anim, step=0.05, label="Появление значка, с", buttons=[])
            with gr.Accordion("Субтитры", open=False):
                with gr.Row(elem_classes="cdm-trio"):
                    subtitles = gr.Checkbox(d.subtitles, label="Показывать субтитры")
                    sub_box = gr.Checkbox(d.sub_box, label="Плашка под текстом")
                    sub_upper = gr.Checkbox(d.sub_upper, label="Заглавными")
                with gr.Row(elem_classes="cdm-trio"):
                    sub_color = gr.ColorPicker(d.sub_color, label="Цвет текста")
                    sub_hl_on = gr.Checkbox(bool(d.sub_highlight), label="Подсвечивать слово")
                    sub_highlight = gr.ColorPicker(d.sub_highlight or "#ffd34d", label="Цвет подсветки")
                with gr.Row(elem_classes="cdm-trio"):
                    sub_bg = gr.ColorPicker(_hex(d.sub_bg), label="Цвет плашки")
                    sub_size = gr.Slider(24, 96, value=d.sub_size, step=2, label="Размер", buttons=[])
                    sub_words = gr.Slider(1, 14, value=d.sub_words, step=1, label="Слов в строке", buttons=[])
                sub_position = gr.Slider(0.1, 0.95, value=d.sub_position, step=0.01, label="Высота: 0 — верх, 1 — низ видео",
                                         buttons=[])
            with gr.Accordion("Звуковые метки", open=False):
                with gr.Row(elem_classes="cdm-pair"):
                    sound_tags = gr.Checkbox(d.sound_tags, label="Показывать отмеченные звуки")
                    tag_position = gr.Radio(vs.TAG_POSITIONS, value=d.tag_position, label="Где", elem_classes="cdm-seg")
                with gr.Row(elem_classes="cdm-pair"):
                    tag_bg = gr.ColorPicker(_hex(d.tag_bg), label="Фон метки")
                    tag_text = gr.ColorPicker(d.tag_text, label="Текст метки")
        with gr.Column(elem_id="style-preview-col", min_width=0):
            with gr.Row(elem_classes="cdm-pair"):
                fmt = gr.Radio(FORMATS, value="tiktok", label="Формат", elem_classes="cdm-seg", elem_id="st-format")
                jump = gr.Dropdown([], label="Показать момент", elem_id="st-jump")
            t_out = gr.Slider(0, 10, value=1.0, step=0.05, label="Время в готовом видео, с", buttons=[], elem_id="st-time")
            preview = gr.Image(show_label=False, interactive=False, type="pil", elem_id="style-preview", buttons=[],
                               height="clamp(360px, calc(100vh - 330px), 980px)")
            problems = gr.Markdown("", elem_id="style-problems")
            source_md = gr.Markdown("", elem_classes="hint", elem_id="style-source")
            with gr.Row(elem_classes="cdm-actions"):
                apply_btn = gr.Button("Сохранить в задание", icon=design.icon("check", "dark"), variant="primary", scale=0,
                                      elem_id="st-apply")
                render_btn = gr.Button("Сохранить и собрать видео", icon=design.icon("film"), variant="secondary", scale=0,
                                       elem_id="st-render")

    simple = {"accent": accent, "title_accent": title_accent, "font_family": font, "title_upper": title_upper,
              "title_scale": title_scale, "body_scale": body_scale, "ui_position": ui_position, "panel_side": panel_side,
              "card_margin": card_margin, "card_radius": card_radius, "show_players": show_players, "show_eval": show_eval,
              "show_icon": show_icon, "transition": transition, "card_fade": card_fade, "eval_anim": eval_anim,
              "icon_anim": icon_anim, "subtitles": subtitles, "sub_box": sub_box, "sub_upper": sub_upper,
              "sub_color": sub_color, "sub_size": sub_size, "sub_words": sub_words, "sub_position": sub_position,
              "sound_tags": sound_tags, "tag_position": tag_position, "tag_text": tag_text, **colors}
    controls = [*simple.values(), *move_cols.values(), sub_hl_on, sub_highlight, sub_bg, tag_bg]
    n_simple, n_moves = len(simple), len(move_cols)
    outputs = [preset, *controls, fmt, t_out, jump, preview, problems, source_md]

    def read(name: str, vals) -> tuple[vs.VideoStyle, list[str]]:
        data = dict(zip(simple, vals[:n_simple]))
        for k in list(data):
            if k in colors or k in ("sub_color", "tag_text"):
                data[k] = _hex(data[k])
        mc = dict(zip(move_cols, vals[n_simple:n_simple + n_moves]))
        data["move_colors"] = {t: _hex(c) for t, c in mc.items() if _hex(c).lower() != config.MOVE_TYPES[t].color.lower()}
        hl_on, hl, sbg, tbg = vals[n_simple + n_moves:]
        data["sub_highlight"] = _hex(hl) if hl_on else ""
        data["sub_bg"] = _hex(sbg, "a6")
        data["tag_bg"] = _hex(tbg, "cc")
        data["name"] = name or "Свой"
        st, problems_ = vs.validate(data)
        return st, problems_ + [p for p in st.problems() if p not in problems_]

    def values_of(st: vs.VideoStyle) -> list:
        vals = [getattr(st, k) for k in simple]
        vals += [st.move_color(t) for t in move_cols]
        vals += [bool(st.sub_highlight), st.sub_highlight or "#ffd34d", _hex(st.sub_bg), _hex(st.tag_bg)]
        return vals

    def get_preview(jid, st: vs.VideoStyle, fmt_v: str) -> Preview:
        job = Job.load(jid) if jid else None
        key = (jid, st.key(), fmt_v, job.moves_file.stat().st_mtime if job and job.moves_file.exists() else 0)
        if key not in cache:
            cache.clear()
            cache[key] = Preview(job, st, fmt_v)
        return cache[key]

    def render_view(jid, st, problems_, fmt_v, t, keep_time=True) -> dict:
        try:
            pv = get_preview(jid, st, fmt_v)
        except Exception as e:
            log.debug("style preview: %s", e, exc_info=True)
            return {preview: None, problems: f"<div class='cdm-msg cdm-msg-error'>✗ Превью не получилось: {up.esc(e)}</div>"}
        dur = round(pv.duration, 2)
        t = min(float(t or 0), dur) if keep_time else (pv.move_times()[0] if pv.moves else 1.0)
        times = pv.move_times()
        msgs = "".join(f"<div class='cdm-msg cdm-msg-warn'>⚠ {up.esc(p)}</div>" for p in problems_)
        src = ("Демо-разбор поверх условной картинки — выберите задание с готовыми ходами, чтобы увидеть свой ролик."
               if pv.demo else f"Задание `{jid}` · {len(pv.moves)} ходов · видео с паузами {dur:.0f} с")
        return {preview: pv.image(t), t_out: gr.update(maximum=max(1.0, dur), value=t), problems: msgs, source_md: src,
                jump: gr.update(choices=[(f"Ход {i + 1}: {m.title[:40]}", str(tm)) for i, (m, tm) in enumerate(zip(pv.moves, times))],
                                value=None)}

    def load(jid) -> dict:
        st = vs.load_job_style(Job.load(jid).dir) if jid else vs.default_style()
        fmt_v = Job.load(jid).output_format if jid else "tiktok"
        res = dict(zip(controls, values_of(st)))
        res[preset] = gr.update(choices=list(vs.all_presets()), value=st.name)
        res[fmt] = fmt_v
        res.update(render_view(jid, st, st.problems(), fmt_v, 0, keep_time=False))
        return res

    # ---------------------------------------------------------------- handlers
    def on_change(jid, name, fmt_v, t, *vals):
        st, problems_ = read(name, vals)
        return render_view(jid, st, problems_, fmt_v, t)

    def on_time(jid, name, fmt_v, t, *vals):
        st, _ = read(name, vals)
        try:
            return {preview: get_preview(jid, st, fmt_v).image(float(t or 0))}
        except Exception as e:
            log.debug("style preview: %s", e)
            return {}

    def on_jump(jid, name, fmt_v, when, *vals):
        if not when:
            return {}
        st, _ = read(name, vals)
        return {preview: get_preview(jid, st, fmt_v).image(float(when)), t_out: float(when)}

    def on_preset(jid, name, fmt_v, t):
        st = vs.all_presets().get(name)
        if st is None:
            return {}
        res = dict(zip(controls, values_of(st)))
        res.update(render_view(jid, st, st.problems(), fmt_v, t))
        return {**res, shell.status: f"🎨 Пресет «{name}» — сохраните его в задание, чтобы применить к видео"}

    def on_save_preset(jid, new_name, name, fmt_v, t, *vals):
        st, problems_ = read(name, vals)
        try:
            st = vs.save_preset(st, new_name)
        except ValueError as e:
            return {shell.status: f"⚠️ {e}"}
        return {preset: gr.update(choices=list(vs.all_presets()), value=st.name),
                shell.status: f"✅ Пресет «{st.name}» сохранён"}

    def on_del_preset(name):
        if name in vs.PRESETS:
            return {shell.status: "⚠️ Встроенный пресет удалить нельзя"}
        ok = vs.delete_preset(name)
        return {preset: gr.update(choices=list(vs.all_presets()), value=vs.DEFAULT.name),
                shell.status: f"🗑 Пресет «{name}» удалён" if ok else "Такого пресета нет"}

    def on_default(name):
        if name not in vs.all_presets():
            return {shell.status: "⚠️ Сначала сохраните стиль как пресет — по умолчанию можно выбрать только пресет"}
        prefs.save(style_preset=name)
        return {shell.status: f"✅ Новые видео будут в стиле «{name}»"}

    def on_apply(jid, name, *vals):
        if not jid:
            return {shell.status: "⚠️ Выберите задание слева, чтобы сохранить в него стиль"}
        st, problems_ = read(name, vals)
        job = Job.load(jid)
        vs.save_job_style(job.dir, st)
        warn = f" · {problems_[0]}" if problems_ else ""
        return {shell.status: f"✅ Стиль «{st.name}» сохранён в задание — он применится при сборке видео{warn}"}

    def on_render(jid, name, *vals):
        st, _ = read(name, vals)

        def action(run):
            run.job = shell.need_job(jid)
            if not run.job.moves_file.exists():
                raise ValueError("Ходов ещё нет: сначала создайте разбор.")
            vs.save_job_style(run.job.dir, st)
            pipeline.step_render(run.job, run.progress)

        yield from shell.live(action, f"✅ Видео собрано в стиле «{st.name}» — оно на странице «Создание видео»",
                              "Сохранить и собрать видео", finish=lambda job: {})

    # ---------------------------------------------------------------- wiring
    hid = dict(show_progress="hidden")
    base_in = [shell.job_id, preset, fmt, t_out]
    live = dict(hid, trigger_mode="always_last")  # only user edits redraw (a preset sets 40 controls at once)
    for c in controls:
        (c.release if isinstance(c, gr.Slider) else c.input)(on_change, base_in + controls, outputs, **live)
    fmt.input(on_change, base_in + controls, outputs, **live)
    t_out.release(on_time, base_in + controls, [preview], **live)
    jump.input(on_jump, [shell.job_id, preset, fmt, jump, *controls], [preview, t_out], **live)
    preset.input(on_preset, [shell.job_id, preset, fmt, t_out], outputs + [shell.status], **hid)
    save_preset.click(on_save_preset, [shell.job_id, preset_name, preset, fmt, t_out, *controls], [preset, shell.status], **hid)
    del_preset.click(on_del_preset, [preset], [preset, shell.status], **hid)
    default_btn.click(on_default, [preset], [shell.status], **hid)
    apply_btn.click(on_apply, [shell.job_id, preset, *controls], [shell.status], **hid)
    render_btn.click(on_render, [shell.job_id, preset, *controls], shell.outs, **hid)

    return SimpleNamespace(load=load, outputs=outputs)
