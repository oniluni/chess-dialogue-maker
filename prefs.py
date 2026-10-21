"""Last choices made in the web UI (LLM provider/model, pause and sound settings),
remembered across restarts in `.ui_prefs.json`. `.env` stays the source of defaults."""
from __future__ import annotations

import json
import logging

import config

log = logging.getLogger(__name__)


def defaults() -> dict:
    return {
        "llm_provider": config.LLM_PROVIDER,
        "llm_model": config.LLM_MODEL or (config.GEMINI_MODEL if config.LLM_PROVIDER == "gemini" else config.OLLAMA_MODEL),
        "pause_on_move": config.PAUSE_ON_MOVE,
        "sfx_enabled": config.SFX_ENABLED,
        "sfx_volume": config.SFX_VOLUME,
        "output_format": config.OUTPUT_FORMAT,
        "typing_cps": config.TYPING_CPS,
        "read_hold": config.READ_HOLD,
        "sfx_pause": config.SFX_PAUSE,
        "sfx_unpause": config.SFX_UNPAUSE,
        "sfx_typing": config.SFX_TYPING,
        "sfx_typing_set": config.SFX_TYPING_SET,
        "sfx_pause_set": config.SFX_PAUSE_SET,
        "style_preset": "Chess.com",        # last preset picked in the style editor
        "review_before_analysis": True,     # stop for the characters and the scene summary before the LLM
        "ui_mode": "guided",                # guided (seven steps) | advanced (every setting at once)
    }


def load() -> dict:
    prefs = defaults()
    try:
        if config.PREFS_FILE.exists():
            saved = json.loads(config.PREFS_FILE.read_text())
            prefs.update({k: v for k, v in saved.items() if k in prefs and v is not None})
    except Exception as e:
        log.warning("Не удалось прочитать %s (%s) — беру настройки из .env", config.PREFS_FILE.name, e)
    return prefs


def save(**values) -> None:
    prefs = load()
    before = (prefs["llm_provider"], prefs["llm_model"])
    prefs.update({k: v for k, v in values.items() if k in prefs and v is not None})
    try:
        config.PREFS_FILE.write_text(json.dumps(prefs, ensure_ascii=False, indent=1))
    except OSError as e:
        log.warning("Не удалось сохранить настройки интерфейса: %s", e)
    after = (prefs["llm_provider"], prefs["llm_model"])
    if after != before or (config.LLM_PROVIDER, config.LLM_MODEL) != after:
        remember_llm(*after)


def remember_llm(provider: str, model: str) -> None:
    """The last provider/model also go to .env, so the CLI and a restarted UI start with them."""
    if (config.LLM_PROVIDER, config.LLM_MODEL) == (provider, model or ""):
        return
    try:
        config.write_env("LLM_PROVIDER", provider)
        config.write_env("LLM_MODEL", model or "")
    except OSError as e:
        log.warning("Не удалось записать выбор нейросети в .env: %s", e)
        return
    config.LLM_PROVIDER, config.LLM_MODEL = provider, model or ""
    log.debug("LLM choice saved to .env: %s / %s", provider, model)
