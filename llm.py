"""LLM providers for the move analysis: one registry, one interface.

    provider = llm.get_provider("groq", "llama-3.3-70b-versatile")
    text = provider.chat(messages, schema)        # JSON text

Providers:
  * ollama        local models (with `pull()` to download one)
  * gemini        Google AI Studio (GEMINI_API_KEY)
  * pollinations  text.pollinations.ai, no key at all (anonymous tier is rate-limited)
  * github        GitHub Models (GITHUB_TOKEN: a token with the "models" permission)
  * groq          GROQ_API_KEY
  * openrouter    OPENROUTER_API_KEY (its ":free" models cost nothing)
  * mistral       MISTRAL_API_KEY

Everything except Ollama and Gemini speaks the OpenAI chat-completions protocol,
so they share `OpenAICompatProvider` (plain `requests`, no SDK). Keys come from
`.env` / the environment; `save_key()` validates a key and writes it to `.env`.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable

import requests

import config

log = logging.getLogger(__name__)
TRANSIENT_WAITS = (8, 20, 45)  # s between retries of an overloaded / rate-limited provider
GEMINI_HIDDEN = ("tts", "image", "banana", "lyria", "robotics", "computer-use", "antigravity", "deep-research",
                 "transcribe", "embedding", "aqa", "veo", "imagen", "learnlm", "customtools", "audio", "live")


class LLMError(RuntimeError):
    """Error with a ready Russian explanation (errors.explain uses .title/.hint as is)."""

    def __init__(self, title: str, hint: str = "", detail: str = ""):
        super().__init__(f"{title} {detail}".strip())
        self.title, self.hint = title, hint


@dataclass(frozen=True)
class Spec:
    id: str
    label: str
    kind: str                         # "openai" | "ollama" | "gemini"
    base_url: str = ""
    chat_url: str = ""                # default: base_url + /chat/completions
    key_env: str | None = None        # None = no key needed
    key_page: str = ""                # where a person creates a key
    default_model: str = ""
    suggested: tuple[str, ...] = ()   # shown even when the live list is unavailable
    note: str = ""
    extra_headers: dict = field(default_factory=dict)
    extra_body: dict = field(default_factory=dict)   # provider-specific request fields

    @property
    def needs_key(self) -> bool:
        return self.key_env is not None

    def chat_endpoint(self) -> str:
        return self.chat_url or f"{self.base_url}/chat/completions"


SPECS: dict[str, Spec] = {s.id: s for s in (
    Spec("ollama", "Ollama · локально", "ollama", base_url=config.OLLAMA_URL,
         default_model=config.OLLAMA_MODEL,
         suggested=("qwen2.5:14b", "gemma2:9b", "deepseek-r1:14b", "qwen2.5:7b"),
         note="Локальные модели: бесплатно и без интернета, но нужна видеокарта/память. 14B заметно лучше 7B по-русски."),
    Spec("pollinations", "Pollinations · без ключа", "openai", base_url="https://text.pollinations.ai",
         chat_url="https://text.pollinations.ai/openai", default_model="openai-fast",
         suggested=("openai-fast",),
         # the anonymous tier caps answers at ~1500 tokens: a reasoning model must think briefly
         extra_body={"reasoning_effort": "low"},
         note="Работает без регистрации и ключа. Анонимно — не чаще 1 ответа в 30 секунд с одного IP и только "
              "небольшая модель GPT-OSS 20B: для лучшего русского возьмите Groq, Mistral или OpenRouter."),
    Spec("github", "GitHub Models", "openai", base_url="https://models.github.ai/inference",
         key_env="GITHUB_TOKEN", key_page="https://github.com/settings/personal-access-tokens/new",
         default_model="openai/gpt-4o",
         suggested=("openai/gpt-4o", "meta/Llama-3.3-70B-Instruct", "openai/gpt-4o-mini", "deepseek/DeepSeek-V3-0324"),
         note="Бесплатно с аккаунтом GitHub: нужен токен с правом «Models: read» (fine-grained token)."),
    Spec("groq", "Groq", "openai", base_url="https://api.groq.com/openai/v1",
         key_env="GROQ_API_KEY", key_page="https://console.groq.com/keys",
         default_model="llama-3.3-70b-versatile",
         suggested=("llama-3.3-70b-versatile", "deepseek-r1-distill-llama-70b"),
         note="Очень быстрые большие модели, бесплатный тариф с лимитами в минуту."),
    Spec("openrouter", "OpenRouter", "openai", base_url="https://openrouter.ai/api/v1",
         key_env="OPENROUTER_API_KEY", key_page="https://openrouter.ai/settings/keys",
         default_model="deepseek/deepseek-chat:free",
         suggested=("deepseek/deepseek-chat:free", "deepseek/deepseek-r1:free", "meta-llama/llama-3.3-70b-instruct:free"),
         note="Модели с пометкой «:free» бесплатны (с дневным лимитом). Список бесплатных часто меняется.",
         extra_headers={"HTTP-Referer": "https://github.com/chess-dialogue-maker", "X-Title": "Chess Dialogue Maker"}),
    Spec("mistral", "Mistral AI", "openai", base_url="https://api.mistral.ai/v1",
         key_env="MISTRAL_API_KEY", key_page="https://console.mistral.ai/api-keys",
         default_model="mistral-large-latest",
         suggested=("mistral-large-latest", "mistral-medium-latest", "mistral-small-latest"),
         note="Бесплатный тариф «Experiment»; хорошо пишет по-русски."),
    Spec("gemini", "Gemini", "gemini", key_env="GEMINI_API_KEY", key_page="https://aistudio.google.com/apikey",
         default_model=config.GEMINI_MODEL, suggested=(config.GEMINI_MODEL,),
         note="Google AI Studio, бесплатный тариф с лимитами."),
)}
PROVIDER_IDS = tuple(SPECS)


def spec(provider: str) -> Spec:
    try:
        return SPECS[provider.lower()]
    except KeyError:
        raise LLMError(f"Неизвестная нейросеть «{provider}».", f"Доступны: {', '.join(PROVIDER_IDS)}.") from None


def default_model(provider: str) -> str:
    s = spec(provider)
    if s.id == config.LLM_PROVIDER and config.LLM_MODEL:  # the last model chosen for this provider
        return config.LLM_MODEL
    if s.id == "openrouter":  # the free line-up changes often: take the biggest free model available now
        live = _live_models(s)
        if live and s.default_model not in live:
            return live[0]
    return s.default_model


# ================================================================ keys
def get_key(provider: str) -> str:
    """The provider's key from the environment / .env ("" if not set)."""
    s = spec(provider)
    return os.environ.get(s.key_env, "").strip() if s.key_env else ""


def key_status(provider: str) -> str:
    s = spec(provider)
    if not s.needs_key:
        return "🔓 Ключ не нужен"
    key = get_key(provider)
    return f"🔑 Ключ задан ({mask(key)})" if key else f"🔒 Нужен ключ {s.key_env}"


def mask(key: str) -> str:
    return f"{key[:4]}…{key[-4:]}" if len(key) > 12 else "…"


def validate_key(provider: str, key: str) -> None:
    """Raises LLMError if the provider rejects the key."""
    s = spec(provider)
    key = key.strip()
    if not key or re.search(r"\s", key):
        raise LLMError("Ключ пустой или содержит пробелы.", "Скопируйте ключ целиком, без пробелов и переносов.")
    try:
        if s.id == "gemini":
            from google import genai

            client = genai.Client(api_key=key)
            next(iter(client.models.list()))
            return
        if s.id == "openrouter":
            r = requests.get(f"{s.base_url}/key", headers={"Authorization": f"Bearer {key}"}, timeout=20)
        elif s.id == "github":
            r = requests.post(s.chat_endpoint(), headers=_auth(key), timeout=30,
                              json={"model": "openai/gpt-4o-mini", "messages": [{"role": "user", "content": "ok"}],
                                    "max_tokens": 1})
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
    log.info("✓ Ключ %s проверен и сохранён в .env (%s)", s.label, mask(key))
    return f"✅ Ключ {s.label} проверен и сохранён в .env ({mask(key)})"


def _auth(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"} if key else {}


# ================================================================ model lists
def list_models(provider: str) -> list[tuple[str, str]]:
    """(label, model) pairs for the UI: live list where the provider offers one,
    otherwise the suggested models. Ollama marks models that are not downloaded yet."""
    s = spec(provider)
    if s.kind == "ollama":
        installed = OllamaProvider.installed()
        extra = [m for m in s.suggested if m not in installed and f"{m}:latest" not in installed]
        return [(m, m) for m in installed] + [(f"{m}  · не скачана", m) for m in extra]
    if s.kind == "gemini":
        if not get_key("gemini"):
            return [(m, m) for m in s.suggested]
        return [(m, m) for m in GeminiProvider.list_models()]
    live = _live_models(s)
    names = live or list(s.suggested)
    return [(m, m) for m in names]


def _live_models(s: Spec) -> list[str]:
    try:
        if s.id == "pollinations":
            r = requests.get(f"{s.base_url}/models", timeout=15)
            r.raise_for_status()
            return [m["name"] for m in r.json() if m.get("tier") in (None, "anonymous")]
        if s.id == "openrouter":
            r = requests.get(f"{s.base_url}/models", timeout=20)
            r.raise_for_status()
            unfit = ("safety", "guard", "code", "-vl", "omni", "embed")  # moderation / coding / vision models
            free = [m["id"] for m in r.json()["data"]
                    if m["id"].endswith(":free") and not any(u in m["id"] for u in unfit)]
            big_first = sorted(free, key=lambda m: -_size_hint(m))
            return big_first
        if s.id == "github":
            return []  # the catalog needs a token-specific API; the suggested list covers the useful models
        key = get_key(s.id)
        if not key:
            return []
        r = requests.get(f"{s.base_url}/models", headers=_auth(key), timeout=20)
        r.raise_for_status()
        ids = [m["id"] for m in r.json().get("data", [])]
        skip = ("whisper", "tts", "embed", "guard", "moderation", "ocr", "audio", "vision", "image", "playai")
        ids = sorted(i for i in ids if not any(k in i.lower() for k in skip))
        return [m for m in s.suggested if m in ids] + [m for m in ids if m not in s.suggested]
    except Exception as e:
        log.debug("model list of %s unavailable: %s", s.id, e)
        return []


def _size_hint(model: str) -> float:
    """Parameter count in billions guessed from the name (for sorting), 0 if unknown."""
    m = re.findall(r"(\d+(?:\.\d+)?)b\b", model.lower())
    return max(map(float, m)) if m else 0.0


# ================================================================ providers
def get_provider(provider: str | None = None, model: str | None = None):
    s = spec(provider or config.LLM_PROVIDER)
    model = model or default_model(s.id)
    if s.kind == "ollama":
        return OllamaProvider(model=model)
    if s.kind == "gemini":
        return GeminiProvider(model=model)
    if s.needs_key and not get_key(s.id):
        raise LLMError(f"Для {s.label} нужен ключ {s.key_env}.",
                       f"Получите его на {s.key_page} и вставьте в поле ключа под выбором нейросети (или в .env), либо выберите Pollinations — он без ключа.")
    return OpenAICompatProvider(s, model)


def strip_reasoning(text: str) -> str:
    """Reasoning models (DeepSeek R1, …) may prepend <think>…</think>."""
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


def _retry_after(r: requests.Response, fallback: float) -> float:
    if r.headers.get("retry-after", "").replace(".", "", 1).isdigit():
        return min(90.0, float(r.headers["retry-after"]) + 1)
    m = re.search(r"[Rr]etry after (\d+)", r.text)
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
    except ValueError:
        return r.text[:200]


class OllamaProvider:
    def __init__(self, url: str = config.OLLAMA_URL, model: str | None = None):
        self.url, self.model = url.rstrip("/"), model or config.OLLAMA_MODEL
        self.label = f"Ollama · {self.model}"

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
        log.info("✓ Модель %s скачана", model)

    def chat(self, messages: list[dict], schema: dict | None = None) -> str:
        for wait in (*TRANSIENT_WAITS, None):
            try:
                r = requests.post(
                    f"{self.url}/api/chat",
                    json={
                        "model": self.model,
                        "messages": messages,
                        "stream": False,
                        "format": schema or "json",  # structured output (Ollama ≥ 0.5)
                        "options": {"temperature": config.LLM_TEMPERATURE, "num_ctx": 8192},
                    },
                    timeout=config.LLM_TIMEOUT,
                )
            except requests.ConnectionError as e:
                raise LLMError("Ollama не запущена.", "Запустите `ollama serve` (или приложение Ollama) — либо выберите другую нейросеть.") from e
            except requests.Timeout as e:
                raise LLMError(f"Ollama не успела ответить за {config.LLM_TIMEOUT} с.",
                               "Модель слишком тяжёлая для этого компьютера: выберите поменьше или увеличьте LLM_TIMEOUT.") from e
            if r.status_code == 404:
                raise LLMError(f"В Ollama нет модели {self.model}.",
                               f"Нажмите «⬇ Скачать модель» или выполните `ollama pull {self.model}`.")
            if r.status_code >= 500 and wait is not None:  # model still loading / out of memory hiccup
                log.warning("Ollama ответила ошибкой %d, повтор через %d с…", r.status_code, wait)
                time.sleep(wait)
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

        self.client, self.model = genai.Client(api_key=api_key), (model or config.GEMINI_MODEL).removeprefix("models/")
        self.label = f"Gemini · {self.model}"

    @staticmethod
    def list_models(api_key: str | None = None) -> list[str]:
        from google import genai

        names = []
        client = genai.Client(api_key=api_key or get_key("gemini"))  # keep a reference: the pager is lazy
        for m in client.models.list():
            name = m.name.removeprefix("models/")
            if "generateContent" in (m.supported_actions or []) and not any(h in name for h in GEMINI_HIDDEN):
                names.append(name)
        return names

    def chat(self, messages: list[dict], schema: dict | None = None) -> str:
        from google.genai import errors as genai_errors
        from google.genai import types

        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        contents = [
            types.Content(role="model" if m["role"] == "assistant" else "user", parts=[types.Part(text=m["content"])])
            for m in messages
            if m["role"] != "system"
        ]
        for wait in (*TRANSIENT_WAITS, None):
            try:
                resp = self.client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=system,
                        temperature=config.LLM_TEMPERATURE,
                        response_mime_type="application/json",
                    ),
                )
                return strip_reasoning(resp.text or "")
            except (genai_errors.ServerError, genai_errors.ClientError) as e:
                code = getattr(e, "code", 0) or 0
                transient = code >= 500 or (code == 429 and "per day" not in str(e).lower())
                if not transient or wait is None:
                    raise
                what = "перегружен" if code >= 500 else "просит снизить частоту запросов"
                log.warning("Gemini %s (ошибка %d), повтор через %d с…", what, code, wait)
                time.sleep(wait)
        raise LLMError("Gemini не ответил.", "Повторите позже или выберите другую нейросеть.")


# ================================================================ alternatives after a failure
def alternatives(provider: str, model: str, kind: str = "other", limit: int = 10) -> list[tuple[str, str, str]]:
    """(label, provider, model) that can be tried right now instead of a failed one: providers
    without a key or with a key set, installed Ollama models. After a limit / key / connection
    problem the same provider is left out; after a missing model, other models of it stay."""
    out: list[tuple[str, str, str]] = []
    same_provider_ok = kind in ("model", "output", "temporary", "other")
    for s in SPECS.values():
        if s.id == provider and not same_provider_ok:
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
            ranked = sorted(models, key=lambda m: -_size_hint(m))
        else:
            ranked = [default_model(s.id)] + [m for m in s.suggested if m != default_model(s.id)]
        for m in ranked[:3]:
            if s.id == provider and m == model:
                continue
            note = "" if not s.needs_key else " · ключ задан"
            out.append((f"{s.label} — {m}{note}", s.id, m))
    return out[:limit]
