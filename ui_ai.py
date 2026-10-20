"""The «нейросеть не справилась» dialog: what went wrong (temporary failure, limit, key,
missing model, no connection, malformed answer), what is already done and kept, and
the models that can be used right now. «Повторить» repeats the very same request with
the current model, «Переключить и повторить» with the chosen one — the job keeps its
clip, transcript, faces, sounds, summary and every manual edit, so nothing is redone
except the failed request (review.py maps the stored request to its action).
"""
from __future__ import annotations

from types import SimpleNamespace

import design
import errors
import llm
import ui_parts as up

OPS = {"run": "создание видео", "analysis": "разбор ходов", "reanalyze": "новый разбор ходов",
       "summary": "сводка сцены", "flow": "подготовка разбора", "refine": "уточнение описания"}


def done_list(job) -> str:
    """What the job already has (kept for the retry)."""
    if job is None:
        return ""
    import cast
    import sounds
    import summary

    items = [("клип", job.clip.exists()), ("речь", job.transcript.exists()),
             ("лица", (job.dir / cast.FILE).exists()), ("кто говорит", job.speakers_file.exists()),
             ("звуки", sounds.Sounds.exists(job.dir)), ("сводка", summary.Summary.exists(job.dir)),
             ("ходы", job.moves_file.exists()), ("видео", job.final.exists())]
    have = [n for n, ok in items if ok]
    return ("Уже готово и сохранено: " + ", ".join(have) + ". Повтор начнёт с места сбоя, ваши правки не тронуты."
            if have else "")


def build(gr) -> SimpleNamespace:
    ctx = gr.State(None)   # the failed request: {"op", "job", "provider", "model", ...}
    with gr.Column(visible=False, elem_id="ai-modal") as modal:
        with gr.Column(elem_id="ai-card"):
            title = gr.Markdown("", elem_id="ai-title")
            body = gr.HTML("", elem_id="ai-body", container=False, padding=False)
            alts = gr.Radio([], label="Можно попробовать сейчас", elem_id="ai-alts")
            with gr.Row(elem_classes="cdm-actions"):
                retry = gr.Button("Повторить с этой же моделью", icon=design.icon("rotate"), variant="secondary",
                                  elem_id="ai-retry", scale=0)
                switch = gr.Button("Переключить и повторить", icon=design.icon("sparkles", "dark"), variant="primary",
                                   elem_id="ai-switch", scale=0)
                close = gr.Button("Закрыть", variant="secondary", elem_id="ai-close", scale=0)
    outputs = [modal, title, body, alts, ctx]

    def open_(e: BaseException, op: dict, job=None) -> dict:
        kind = errors.ai_kind(e)
        head, advice = errors.AI_KINDS[kind]
        why = errors.explain(e)
        provider = (op or {}).get("provider") or (job.llm_provider if job else "")
        model = (op or {}).get("model") or (job.llm_model if job else "")
        label = llm.SPECS[provider].label if provider in llm.SPECS else provider
        choices = [(lbl, f"{p}|{m}") for lbl, p, m in llm.alternatives(provider, model, kind)]
        what = OPS.get((op or {}).get("stage") or (op or {}).get("op"), "запрос")
        html = (f"<p class='cdm-ai-what'>Не получилось: <b>{up.esc(what)}</b> · {up.esc(label)} · <code>{up.esc(model)}</code></p>"
                f"<div class='cdm-msg cdm-msg-error'>✗ {up.esc(why.title)}{(' ' + up.esc(why.hint)) if why.hint else ''}</div>"
                f"<p>{up.esc(advice)}</p>")
        kept = done_list(job)
        if kept:
            html += f"<div class='cdm-msg cdm-msg-ok'>✓ {up.esc(kept)}</div>"
        if not choices:
            html += ("<div class='cdm-msg cdm-msg-warn'>⚠ Других доступных моделей сейчас нет: добавьте ключ сервиса "
                     "в разделе «Нейросеть» (Groq, Mistral, OpenRouter бесплатны) или запустите Ollama.</div>")
        same_ok = kind not in ("auth", "model")
        return {modal: gr.update(visible=True), title: f"### {head}", body: html,
                alts: gr.update(choices=choices, value=choices[0][1] if choices else None, visible=bool(choices)),
                ctx: {**(op or {}), "provider": provider, "model": model, "job": (op or {}).get("job") or (job.id if job else None)},
                retry: gr.update(visible=same_ok), switch: gr.update(interactive=bool(choices))}

    close.click(lambda: gr.update(visible=False), None, modal, show_progress="hidden")
    return SimpleNamespace(modal=modal, title=title, body=body, alts=alts, ctx=ctx, retry=retry, switch=switch,
                           close=close, open=open_, outputs=outputs + [retry, switch])
