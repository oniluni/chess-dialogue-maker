"""HTML building blocks shared by the editor pages: timeline, player transport,
empty states, the review-structure board and list-row labels.

Timeline segments carry `data-start` (seek) and `data-value` (the list item to
select); design.HEAD wires the clicks, the playhead and the transport buttons, and
turns the plain list labels (fields joined by SEP) into rich rows.
"""
from __future__ import annotations

import html

import config
import design
from moves_edit import fmt_ts

SEP = " ⁣"   # invisible separator between label fields (the page script splits on it)


def esc(text) -> str:
    return html.escape(str(text or ""), quote=True)


def _tick_step(duration: float) -> float:
    for step in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600):
        if duration / step <= 12:
            return step
    return 1200


def timeline(player: str, duration: float | None, lanes: list[tuple[str, list[dict]]], list_hint: str = "") -> str:
    """lanes: [(name, [{start, end, cls, label, title, value, list, selected}])]."""
    if not duration:
        return '<div class="cdm-tl cdm-tl-empty">Таймлайн появится, когда будет готов клип</div>'
    rows = []
    for name, items in lanes:
        segs = []
        for it in items:
            left = max(0.0, 100 * it["start"] / duration)
            width = max(0.7, 100 * (it["end"] - it["start"]) / duration)
            width = min(width, 100 - left)
            cls = f'cdm-tl-seg {it.get("cls", "")}{" is-selected" if it.get("selected") else ""}'
            segs.append(
                f'<button type="button" class="{cls}" style="left:{left:.3f}%;width:{width:.3f}%" '
                f'data-start="{it["start"]:.2f}" data-value="{esc(it.get("value", ""))}" data-list="{esc(it.get("list", ""))}" '
                f'title="{esc(it.get("title", ""))}"><span>{esc(it.get("label", ""))}</span></button>')
        rows.append(f'<div class="cdm-tl-lane"><div class="cdm-tl-name">{esc(name)}</div>'
                    f'<div class="cdm-tl-track">{"".join(segs)}<div class="cdm-playhead"></div></div></div>')
    step = _tick_step(duration)
    ticks, t = [], 0.0
    while t <= duration + 1e-6:
        ticks.append(f'<span class="cdm-tl-tick" style="left:{100 * t / duration:.3f}%">{fmt_ts(t).rsplit(".", 1)[0]}</span>')
        t += step
    hint = f'<div class="cdm-tl-hint">{esc(list_hint)}</div>' if list_hint else ""
    return (f'<div class="cdm-tl" data-player="{esc(player)}" data-duration="{duration:.3f}">{"".join(rows)}'
            f'<div class="cdm-tl-lane cdm-tl-axis"><div class="cdm-tl-name"></div><div class="cdm-tl-ticks">{"".join(ticks)}</div></div>'
            f'{hint}</div>')


def transport(player: str) -> str:
    play = design.svg("play", size=14)
    return (
        f'<div class="cdm-transport" data-player="{esc(player)}">'
        f'<button type="button" class="cdm-tp" data-cmd="-5" title="Назад 5 с (Shift+←)">−5</button>'
        f'<button type="button" class="cdm-tp" data-cmd="-1" title="Назад 1 с (←)">−1</button>'
        f'<button type="button" class="cdm-tp cdm-tp-play" data-cmd="play" title="Пуск / пауза (пробел)">{play}</button>'
        f'<button type="button" class="cdm-tp" data-cmd="+1" title="Вперёд 1 с (→)">+1</button>'
        f'<button type="button" class="cdm-tp" data-cmd="+5" title="Вперёд 5 с (Shift+→)">+5</button>'
        f'<span class="cdm-tp-time" data-player="{esc(player)}">0:00.0</span>'
        f'<span class="cdm-tp-keys"><kbd>Пробел</kbd> пуск <kbd>←</kbd><kbd>→</kbd> ±1 с <kbd>I</kbd><kbd>O</kbd> начало/конец '
        f'<kbd>N</kbd> новый</span></div>')


def empty_state(title: str, text: str, icon: str = "film") -> str:
    return (f'<div class="cdm-empty">{design.svg(icon, size=28)}<h3>{esc(title)}</h3>'
            f'<p>{esc(text)}</p></div>')


# ---------------------------------------------------------------- list labels (decorated by the page script)
def move_label(i: int, m) -> str:
    return SEP.join([str(i + 1), f"{fmt_ts(m.start)}–{fmt_ts(m.end)}", m.move_type, m.title])


def line_label(ln, moment_no: int) -> str:
    strength = "" if ln.strength is None else f"{ln.strength:+d}"
    return SEP.join([fmt_ts(ln.start), ln.speaker or "-", "★" if ln.key else "", strength,
                     str(moment_no) if moment_no else "", ln.text or "(без текста)"])


def moment_label(p) -> str:
    time_range = f"{fmt_ts(p.start)}–{fmt_ts(p.end)}" if p.lines else "нет реплик"
    return SEP.join([str(p.number), p.move_type, p.title or "Без заголовка", time_range, "!" if p.problems else ""])


# ---------------------------------------------------------------- structure board (manual page)
AUTO_RU = {"speaker": "чей ход", "type": "тип", "eval": "оценка"}


def board(planned, names: dict[str, str], selected: str | None) -> str:
    if not planned:
        return empty_state("Ключевых моментов пока нет",
                           "Отметьте важные реплики звёздочкой и выберите для них «Ключевой момент» — "
                           "здесь появится последовательность ходов будущего разбора.", "sparkles")
    cards = []
    for p in planned:
        style = config.MOVE_TYPES[p.move_type]
        who = names.get(p.speaker or "", "") or "кто?"
        sel = " is-selected" if p.moment.id == selected else ""
        warn = "".join(f'<div class="cdm-card-warn">⚠ {esc(x)}</div>' for x in p.problems)
        autos = [AUTO_RU[k] for k, v in p.auto.items() if v]
        auto = f'<span class="cdm-card-auto" title="рассчитано автоматически">авто: {", ".join(autos)}</span>' if autos else ""
        lines = f"{len(p.lines)} реплик" if len(p.lines) != 1 else "1 реплика"
        time_range = f"{fmt_ts(p.start)}–{fmt_ts(p.end)}" if p.lines else "—"
        cards.append(
            f'<button type="button" class="cdm-card mt-{p.move_type}{sel}{" has-problems" if p.problems else ""}" '
            f'data-value="{esc(p.moment.id)}" data-list="#moments-list" data-start="{p.start:.2f}">'
            f'<div class="cdm-card-top"><span class="cdm-glyph">{esc(style.glyph)}</span>'
            f'<span class="cdm-card-n">Ход {p.number}</span><span class="cdm-card-time">{time_range}</span></div>'
            f'<div class="cdm-card-title">{esc(p.title or "Без заголовка")}</div>'
            f'<div class="cdm-card-meta">{esc(who)} · {esc(style.label_ru)} · {p.eval_score:+.1f} · {lines} {auto}</div>'
            f'<div class="cdm-card-text">{esc(p.explanation)}</div>{warn}</button>')
    return f'<div class="cdm-board">{"".join(cards)}</div>'
