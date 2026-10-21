"""LLM providers for the move analysis: one registry, one interface.

    provider = llm.get_provider("groq", "llama-3.3-70b-versatile")
    text = provider.chat(messages, schema)        # JSON text
    provider.label                                 # "Groq · llama-3.3-70b-versatile"

Providers:
  * ollama        local models (with `pull()` to download one; `recommend_local()` picks sizes for this PC)
  * gemini        Google AI Studio (GEMINI_API_KEY): free tier with limits, or paid after enabling billing
  * anthropic     Claude via the official `anthropic` SDK (ANTHROPIC_API_KEY, paid per token)
  * pollinations  text.pollinations.ai, no key at all (anonymous tier is rate-limited)
  * groq          GROQ_API_KEY
  * openrouter    OPENROUTER_API_KEY (its ":free" models cost nothing)
  * mistral       MISTRAL_API_KEY
  * github        GitHub Models — retired by GitHub on 2026-07-30, kept only so old jobs/prefs load

Pollinations, Groq, OpenRouter and Mistral speak the OpenAI chat-completions protocol,
so they share `OpenAICompatProvider` (plain `requests`, no SDK). Keys come from
`.env` / the environment; `save_key()` validates a key and writes it to `.env`.

The model actually used is always `resolve_model(provider, model)`: a model that belongs
to another provider (e.g. "qwen2.5:7b" for Claude) is replaced by the provider's default.
`MODEL_GUIDE` / `guide_markdown()` describe the researched models (cost, limits, quality).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import requests

import config

log = logging.getLogger(__name__)
TRANSIENT_WAITS = (8, 20, 45)  # s between retries of an overloaded / rate-limited provider
GEMINI_HIDDEN = ("tts", "image", "banana", "lyria", "robotics", "computer-use", "antigravity", "deep-research",
                 "transcribe", "embedding", "aqa", "veo", "imagen", "learnlm", "customtools", "audio", "live")
ANTHROPIC_MAX_TOKENS = 16000          # non-streaming ceiling (thinking + answer); a move analysis needs ~1–3k
ANTHROPIC_EFFORT = "medium"           # output_config.effort for Claude 5.x (Opus 5.5 is always adaptive-thinking)
ANTHROPIC_FALLBACK_BETA = "server-side-fallback-2026-07-01"   # `fallbacks: "default"` on a safety decline
OLLAMA_THINK = False                  # thinking models (qwen3.x, gemma4) answer much faster without a reasoning pass


class LLMError(RuntimeError):
    """Error with a ready Russian explanation (errors.explain uses .title/.hint as is)."""

    def __init__(self, title: str, hint: str = "", detail: str = ""):
        super().__init__(f"{title} {detail}".strip())
        self.title, self.hint = title, hint


@dataclass(frozen=True)
class Spec:
    id: str
    label: str
    kind: str                         # "openai" | "ollama" | "gemini" | "anthropic"
    base_url: str = ""
    chat_url: str = ""                # default: base_url + /chat/completions
    key_env: str | None = None        # None = no key needed
    key_page: str = ""                # where a person creates a key
    default_model: str = ""
    suggested: tuple[str, ...] = ()   # shown even when the live list is unavailable
    note: str = ""
    extra_headers: dict = field(default_factory=dict)
    extra_body: dict = field(default_factory=dict)   # provider-specific request fields
    retired: str = ""                 # non-empty: the service is gone (Russian reason)

    @property
    def needs_key(self) -> bool:
        return self.key_env is not None

    def chat_endpoint(self) -> str:
        return self.chat_url or f"{self.base_url}/chat/completions"


def _env_set(name: str) -> bool:
    """True if the person set NAME explicitly (environment or .env), not just the config default."""
    return bool(os.environ.get(name, "").strip())


# An explicitly configured model wins; otherwise the researched default (config's built-in defaults are stale:
# qwen2.5-coder writes poor Russian, gemini-2.5-flash is only served to projects that used it before).
_OLLAMA_DEFAULT = config.OLLAMA_MODEL if _env_set("OLLAMA_MODEL") else "qwen3.5:9b"
_GEMINI_DEFAULT = config.GEMINI_MODEL if _env_set("GEMINI_MODEL") else "gemini-3.8-flash"

SPECS: dict[str, Spec] = {s.id: s for s in (
    Spec("ollama", "Ollama · локально", "ollama", base_url=config.OLLAMA_URL,
         default_model=_OLLAMA_DEFAULT,
         suggested=("gemma4:12b", "qwen3.5:9b", "gemma4:26b", "qwen3.6:35b", "qwen3.8:27b", "gemma4:31b", "qwen3.5:4b"),
         note="Локально: бесплатно, без лимитов и без интернета, но нужна память. Для русского берите gemma4 или "
              "qwen3.5/3.6/3.8 — старые qwen2.5 (особенно qwen2.5-coder) пишут заметно хуже. "
              "Модель на 9B требует около 16 ГБ памяти, 27–31B — от 32 ГБ."),
    Spec("pollinations", "Pollinations · без ключа", "openai", base_url="https://text.pollinations.ai",
         chat_url="https://text.pollinations.ai/openai", default_model="openai-fast",
         suggested=("openai-fast",),
         # the anonymous tier caps answers at ~1500 tokens: a reasoning model must think briefly
         extra_body={"reasoning_effort": "low"},
         note="Работает без регистрации и ключа. Анонимно — не чаще 1 ответа в 30 секунд с одного IP и только "
              "небольшая модель GPT-OSS 20B: для лучшего русского возьмите Gemini, Mistral или Groq."),
    Spec("github", "GitHub Models", "openai", base_url="https://models.github.ai/inference",
         key_env="GITHUB_TOKEN", key_page="https://github.blog/changelog/2026-07-30-github-models-is-now-retired/",
         default_model="openai/gpt-4o", suggested=(),
         retired="GitHub закрыл сервис GitHub Models 30 июля 2026 года: каталог и API больше не работают ни у кого.",
         note="⛔ Сервис закрыт GitHub 30 июля 2026 года. Выберите другой: Gemini, Mistral, Groq или OpenRouter "
              "(бесплатно с ключом) либо Ollama."),
    Spec("groq", "Groq", "openai", base_url="https://api.groq.com/openai/v1",
         key_env="GROQ_API_KEY", key_page="https://console.groq.com/keys",
         default_model="llama-3.3-70b-versatile",
         suggested=("llama-3.3-70b-versatile", "openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"),
         note="Бесплатный тариф без карты: около 30 запросов в минуту и 1000 в день, но мало токенов в минуту "
              "(8–12 тыс.) — длинный клип может не пройти. Отвечает очень быстро."),
    Spec("openrouter", "OpenRouter", "openai", base_url="https://openrouter.ai/api/v1",
         key_env="OPENROUTER_API_KEY", key_page="https://openrouter.ai/settings/keys",
         default_model="google/gemma-4-31b-it:free",
         suggested=("google/gemma-4-31b-it:free", "google/gemma-4-26b-a4b-it:free", "minimax/minimax-m3.1-flash:free",
                    "nvidia/nemotron-3-ultra-550b-a55b:free"),
         note="Модели с пометкой «:free» бесплатны: 20 запросов в минуту и 50 в день (1000 в день, если хоть раз "
              "пополнить счёт на $10). Список бесплатных часто меняется; бесплатные провайдеры могут сохранять запросы.",
         extra_headers={"HTTP-Referer": "https://github.com/chess-dialogue-maker", "X-Title": "Chess Dialogue Maker"}),
    Spec("mistral", "Mistral AI", "openai", base_url="https://api.mistral.ai/v1",
         key_env="MISTRAL_API_KEY", key_page="https://console.mistral.ai/api-keys",
         default_model="mistral-large-latest",
         suggested=("mistral-large-latest", "mistral-medium-latest", "mistral-large-4", "mistral-small-latest"),
         note="Бесплатный тариф «Experiment» (нужно подтвердить телефон): примерно 1 запрос в секунду и до 1 млрд "
              "токенов в месяц на модель; запросы этого тарифа могут использоваться для обучения. Хорошо пишет по-русски."),
    Spec("gemini", "Gemini", "gemini", key_env="GEMINI_API_KEY", key_page="https://aistudio.google.com/apikey",
         default_model=_GEMINI_DEFAULT,
         suggested=tuple(dict.fromkeys((_GEMINI_DEFAULT, "gemini-3.8-flash", "gemini-3.5-flash-lite",
                                        "gemini-3.1-flash-lite", "gemini-4-argon", "gemini-3.1-pro-preview"))),
         note="Google AI Studio. Бесплатный тариф — модели Flash и Flash-Lite с лимитами в минуту и в день "
              "(свои лимиты — на aistudio.google.com/rate-limit); запросы бесплатного тарифа Google может использовать "
              "для улучшения своих продуктов. Платный тариф — тот же ключ из AI Studio после включения оплаты (Billing) "
              "в Google Cloud: данные не идут на обучение, доступны Pro-модели. Подписки Google AI Pro / Ultra "
              "(бывшие Gemini Advanced, Google One AI Premium) доступа к API не дают. Цены за 1 млн токенов, вход/выход: "
              "3.8 Flash $0.75/$3.75 (до 31.12.2026, затем $1.50/$7.50), 3.5 Flash-Lite $0.30/$2.50, "
              "3.1 Flash-Lite $0.25/$1.50, 4 Argon $2/$10 (с 01.12.2026 — $4/$20), 3.1 Pro $2/$12."),
    Spec("anthropic", "Claude · Anthropic API", "anthropic", key_env="ANTHROPIC_API_KEY",
         key_page="https://console.anthropic.com/settings/keys", default_model="claude-opus-5-5",
         suggested=("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"),
         note="Платно, оплата за токены: предоплаченные кредиты в Anthropic Console (console.anthropic.com → Billing). "
              "Подписки Claude Pro / Max доступа к API не включают. Цены за 1 млн токенов, вход/выход: "
              "Opus 5.5 $4/$20, Sonnet 5.5 $2/$10, Haiku 5.5 $0.10/$0.50. Разбор клипа (≈5 тыс. токенов на входе, "
              "2 тыс. на выходе) на Sonnet 5.5 стоит примерно 3–5 центов, на Haiku 5.5 — доли цента."),
)}
PROVIDER_IDS = tuple(SPECS)
assert set(PROVIDER_IDS) == set(config.LLM_PROVIDERS), "llm.SPECS and config.LLM_PROVIDERS must list the same ids"


def spec(provider: str) -> Spec:
    try:
        return SPECS[provider.lower()]
    except KeyError:
        raise LLMError(f"Неизвестная нейросеть «{provider}».", f"Доступны: {', '.join(PROVIDER_IDS)}.") from None


# ================================================================ model guide (researched 2026-10-20)
# quality: 1–5 for this app's task (Russian prose + structured JSON over a short dialogue), an estimate from the
# providers' model cards / multilingual scores and model size. cost: "free" | "free_tier" | "paid".
_GROQ_LIMITS = "≈30 запросов/мин, 1000/день, 8 тыс. токенов/мин, 200 тыс./день"
_OR_LIMITS = "20 запросов/мин, 50/день (1000/день после пополнения на $10)"
_MISTRAL_LIMITS = "≈1 запрос/с, до 1 млрд токенов/мес на модель (тариф Experiment)"
_GEMINI_FREE = "бесплатно с лимитами (см. aistudio.google.com/rate-limit); платно "
MODEL_GUIDE: list[dict] = [
    # ---- Ollama (local, free, no limits)
    dict(provider="ollama", model="gemma4:31b", label="Gemma 4 31B", cost="free", limits="без лимитов", quality=4,
         speed="медленно без видеокарты 24 ГБ", hardware="≈20 ГБ: видеокарта 24 ГБ или 32 ГБ ОЗУ (на процессоре очень медленно)",
         context=262144, note="Лучшая локальная по многоязычным тестам (MMMLU 88,4 %).",
         source="https://ai.google.dev/gemma/docs/core/model_card_4"),
    dict(provider="ollama", model="qwen3.8:27b", label="Qwen3.8 27B", cost="free", limits="без лимитов", quality=4,
         speed="медленно без видеокарты 24 ГБ", hardware="≈18 ГБ: видеокарта 24 ГБ или 32 ГБ ОЗУ (на процессоре очень медленно)",
         context=262144, note="Новейшая Qwen; режим рассуждений отключаем ради скорости.",
         source="https://ollama.com/library/qwen3.8"),
    dict(provider="ollama", model="gemma4:26b", label="Gemma 4 26B (MoE)", cost="free", limits="без лимитов", quality=4,
         speed="средне: активны ~4 млрд параметров, терпимо и на процессоре", hardware="16–19 ГБ: 24 ГБ ОЗУ или видеокарта 16–24 ГБ",
         context=262144, note="Почти как 31B (MMMLU 86,3 %), но в разы быстрее.",
         source="https://ai.google.dev/gemma/docs/core/model_card_4"),
    dict(provider="ollama", model="qwen3.6:35b", label="Qwen3.6 35B-A3B (MoE)", cost="free", limits="без лимитов", quality=4,
         speed="средне: активны 3 млрд параметров", hardware="≈24 ГБ: 32 ГБ ОЗУ или видеокарта 24 ГБ",
         context=262144, note="Быстрая для своего размера (MMLU-Pro 85,2).",
         source="https://huggingface.co/Qwen/Qwen3.6-35B-A3B"),
    dict(provider="ollama", model="gemma4:12b", label="Gemma 4 12B", cost="free", limits="без лимитов", quality=3,
         speed="быстро на видеокарте 10–12 ГБ, медленно на процессоре", hardware="≈8 ГБ: видеокарта 10–12 ГБ или 16 ГБ ОЗУ",
         context=262144, note="Лучший выбор для обычного ПК (MMMLU 83,4 %).",
         source="https://ai.google.dev/gemma/docs/core/model_card_4"),
    dict(provider="ollama", model="qwen3.5:9b", label="Qwen3.5 9B", cost="free", limits="без лимитов", quality=3,
         speed="быстро на видеокарте 8 ГБ, средне на процессоре", hardware="≈7 ГБ: видеокарта 8 ГБ или 16 ГБ ОЗУ",
         context=262144, note="Сильная маленькая модель (MMMLU 81,2, 201 язык).",
         source="https://huggingface.co/Qwen/Qwen3.5-9B"),
    dict(provider="ollama", model="gpt-oss:20b", label="GPT-OSS 20B (MoE)", cost="free", limits="без лимитов", quality=2,
         speed="быстро: активны 3,6 млрд параметров", hardware="≈14 ГБ: видеокарта 16 ГБ или 24 ГБ ОЗУ",
         context=131072, note="Хорошо держит JSON, но русский текст суховат.",
         source="https://ollama.com/library/gpt-oss"),
    dict(provider="ollama", model="qwen3.5:4b", label="Qwen3.5 4B", cost="free", limits="без лимитов", quality=2,
         speed="быстро даже на процессоре", hardware="≈4 ГБ: 8 ГБ ОЗУ",
         context=262144, note="Для слабых ПК: чаще путает говорящих и шаблонит.",
         source="https://ollama.com/library/qwen3.5"),
    dict(provider="ollama", model="qwen2.5:7b", label="Qwen2.5 7B (старая)", cost="free", limits="без лимитов", quality=2,
         speed="быстро", hardware="≈5 ГБ: видеокарта 6 ГБ или 8 ГБ ОЗУ",
         context=32768, note="Устарела: qwen3.5:9b того же размера пишет по-русски заметно лучше.",
         source="https://ollama.com/library/qwen2.5"),
    # ---- Pollinations (no key)
    dict(provider="pollinations", model="openai-fast", label="GPT-OSS 20B · аноним", cost="free",
         limits="без ключа; ≈1 ответ в 30 с с одного IP, ответ до ~1500 токенов", quality=2, speed="средне", hardware="",
         context=131072, note="Единственная анонимная модель; для запуска «без ничего».",
         source="https://text.pollinations.ai/models"),
    # ---- Gemini (Google AI Studio)
    dict(provider="gemini", model="gemini-3.8-flash", label="Gemini 3.8 Flash", cost="free_tier",
         limits=_GEMINI_FREE + "$0.75/$3.75 за 1 млн (до 31.12.2026)", quality=5, speed="быстро", hardware="",
         context=1048576, note="Лучшее бесплатное качество по-русски; рекомендуемая модель Google.",
         source="https://ai.google.dev/gemini-api/docs/pricing"),
    dict(provider="gemini", model="gemini-3.5-flash-lite", label="Gemini 3.5 Flash-Lite", cost="free_tier",
         limits=_GEMINI_FREE + "$0.30/$2.50 за 1 млн", quality=4, speed="очень быстро", hardware="",
         context=1048576, note="Дешевле и быстрее Flash, лимиты обычно щедрее.",
         source="https://ai.google.dev/gemini-api/docs/pricing"),
    dict(provider="gemini", model="gemini-3.1-flash-lite", label="Gemini 3.1 Flash-Lite", cost="free_tier",
         limits=_GEMINI_FREE + "$0.25/$1.50 за 1 млн", quality=3, speed="очень быстро", hardware="",
         context=1048576, note="Запасной бесплатный вариант, если у других исчерпан дневной лимит.",
         source="https://ai.google.dev/gemini-api/docs/pricing"),
    dict(provider="gemini", model="gemini-4-argon", label="Gemini 4 Argon", cost="paid",
         limits="только платно: $2/$10 за 1 млн (с 01.12.2026 — $4/$20)", quality=5, speed="средне (рассуждает)", hardware="",
         context=1048576, note="Флагман Google; нет в бесплатном тарифе.",
         source="https://ai.google.dev/gemini-api/docs/pricing"),
    dict(provider="gemini", model="gemini-3.1-pro-preview", label="Gemini 3.1 Pro (preview)", cost="paid",
         limits="только платно: $2/$12 за 1 млн", quality=5, speed="средне (рассуждает)", hardware="",
         context=1048576, note="Preview-модель: может смениться без предупреждения.",
         source="https://ai.google.dev/gemini-api/docs/pricing"),
    # ---- Mistral (free «Experiment» tier)
    dict(provider="mistral", model="mistral-large-latest", label="Mistral Large 3", cost="free_tier",
         limits=_MISTRAL_LIMITS + "; платно $0.50/$1.50 за 1 млн", quality=4, speed="быстро", hardware="",
         context=262144, note="Надёжный русский и JSON; бесплатно на тарифе Experiment.",
         source="https://docs.mistral.ai/models/mistral-large-3-25-12.md"),
    dict(provider="mistral", model="mistral-medium-latest", label="Mistral Medium 3.5", cost="free_tier",
         limits=_MISTRAL_LIMITS + "; платно $1.50/$7.50 за 1 млн", quality=4, speed="быстро", hardware="",
         context=262144, note="Новее Large 3, сильнее в рассуждениях.",
         source="https://docs.mistral.ai/models/mistral-medium-3-5-26-04.md"),
    dict(provider="mistral", model="mistral-large-4", label="Mistral Large 4 (preview)", cost="free_tier",
         limits=_MISTRAL_LIMITS + "; платно $1.36/$4.18 за 1 млн", quality=4, speed="средне", hardware="",
         context=1000000, note="Public preview с 6.10.2026: возможны сбои и смена поведения.",
         source="https://docs.mistral.ai/models/mistral-large-4-0.md"),
    dict(provider="mistral", model="mistral-small-latest", label="Mistral Small 4", cost="free_tier",
         limits=_MISTRAL_LIMITS + "; платно $0.15/$0.60 за 1 млн", quality=3, speed="очень быстро", hardware="",
         context=262144, note="Быстрая и дешёвая, для коротких клипов.",
         source="https://docs.mistral.ai/models/mistral-small-4-0-26-03.md"),
    # ---- Groq (free tier, very fast)
    dict(provider="groq", model="llama-3.3-70b-versatile", label="Llama 3.3 70B", cost="free_tier",
         limits="≈30 запросов/мин, 1000/день, 12 тыс. токенов/мин", quality=3, speed="очень быстро", hardware="",
         context=131072, note="Проверенный вариант, больший лимит токенов в минуту.",
         source="https://console.groq.com/docs/models"),
    dict(provider="groq", model="qwen/qwen3.8-27b", label="Qwen3.8 27B (preview)", cost="free_tier",
         limits=_GROQ_LIMITS, quality=4, speed="быстро", hardware="",
         context=131072, note="Лучше по-русски, но preview — может исчезнуть.",
         source="https://console.groq.com/docs/rate-limits"),
    dict(provider="groq", model="openai/gpt-oss-120b", label="GPT-OSS 120B", cost="free_tier",
         limits=_GROQ_LIMITS, quality=3, speed="очень быстро", hardware="",
         context=131072, note="Строгий JSON; длинный клип упирается в 8 тыс. токенов/мин.",
         source="https://console.groq.com/docs/rate-limits"),
    # ---- OpenRouter (":free" models)
    dict(provider="openrouter", model="google/gemma-4-31b-it:free", label="Gemma 4 31B · free", cost="free_tier",
         limits=_OR_LIMITS, quality=4, speed="средне", hardware="",
         context=262144, note="Лучший бесплатный на OpenRouter для русского.",
         source="https://openrouter.ai/docs/api/reference/limits"),
    dict(provider="openrouter", model="google/gemma-4-26b-a4b-it:free", label="Gemma 4 26B · free", cost="free_tier",
         limits=_OR_LIMITS, quality=3, speed="быстро", hardware="",
         context=262144, note="Быстрее 31B, чуть слабее.",
         source="https://openrouter.ai/api/v1/models"),
    dict(provider="openrouter", model="minimax/minimax-m3.1-flash:free", label="MiniMax M3.1 Flash · free", cost="free_tier",
         limits=_OR_LIMITS, quality=3, speed="быстро", hardware="",
         context=1000000, note="Большая быстрая модель с JSON-режимом.",
         source="https://openrouter.ai/api/v1/models"),
    dict(provider="openrouter", model="nvidia/nemotron-3-ultra-550b-a55b:free", label="Nemotron 3 Ultra · free",
         cost="free_tier", limits=_OR_LIMITS, quality=3, speed="медленно (рассуждает)", hardware="",
         context=1000000, note="Очень большая, но без JSON-режима — чаще нужны повторы.",
         source="https://openrouter.ai/api/v1/models"),
    # ---- Claude (paid)
    dict(provider="anthropic", model="claude-opus-5-5", label="Claude Opus 5.5", cost="paid",
         limits="оплата за токены: $4 вход / $20 выход за 1 млн", quality=5, speed="средне (всегда думает)", hardware="",
         context=1000000, note="Самый точный разбор и русский; строгий JSON по схеме.",
         source="https://platform.claude.com/docs/en/about-claude/pricing"),
    dict(provider="anthropic", model="claude-sonnet-5-5", label="Claude Sonnet 5.5", cost="paid",
         limits="оплата за токены: $2 вход / $10 выход за 1 млн", quality=5, speed="быстро", hardware="",
         context=1000000, note="Почти как Opus вдвое дешевле — лучший баланс.",
         source="https://platform.claude.com/docs/en/about-claude/pricing"),
    dict(provider="anthropic", model="claude-haiku-5-5", label="Claude Haiku 5.5", cost="paid",
         limits="оплата за токены: $0.10 вход / $0.50 выход за 1 млн", quality=4, speed="очень быстро", hardware="",
         context=1000000, note="Очень дёшево; для массовых коротких клипов.",
         source="https://platform.claude.com/docs/en/about-claude/pricing"),
]
# local models: (download size GB at the default quant, active params in B for MoE / None for dense)
_LOCAL_REQ: dict[str, tuple[float, float | None]] = {
    "gemma4:31b": (20, None), "qwen3.8:27b": (18, None), "gemma4:26b": (17, 4), "qwen3.6:35b": (24, 3),
    "gemma4:12b": (8, None), "qwen3.5:9b": (7, None), "gpt-oss:20b": (14, 3.6), "qwen3.5:4b": (3.5, None),
    "qwen2.5:7b": (4.7, None),
}
COST_RU = {"free": "бесплатно", "free_tier": "бесплатный тариф", "paid": "платно"}


def guide(provider: str | None = None) -> list[dict]:
    """MODEL_GUIDE rows (all, or of one provider), best quality first within a provider."""
    rows = [g for g in MODEL_GUIDE if provider is None or g["provider"] == provider]
    return rows if provider is None else sorted(rows, key=lambda g: -g["quality"])


def guide_markdown(provider: str | None = None) -> str:
    """A compact Russian Markdown table of the researched models (all providers or one)."""
    if provider is not None:
        s = spec(provider)
        if s.retired:
            return f"⛔ **{s.label}**: {s.retired}"
    rows = guide(provider)
    if not rows:
        return "Для этого сервиса нет справки по моделям."
    local = provider == "ollama"
    head = ([] if provider else ["Сервис"]) + ["Модель", "Цена", "Лимиты", "Качество", "Скорость"]
    head += ["Железо"] if local else []
    head += ["Контекст", "Заметка"]
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for g in rows:
        cells = ([] if provider else [SPECS[g["provider"]].label]) + [
            f"`{g['model']}`", COST_RU.get(g["cost"], g["cost"]), g["limits"],
            "★" * g["quality"] + "☆" * (5 - g["quality"]), g["speed"]]
        cells += [g["hardware"]] if local else []
        cells += [_ctx_ru(g["context"]), g["note"]]
        out.append("| " + " | ".join(c.replace("|", "/") for c in cells) + " |")
    out.append("\nКачество — оценка для разбора русского диалога в JSON (по карточкам моделей и многоязычным тестам), "
               "лимиты и цены — на 20.10.2026 по страницам провайдеров.")
    return "\n".join(out)


def _ctx_ru(tokens: int) -> str:
    return f"{tokens / 1_000_000:g} млн" if tokens >= 1_000_000 else f"{round(tokens / 1024)} тыс."


# ================================================================ hardware → local model advice
def _ram_gb() -> float | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return round(int(line.split()[1]) / 1024 / 1024, 1)
    except (OSError, ValueError, IndexError):
        pass
    return None


def _nvidia_vram_gb() -> float | None:
    """The biggest NVIDIA GPU's memory (GB), None without nvidia-smi. AMD/Intel → None (unknown)."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5, check=True).stdout
        sizes = [float(x) for x in out.split() if x.replace(".", "", 1).isdigit()]
        return round(max(sizes) / 1024, 1) if sizes else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def recommend_local(ram_gb: float | None = None, vram_gb: float | None = None) -> dict:
    """Which local (Ollama) models this computer can run, best first, plus Russian advice.
    {"ram_gb", "vram_gb", "models": [ids], "text": str}. Pass ram_gb/vram_gb to override detection."""
    ram = _ram_gb() if ram_gb is None else ram_gb
    vram = _nvidia_vram_gb() if vram_gb is None else vram_gb
    quality = {g["model"]: g["quality"] for g in guide("ollama")}
    tiers: dict[str, tuple[int, str]] = {}   # model -> (0 = in VRAM, 1 = MoE on CPU, 2 = dense on CPU (slow), why)
    for model, (size, active) in _LOCAL_REQ.items():
        need = size + 1.5            # weights + KV cache for a ~16k context + runtime
        if vram and need <= vram:
            tiers[model] = (0, "целиком в видеопамяти — быстро")
        elif ram and need <= ram * 0.8 + (vram or 0):
            if active is not None and active <= 4:
                tiers[model] = (1, "MoE, терпимо и на процессоре")
            else:
                tiers[model] = (2, "поместится, но на процессоре будет медленно")
    order = sorted(tiers, key=lambda m: (tiers[m][0] > 1, -quality.get(m, 0), tiers[m][0], _LOCAL_REQ[m][0]))
    hw = []
    hw.append(f"ОЗУ {ram:g} ГБ".replace(".", ",") if ram else "ОЗУ не определена")
    hw.append(f"видеокарта NVIDIA {vram:g} ГБ".replace(".", ",") if vram else "видеопамять не определена (нет NVIDIA или nvidia-smi; "
                                                            "AMD/Intel Ollama часто использует только частично)")
    text = f"Найдено: {', '.join(hw)}. "
    if not order:
        text += ("Памяти мало для хороших локальных моделей — используйте бесплатные облачные: Gemini (бесплатный "
                 "тариф), Mistral или Groq, либо Pollinations без ключа.")
    else:
        best = order[0]
        text += f"Лучше всего подойдёт {best} ({tiers[best][1]})."
        better = next((m for m in order[1:] if quality.get(m, 0) > quality.get(best, 0)), None)
        faster = next((m for m in order[1:] if tiers[m][0] < tiers[best][0]), None)
        if better:
            text += f" Если важнее качество — {better} ({tiers[better][1]})."
        if faster:
            text += f" Быстрее — {faster} ({tiers[faster][1]})."
        if quality.get(best, 0) <= 2:
            text += " Для заметно лучшего русского бесплатно — Gemini 3.8 Flash или Mistral."
        text += f" Скачать: кнопка «Скачать модель в Ollama» или `ollama pull {best}`."
    return {"ram_gb": ram, "vram_gb": vram, "models": order, "text": text}


# ================================================================ model ↔ provider consistency
_MODEL_CACHE: dict[str, tuple[float, list[tuple[str, str]]]] = {}
# ids that can only be one provider's (proxies like OpenRouter/Pollinations use "vendor/…" and never match these)
_DISTINCT = {"anthropic": re.compile(r"^claude-"), "gemini": re.compile(r"^(models/)?gemini-")}
_ID_PATTERNS = {
    "anthropic": r"^claude-[a-z0-9][a-z0-9.-]*$",
    "gemini": r"^(models/)?(gemini|gemma)-[\w.-]+$",
    "mistral": r"^(mistral|ministral|magistral|codestral|devstral|pixtral|voxtral|open-mistral|open-mixtral)[\w.-]*$",
    "groq": r"^([\w.-]+/)?[\w.-]+$",
    "openrouter": r"^[\w.-]+/[\w.:-]+$",
    "pollinations": r"^[\w.-]+(/[\w.:-]+)?$",
    "github": r"^[\w.-]+/[\w.-]+$",
    "ollama": r"^([\w.-]+/)*[\w.-]+(:[\w.-]+)?$",
}


def _known(provider: str) -> set[str]:
    s = SPECS[provider]
    return {s.default_model, *s.suggested, *(g["model"] for g in MODEL_GUIDE if g["provider"] == provider)} - {""}


def model_belongs(provider: str, model: str) -> bool:
    """True if `model` is a model of this provider: in its cached/live list, suggested list, default or guide,
    or (when unknown) shaped like its ids; False if it is clearly another provider's model."""
    s = spec(provider)
    model = (model or "").strip()
    if not model:
        return False
    if model in _known(s.id) or (s.id == "gemini" and model.removeprefix("models/") in _known(s.id)):
        return True
    cached = _MODEL_CACHE.get(s.id)
    if cached and model in {m for _, m in cached[1]}:
        return True
    # OpenRouter proxies everyone's "vendor/model" ids, so another provider's list says nothing there
    if s.id != "openrouter" and any(model in _known(o) for o in SPECS if o != s.id):
        return False
    if any(rx.match(model) for o, rx in _DISTINCT.items() if o != s.id):
        return False
    if s.id == "ollama" and model.endswith(":free"):
        return False
    return bool(re.match(_ID_PATTERNS.get(s.id, r"^\S+$"), model))


def resolve_model(provider: str, model: str | None, offline: bool = False) -> str:
    """The model that will actually be used: the given one if it belongs to the provider, else its default.
    offline=True decides from cached lists only (no network)."""
    s = spec(provider)
    model = (model or "").strip()
    if s.id == "gemini":
        model = model.removeprefix("models/")
    return model if model and model_belongs(s.id, model) else default_model(s.id, offline=offline)


def default_model(provider: str, offline: bool = False) -> str:
    """The provider's default model. offline=True uses cached lists only (no network)."""
    s = spec(provider)
    if s.id == config.LLM_PROVIDER and config.LLM_MODEL and model_belongs(s.id, config.LLM_MODEL):
        return config.LLM_MODEL  # the last model chosen for this provider
    if s.id == "ollama":  # the configured default is not downloaded: the best installed model
        try:
            installed = [m for _, m in cached_models("ollama", offline=offline) if m in _ollama_installed_cache()]
        except Exception:
            installed = []
        if installed and not _is_installed(s.default_model, installed):
            return _ollama_rank(installed)[0]
    if s.id == "openrouter":  # the free line-up changes often: take the best free model available now
        try:
            live = [m for _, m in cached_models("openrouter", offline=offline)]
        except Exception:
            live = []
        if live and s.default_model not in live:
            return live[0]
    return s.default_model


def _is_installed(model: str, installed: list[str]) -> bool:
    return model in installed or f"{model}:latest" in installed


def _ollama_rank(installed: list[str]) -> list[str]:
    """Installed Ollama models, best for this task first (guide quality, then size; coder models last)."""
    quality = {g["model"]: g["quality"] for g in MODEL_GUIDE if g["provider"] == "ollama"}

    def key(m: str) -> tuple:
        base = m.removesuffix(":latest")
        return ("coder" in m or "embed" in m, -quality.get(base, 0), -_size_hint(m))

    return sorted(installed, key=key)


def cached_models(provider: str, ttl: float = 300, offline: bool = False) -> list[tuple[str, str]]:
    """`list_models` with a per-provider TTL cache. Only live lists are cached; errors (LLMError) are raised.
    offline=True never touches the network: the cached list whatever its age, else []."""
    s = spec(provider)
    hit = _MODEL_CACHE.get(s.id)
    if offline:
        return list(hit[1]) if hit else []
    if hit and time.monotonic() - hit[0] < ttl:
        return list(hit[1])
    return list_models(s.id)


def clear_model_cache(provider: str | None = None) -> None:
    """Forget cached model lists (after saving a key, or for the ↻ button)."""
    if provider is None:
        _MODEL_CACHE.clear()
    else:
        _MODEL_CACHE.pop(spec(provider).id, None)


# ================================================================ keys
def get_key(provider: str) -> str:
    """The provider's key from the environment / .env ("" if not set)."""
    s = spec(provider)
    return os.environ.get(s.key_env, "").strip() if s.key_env else ""


def key_status(provider: str) -> str:
    s = spec(provider)
    if s.retired:
        return "⛔ Сервис закрыт"
    if not s.needs_key:
        return "🔓 Ключ не нужен"
    key = get_key(provider)
    return f"🔑 Ключ задан ({mask(key)})" if key else f"🔒 Нужен ключ {s.key_env}"


def mask(key: str) -> str:
    return f"{key[:4]}…{key[-4:]}" if len(key) > 12 else "…"


def validate_key(provider: str, key: str) -> None:
    """Raises LLMError if the provider rejects the key."""
    s = spec(provider)
    if s.retired:
        raise LLMError(f"{s.label} больше не работает.", s.retired)
    key = key.strip()
    if not key or re.search(r"\s", key):
        raise LLMError("Ключ пустой или содержит пробелы.", "Скопируйте ключ целиком, без пробелов и переносов.")
    if s.kind == "anthropic":
        _anthropic_validate(key)
        return
    try:
        if s.id == "gemini":
            from google import genai

            client = genai.Client(api_key=key)
            next(iter(client.models.list()))
            return
        if s.id == "openrouter":
            r = requests.get(f"{s.base_url}/key", headers={"Authorization": f"Bearer {key}"}, timeout=20)
        else:
            r = requests.get(f"{s.base_url}/models", headers=_auth(key), timeout=20)
    except requests.RequestException as e:
        raise LLMError(f"Не удалось связаться с {s.label}.", "Проверьте интернет и повторите.", str(e)) from e
    except Exception as e:  # Gemini SDK
        raise LLMError(f"{s.label} не принял ключ.", "Проверьте, что ключ скопирован полностью.", str(e)) from e
    if r.status_code in (401, 403):
        raise LLMError(f"{s.label} не принял ключ (ошибка {r.status_code}).",
                       f"Создайте новый ключ на {s.key_page} и вставьте его целиком.")
    if r.status_code >= 400 and r.status_code != 429:  # 429 = valid key, just rate-limited
        raise LLMError(f"{s.label} ответил ошибкой {r.status_code} при проверке ключа.", "Попробуйте ещё раз позже.",
                       r.text[:200])


def save_key(provider: str, key: str) -> str:
    """Validate, write to .env and activate for this process. Returns a status line."""
    s = spec(provider)
    if not s.needs_key:
        return f"{s.label} работает без ключа."
    key = key.strip()
    validate_key(provider, key)
    config.write_env(s.key_env, key)
    if s.key_env == "GEMINI_API_KEY":
        config.GEMINI_API_KEY = key
    clear_model_cache(s.id)  # the live list depends on the key
    log.info("✓ Ключ %s проверен и сохранён в .env (%s)", s.label, mask(key))
    return f"✅ Ключ {s.label} проверен и сохранён в .env ({mask(key)})"


def _auth(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"} if key else {}


# ================================================================ model lists
def list_models(provider: str) -> list[tuple[str, str]]:
    """(label, model) pairs for the UI: live list where the provider offers one,
    otherwise the suggested models. Ollama marks models that are not downloaded yet.
    A live list is also stored for `cached_models`."""
    s = spec(provider)
    if s.retired:
        return []
    live: list[str] | None
    if s.kind == "ollama":
        installed = OllamaProvider.installed()
        _MODEL_CACHE["_ollama_installed"] = (time.monotonic(), [(m, m) for m in installed])
        extra = [m for m in s.suggested if not _is_installed(m, installed)]
        pairs = [(m, m) for m in installed] + [(f"{m}  · не скачана", m) for m in extra]
        live = installed
    elif s.kind == "gemini":
        live = GeminiProvider.list_models() if get_key("gemini") else None
        pairs = [(m, m) for m in (live or s.suggested)]
    elif s.kind == "anthropic":
        live = _anthropic_models() if get_key("anthropic") else None
        pairs = [(m, m) for m in (live or s.suggested)]
    else:
        live = _live_models(s)
        pairs = [(m, m) for m in (live or s.suggested)]
    if live:
        _MODEL_CACHE[s.id] = (time.monotonic(), list(pairs))
    return pairs


def _ollama_installed_cache() -> set[str]:
    hit = _MODEL_CACHE.get("_ollama_installed")
    return {m for _, m in hit[1]} if hit else set()


def _live_models(s: Spec) -> list[str] | None:
    """The provider's current model list, None if it has none / it is unavailable."""
    try:
        if s.id == "pollinations":
            r = requests.get(f"{s.base_url}/models", timeout=15)
            r.raise_for_status()
            return [m["name"] for m in r.json() if m.get("tier") in (None, "anonymous")] or None
        if s.id == "openrouter":
            r = requests.get(f"{s.base_url}/models", timeout=20)
            r.raise_for_status()
            unfit = ("safety", "guard", "code", "-vl", "omni", "embed", "lfm-")  # moderation / coding / vision / tiny
            free = [m["id"] for m in r.json()["data"]
                    if m["id"].endswith(":free") and not any(u in m["id"] for u in unfit)]
            # researched models first (in guide order), then the rest biggest first
            return ([m for m in s.suggested if m in free]
                    + sorted((m for m in free if m not in s.suggested), key=lambda m: -_size_hint(m))) or None
        key = get_key(s.id)
        if not key:
            return None
        r = requests.get(f"{s.base_url}/models", headers=_auth(key), timeout=20)
        r.raise_for_status()
        ids = [m["id"] for m in r.json().get("data", [])]
        skip = ("whisper", "tts", "embed", "guard", "moderation", "ocr", "audio", "vision", "image", "playai",
                "orpheus", "transcribe", "voxtral", "timestral", "shieldstral")
        ids = sorted(i for i in ids if not any(k in i.lower() for k in skip))
        return ([m for m in s.suggested if m in ids] + [m for m in ids if m not in s.suggested]) or None
    except Exception as e:
        log.debug("model list of %s unavailable: %s", s.id, e)
        return None


def _size_hint(model: str) -> float:
    """Parameter count in billions guessed from the name (for sorting), 0 if unknown."""
    m = re.findall(r"(\d+(?:\.\d+)?)b\b", model.lower())
    return max(map(float, m)) if m else 0.0


# ================================================================ providers
def get_provider(provider: str | None = None, model: str | None = None):
    """A ready provider object; the model is `resolve_model(provider, model)`, so it always matches
    what the UI shows as "will be used"."""
    s = spec(provider or config.LLM_PROVIDER)
    if s.retired:
        raise LLMError(f"{s.label} больше не работает.",
                       f"{s.retired} Выберите другой сервис: Gemini, Mistral, Groq, OpenRouter или Ollama.")
    model = resolve_model(s.id, model)
    if s.kind == "ollama":
        return OllamaProvider(model=model)
    if s.kind == "gemini":
        return GeminiProvider(model=model)
    if s.kind == "anthropic":
        return AnthropicProvider(model=model)
    if s.needs_key and not get_key(s.id):
        raise LLMError(f"Для {s.label} нужен ключ {s.key_env}.",
                       f"Получите его на {s.key_page} и вставьте в поле ключа под выбором нейросети (или в .env), либо выберите Pollinations — он без ключа.")
    return OpenAICompatProvider(s, model)


def strip_reasoning(text: str) -> str:
    """Reasoning models (DeepSeek R1, …) may prepend <think>…</think>."""
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


def _retry_after(r: requests.Response, fallback: float) -> float:
    return _wait_from(r.headers.get("retry-after", ""), r.text, fallback)


def _wait_from(header: str, text: str, fallback: float) -> float:
    """Seconds to wait from a Retry-After header or a "retry after Ns" text, capped at 90 s."""
    if (header or "").replace(".", "", 1).isdigit():
        return min(90.0, float(header) + 1)
    m = re.search(r"[Rr]etry (?:after|in) (\d+(?:\.\d+)?)", text or "")
    return min(90.0, float(m.group(1)) + 1) if m else fallback


class OpenAICompatProvider:
    def __init__(self, s: Spec, model: str):
        self.spec, self.model = s, model
        self.label = f"{s.label} · {model}"
        self._json_mode = True  # response_format; dropped if the model refuses it
        self._extra = dict(s.extra_body)

    def chat(self, messages: list[dict], schema: dict | None = None) -> str:
        s = self.spec
        headers = {"Content-Type": "application/json", **_auth(get_key(s.id)), **s.extra_headers}
        waits = list(TRANSIENT_WAITS)
        while True:
            body = {"model": self.model, "messages": messages, "temperature": config.LLM_TEMPERATURE}
            if self._json_mode:
                body["response_format"] = {"type": "json_object"}
            body.update(self._extra)
            try:
                r = requests.post(s.chat_endpoint(), json=body, headers=headers, timeout=config.LLM_TIMEOUT)
            except requests.Timeout as e:
                raise LLMError(f"{s.label} не ответил за {config.LLM_TIMEOUT} с.",
                               "Попробуйте ещё раз или выберите модель поменьше.") from e
            except requests.RequestException as e:
                raise LLMError(f"Нет связи с {s.label}.", "Проверьте интернет и повторите.", str(e)) from e
            if r.status_code == 400 and self._extra and any(k in r.text for k in self._extra):
                self._extra = {}  # an optional field the model doesn't know
                continue
            if r.status_code == 400 and self._json_mode and re.search(r"response_format|json", r.text, re.I):
                self._json_mode = False  # this model doesn't do JSON mode: the prompt still asks for JSON
                continue
            if r.status_code in (402, 429) or r.status_code >= 500:
                if not waits:
                    break
                wait = _retry_after(r, waits.pop(0))
                why = "просит подождать (лимит запросов)" if r.status_code in (402, 429) else "перегружен"
                log.warning("%s %s, повтор через %.0f с…", s.label, why, wait)
                time.sleep(wait)
                continue
            break
        if r.status_code in (401, 403):
            raise LLMError(f"{s.label} не принял ключ (ошибка {r.status_code}).",
                           f"Создайте новый ключ на {s.key_page} и сохраните его в поле ключа под выбором нейросети.")
        if r.status_code == 404:
            raise LLMError(f"У {s.label} нет модели «{self.model}».", "Выберите другую модель в списке (↻ обновляет список).")
        if r.status_code == 413 or (r.status_code == 400 and re.search(r"tokens per minute|TPM|too large", r.text, re.I)):
            raise LLMError(f"{s.label}: запрос больше бесплатного лимита токенов в минуту для «{self.model}».",
                           "Возьмите фрагмент покороче, модель с большим лимитом (у Groq — llama-3.3-70b-versatile) "
                           "или другого провайдера (Gemini, Mistral).", _first_error(r))
        if r.status_code in (402, 429):
            raise LLMError(f"{s.label}: исчерпан лимит бесплатных запросов.",
                           "Подождите и нажмите «Продолжить выбранное» или выберите другого провайдера.")
        if r.status_code >= 400:
            raise LLMError(f"{s.label} ответил ошибкой {r.status_code}.", "Попробуйте ещё раз или другую модель.",
                           _first_error(r))
        try:
            choice = r.json()["choices"][0]
            msg = choice["message"]
        except (ValueError, KeyError, IndexError) as e:
            raise LLMError(f"{s.label} вернул непонятный ответ.", "Попробуйте ещё раз или другую модель.",
                           r.text[:200]) from e
        content = strip_reasoning(msg.get("content") or "")
        if not content and choice.get("finish_reason") == "length":
            raise LLMError(f"{s.label}: модель израсходовала лимит ответа на рассуждения и не успела написать разбор.",
                           "Повторите или выберите модель без рассуждений / провайдера с большим лимитом (Groq, Mistral).")
        return content


def _first_error(r: requests.Response) -> str:
    try:
        err = r.json().get("error")
        return (err.get("message") if isinstance(err, dict) else str(err))[:200]
    except (ValueError, AttributeError):
        return r.text[:200]


class OllamaProvider:
    def __init__(self, url: str = config.OLLAMA_URL, model: str | None = None):
        self.url, self.model = url.rstrip("/"), model or SPECS["ollama"].default_model
        self.label = f"{SPECS['ollama'].label} · {self.model}"
        self._think = OLLAMA_THINK  # sent as "think"; dropped if this Ollama / model rejects it

    @staticmethod
    def installed(url: str = config.OLLAMA_URL) -> list[str]:
        try:
            r = requests.get(f"{url.rstrip('/')}/api/tags", timeout=5)
            r.raise_for_status()
        except requests.ConnectionError as e:
            raise LLMError("Ollama не запущена.", "Запустите `ollama serve` (или приложение Ollama) — либо выберите другую нейросеть.") from e
        return sorted(m["name"] for m in r.json().get("models", []))

    @staticmethod
    def pull(model: str, progress: Callable[[float, str], None] | None = None, url: str = config.OLLAMA_URL) -> None:
        """Download a model into Ollama, reporting progress."""
        log.info("▶ Скачиваю модель %s в Ollama…", model)
        try:
            with requests.post(f"{url.rstrip('/')}/api/pull", json={"model": model, "stream": True},
                               stream=True, timeout=(10, 600)) as r:
                if r.status_code >= 400:
                    raise LLMError(f"Ollama не смогла скачать {model} (ошибка {r.status_code}).",
                                   "Проверьте название модели на https://ollama.com/library.", r.text[:200])
                last_status = ""
                for line in r.iter_lines():
                    if not line:
                        continue
                    ev = json.loads(line)
                    if ev.get("error"):
                        raise LLMError(f"Ollama не смогла скачать {model}.",
                                       "Проверьте название модели на https://ollama.com/library.", ev["error"])
                    status = ev.get("status", "")
                    if ev.get("total") and progress:
                        done = ev.get("completed", 0) / ev["total"]
                        progress(done, f"Скачивание {model}: {ev.get('completed', 0) / 1e9:.1f} из {ev['total'] / 1e9:.1f} ГБ")
                    elif status != last_status:
                        log.debug("ollama pull: %s", status)
                    last_status = status
        except requests.ConnectionError as e:
            raise LLMError("Ollama не запущена.", "Запустите `ollama serve` и повторите.") from e
        clear_model_cache("ollama")
        _MODEL_CACHE.pop("_ollama_installed", None)
        log.info("✓ Модель %s скачана", model)

    @staticmethod
    def context_for(messages: list[dict]) -> int:
        """num_ctx that fits the prompt plus a ~4k answer (Russian ≈ 2.5 characters per token), 8k–32k."""
        chars = sum(len(m.get("content") or "") for m in messages)
        need = chars / 2.5 + 4096
        return int(min(32768, max(8192, -(-need // 4096) * 4096)))

    def chat(self, messages: list[dict], schema: dict | None = None) -> str:
        waits = [*TRANSIENT_WAITS, None]
        while waits:
            wait = waits[0]
            body = {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "format": schema or "json",  # structured output (Ollama ≥ 0.5)
                "options": {"temperature": config.LLM_TEMPERATURE, "num_ctx": self.context_for(messages)},
            }
            if self._think is not None:
                body["think"] = self._think
            try:
                r = requests.post(f"{self.url}/api/chat", json=body, timeout=config.LLM_TIMEOUT)
            except requests.ConnectionError as e:
                raise LLMError("Ollama не запущена.", "Запустите `ollama serve` (или приложение Ollama) — либо выберите другую нейросеть.") from e
            except requests.Timeout as e:
                raise LLMError(f"Ollama не успела ответить за {config.LLM_TIMEOUT} с.",
                               "Модель слишком тяжёлая для этого компьютера: выберите поменьше или увеличьте LLM_TIMEOUT.") from e
            if r.status_code == 400 and self._think is not None and "think" in r.text.lower():
                self._think = None  # an older Ollama or a model with levels only (gpt-oss)
                continue
            if r.status_code == 404:
                raise LLMError(f"В Ollama нет модели {self.model}.",
                               f"Нажмите «⬇ Скачать модель» или выполните `ollama pull {self.model}`.")
            if r.status_code >= 500 and wait is not None:  # model still loading / out of memory hiccup
                log.warning("Ollama ответила ошибкой %d, повтор через %d с…", r.status_code, wait)
                time.sleep(wait)
                waits.pop(0)
                continue
            r.raise_for_status()
            return strip_reasoning(r.json()["message"]["content"])
        raise LLMError("Ollama не ответила.", "Перезапустите Ollama и повторите.")


class GeminiProvider:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        api_key = api_key or get_key("gemini")
        if not api_key:
            raise LLMError("Не задан ключ Gemini.", "Получите бесплатный ключ на https://aistudio.google.com/apikey "
                           "и сохраните его в поле ключа, или выберите другую нейросеть.")
        from google import genai

        self.client = genai.Client(api_key=api_key)
        self.model = (model or SPECS["gemini"].default_model).removeprefix("models/")
        self.label = f"{SPECS['gemini'].label} · {self.model}"

    @staticmethod
    def list_models(api_key: str | None = None) -> list[str]:
        from google import genai

        names = []
        client = genai.Client(api_key=api_key or get_key("gemini"))  # keep a reference: the pager is lazy
        for m in client.models.list():
            name = m.name.removeprefix("models/")
            if "generateContent" in (m.supported_actions or []) and not any(h in name for h in GEMINI_HIDDEN):
                names.append(name)
        known = [m for m in SPECS["gemini"].suggested if m in names]
        return known + [m for m in names if m not in known]

    def chat(self, messages: list[dict], schema: dict | None = None) -> str:
        from google.genai import errors as genai_errors
        from google.genai import types

        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        contents = [
            types.Content(role="model" if m["role"] == "assistant" else "user", parts=[types.Part(text=m["content"])])
            for m in messages
            if m["role"] != "system"
        ]
        # Gemini 3+ is tuned for its default temperature (1.0); lower values can make it loop
        temperature = None if re.match(r"gemini-([3-9]|\d\d)", self.model) else config.LLM_TEMPERATURE
        for wait in (*TRANSIENT_WAITS, None):
            try:
                resp = self.client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=system,
                        temperature=temperature,
                        response_mime_type="application/json",
                    ),
                )
                return strip_reasoning(resp.text or "")
            except (genai_errors.ServerError, genai_errors.ClientError) as e:
                code = getattr(e, "code", 0) or 0
                text = str(e)
                if _gemini_needs_billing(text):
                    raise LLMError(f"Модель {self.model} не входит в бесплатный тариф Gemini — нужна оплата.",
                                   "Включите оплату (Billing) для проекта в AI Studio — ключ останется тем же — или "
                                   "выберите бесплатную модель: gemini-3.8-flash или gemini-3.5-flash-lite.",
                                   text[:200]) from e
                transient = code >= 500 or (code == 429 and "per day" not in text.lower())
                if not transient or wait is None:
                    raise
                what = "перегружен" if code >= 500 else "просит снизить частоту запросов"
                log.warning("Gemini %s (ошибка %d), повтор через %d с…", what, code, wait)
                time.sleep(wait)
        raise LLMError("Gemini не ответил.", "Повторите позже или выберите другую нейросеть.")


def _gemini_needs_billing(text: str) -> bool:
    """A model outside the free tier (quota "limit: 0") or a project whose billing must be enabled."""
    return bool(re.search(r"free_tier\S*,? limit: 0|limit: 0\b|BILLING_DISABLED|billing to be enabled|"
                          r"requires (an )?active billing|enable billing", text, re.I))


# ================================================================ Claude (official anthropic SDK)
_CLAUDE_BILLING = re.compile(r"credit balance is too low|purchase credits|insufficient (credit|balance|funds)", re.I)


def _anthropic_text(e: Exception) -> str:
    """The API's own error message (no key, no headers)."""
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:300]
    return (getattr(e, "message", "") or str(e))[:300]


def _anthropic_billing_error(detail: str) -> LLMError:
    return LLMError("Claude: на балансе API закончились кредиты — нужна оплата.",
                    "Пополните баланс на console.anthropic.com → Billing (подписка Claude Pro / Max к API не относится) "
                    "или выберите бесплатный сервис: Gemini, Mistral, Groq, OpenRouter или Ollama.", detail)


def _anthropic_error(e: Exception, model: str) -> LLMError:
    """Map an anthropic SDK exception to a Russian LLMError."""
    import anthropic

    s = SPECS["anthropic"]
    detail = _anthropic_text(e)
    status = getattr(e, "status_code", 0) or 0
    if _CLAUDE_BILLING.search(detail):
        return _anthropic_billing_error(detail)
    if isinstance(e, anthropic.AuthenticationError):
        return LLMError(f"{s.label} не принял ключ (ошибка 401).",
                        f"Создайте новый ключ на {s.key_page} и сохраните его в поле ключа под выбором нейросети.", detail)
    if isinstance(e, anthropic.PermissionDeniedError):
        return LLMError(f"{s.label} не принял ключ для этого запроса (ошибка 403).",
                        f"У ключа нет доступа к модели или организации: проверьте ключ на {s.key_page} "
                        "или выберите другую модель Claude.", detail)
    if isinstance(e, anthropic.NotFoundError):
        return LLMError(f"У {s.label} нет модели «{model}».", "Выберите другую модель в списке (↻ обновляет список).", detail)
    if isinstance(e, anthropic.RequestTooLargeError):
        return LLMError(f"{s.label}: запрос слишком большой.", "Возьмите фрагмент покороче.", detail)
    if isinstance(e, anthropic.RateLimitError):
        return LLMError(f"{s.label}: исчерпан лимит запросов вашего уровня (ошибка 429).",
                        "Подождите минуту и нажмите «Продолжить выбранное»; лимиты растут с суммой пополнений "
                        "(console.anthropic.com → Limits). Или выберите другую модель / сервис.", detail)
    if isinstance(e, anthropic.APITimeoutError):
        return LLMError(f"{s.label} не ответил за {config.LLM_TIMEOUT} с.",
                        "Попробуйте ещё раз или выберите модель побыстрее (Sonnet 5.5, Haiku 5.5).")
    if isinstance(e, anthropic.APIConnectionError):
        return LLMError(f"Нет связи с {s.label}.", "Проверьте интернет и повторите.", detail)
    if status == 529 or isinstance(e, anthropic.OverloadedError) or "overloaded" in detail.lower():
        return LLMError(f"{s.label} сейчас перегружен (ошибка 529). Это временно, на стороне Anthropic.",
                        "Подождите 1–2 минуты и нажмите «Продолжить выбранное» — или выберите другую модель.", detail)
    if status >= 500:
        return LLMError(f"Сервер {s.label} ответил ошибкой {status}.",
                        "Обычно это временно: повторите через минуту или выберите другую модель.", detail)
    return LLMError(f"{s.label} отклонил запрос (ошибка {status or '?'}).", "Попробуйте ещё раз или другую модель.", detail)


def _anthropic_client(key: str, timeout: float, retries: int):
    import anthropic

    return anthropic.Anthropic(api_key=key, timeout=timeout, max_retries=retries)


def _anthropic_validate(key: str) -> None:
    import anthropic

    try:
        next(iter(_anthropic_client(key, 20, 0).models.list(limit=1)), None)
    except anthropic.RateLimitError:
        return  # a valid key, just rate-limited
    except anthropic.APIError as e:
        raise _anthropic_error(e, "") from e


def _anthropic_models() -> list[str] | None:
    """Live Claude model ids (suggested first), None when the list is unavailable (network); LLMError on a bad key."""
    import anthropic

    try:
        ids = [m.id for m in _anthropic_client(get_key("anthropic"), 20, 1).models.list(limit=100)]
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
        raise _anthropic_error(e, "") from e
    except anthropic.APIError as e:
        log.debug("Claude model list unavailable: %s", _anthropic_text(e))
        return None
    ids = [i for i in ids if i.startswith("claude-")]
    known = [m for m in SPECS["anthropic"].suggested if m in ids]
    return (known + [m for m in ids if m not in known]) or None


class AnthropicProvider:
    """Claude through the official SDK. JSON schema → structured outputs (`output_config.format`), with a
    fallback to prompt-only JSON if the API rejects the schema; Claude 5.x thinks adaptively (no `thinking`
    or temperature is sent: both are rejected by Opus 5.5), `effort` sets the depth."""

    def __init__(self, model: str | None = None, api_key: str | None = None):
        s = self.spec = SPECS["anthropic"]
        key = api_key or get_key("anthropic")
        if not key:
            raise LLMError(f"Для {s.label} нужен ключ {s.key_env}.",
                           f"Создайте ключ на {s.key_page} (оплата — предоплаченными кредитами в Anthropic Console, "
                           "подписка Claude Pro / Max не подходит) и сохраните его в поле ключа под выбором нейросети — "
                           "или выберите бесплатный сервис.")
        self.model = model or s.default_model
        self.label = f"{s.label} · {self.model}"
        self.client = _anthropic_client(key, config.LLM_TIMEOUT, 2)
        self._schema_ok = True
        self._effort = ANTHROPIC_EFFORT if re.match(r"claude-((opus|sonnet|fable|mythos)-(4-[5-9]|5)|haiku-5)", self.model) else None
        # server-side fallback on a safety decline (not offered for Haiku)
        self._fallbacks = bool(re.match(r"claude-(opus-5|sonnet-5-5|fable-5)", self.model))

    def chat(self, messages: list[dict], schema: dict | None = None) -> str:
        import anthropic

        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        msgs = [{"role": "assistant" if m["role"] == "assistant" else "user", "content": m["content"]}
                for m in messages if m["role"] != "system"]
        waits = list(TRANSIENT_WAITS)
        while True:
            kw: dict = {"model": self.model, "max_tokens": ANTHROPIC_MAX_TOKENS, "messages": msgs}
            if system:
                kw["system"] = system
            out: dict = {}
            if schema is not None and self._schema_ok:
                try:
                    out["format"] = {"type": "json_schema", "schema": anthropic.transform_schema(schema)}
                except (ValueError, TypeError) as e:  # a schema shape the SDK can't convert: JSON by prompt only
                    log.debug("JSON schema not convertible for Claude: %s", e)
                    self._schema_ok = False
            if self._effort:
                out["effort"] = self._effort
            if out:
                kw["output_config"] = out
            try:
                if self._fallbacks:
                    resp = self.client.beta.messages.create(betas=[ANTHROPIC_FALLBACK_BETA], fallbacks="default", **kw)
                else:
                    resp = self.client.messages.create(**kw)
            except anthropic.BadRequestError as e:
                text = _anthropic_text(e)
                if _CLAUDE_BILLING.search(text):
                    raise _anthropic_billing_error(text) from e
                if self._fallbacks and "fallback" in text.lower():
                    self._fallbacks = False
                    continue
                if self._effort and "effort" in text.lower():
                    self._effort = None
                    continue
                if "format" in out and re.search(r"schema|output_config|format", text, re.I):
                    log.debug("Claude rejected the JSON schema, asking for JSON in the prompt only: %s", text)
                    self._schema_ok = False
                    continue
                raise _anthropic_error(e, self.model) from e
            except anthropic.APIStatusError as e:
                status = e.status_code or 0
                text = _anthropic_text(e)
                transient = (status in (429, 529) or status >= 500) and not _CLAUDE_BILLING.search(text)
                if transient and waits:
                    wait = _wait_from(e.response.headers.get("retry-after", ""), text, waits.pop(0))
                    why = "просит подождать (лимит запросов)" if status == 429 else "перегружен"
                    log.warning("%s %s, повтор через %.0f с…", self.spec.label, why, wait)
                    time.sleep(wait)
                    continue
                raise _anthropic_error(e, self.model) from e
            except anthropic.APIError as e:  # connection / timeout (already retried by the SDK)
                raise _anthropic_error(e, self.model) from e
            return self._text(resp)

    def _text(self, resp) -> str:
        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            cat = getattr(details, "category", None)
            raise LLMError(f"Claude отказался отвечать на этот запрос (фильтр безопасности{', категория ' + cat if cat else ''}).",
                           "Такое бывает со сценами насилия, оружия или наркотиков. Повторите или выберите другую "
                           "модель / сервис — например, Gemini или Mistral.")
        parts: list[str] = []
        for block in resp.content:
            if block.type == "fallback":  # text before a fallback came from the model that declined
                parts = []
            elif block.type == "text":
                parts.append(block.text)
        text = strip_reasoning("".join(parts))
        if resp.stop_reason == "max_tokens":
            raise LLMError(f"Ответ Claude получился слишком длинным и обрезан ({ANTHROPIC_MAX_TOKENS} токенов).",
                           "Повторите или возьмите фрагмент покороче.")
        if not text:
            raise LLMError("Claude вернул пустой ответ.", "Повторите или выберите другую модель.")
        return text


# ================================================================ alternatives after a failure
def alternatives(provider: str, model: str, kind: str = "other", limit: int = 10) -> list[tuple[str, str, str]]:
    """(label, provider, model) that can be tried right now instead of a failed one: providers
    without a key or with a key set, installed Ollama models. After a limit / key / connection /
    billing problem the same provider is left out (Gemini keeps its free models after billing);
    after a missing model, other models of it stay."""
    out: list[tuple[str, str, str]] = []
    same_provider_ok = kind in ("model", "output", "temporary", "other") or (kind == "billing" and provider == "gemini")
    paid = {(g["provider"], g["model"]) for g in MODEL_GUIDE if g["cost"] == "paid"}
    for s in SPECS.values():
        if s.retired or (s.id == provider and not same_provider_ok):
            continue
        if s.needs_key and not get_key(s.id):
            continue
        if s.kind == "ollama":
            try:
                models = OllamaProvider.installed()
            except Exception:
                models = []
            if not models:
                continue
            ranked = _ollama_rank(models)
        else:
            ranked = list(dict.fromkeys([default_model(s.id), *s.suggested]))
        if kind == "billing" and s.id == provider:
            ranked = [m for m in ranked if (s.id, m) not in paid]
        for m in ranked[:3]:
            if s.id == provider and m == model:
                continue
            note = "" if not s.needs_key else " · ключ задан"
            out.append((f"{s.label} — {m}{note}", s.id, m))
    return out[:limit]
