"""Human-readable explanations of errors, shared by the web UI and the terminal.

`explain(e)` turns any exception (ours, yt-dlp/ffmpeg output, Gemini/Ollama HTTP
errors, ...) into a short Russian sentence about what went wrong plus a hint on
how to fix it. Full tracebacks stay in the log at DEBUG level (`app.py -v`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Explained:
    title: str          # what went wrong, one sentence
    hint: str = ""      # what to do about it

    def __str__(self) -> str:
        return f"{self.title} {self.hint}".strip()


def _first_line(text: str, limit: int = 160) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    line = lines[-1] if lines and lines[0].endswith("failed:") else (lines[0] if lines else "")
    line = re.sub(r"^(ERROR|error):\s*", "", line)
    return line if len(line) <= limit else line[: limit - 1] + "…"


# (pattern in "TypeName: message", title, hint); first match wins
_RULES: list[tuple[str, str, str]] = [
    # ---- LLM: Gemini
    (r"GEMINI_API_KEY is empty",
     "Не задан ключ Gemini.",
     "Получите бесплатный ключ на https://aistudio.google.com/apikey и впишите GEMINI_API_KEY=… в .env, или выберите Ollama."),
    (r"(API_KEY_INVALID|API key not valid|PERMISSION_DENIED|401 UNAUTHENTICATED)",
     "Gemini отклонил ключ API.",
     "Проверьте GEMINI_API_KEY в .env (ключ мог быть отозван) или выберите Ollama."),
    (r"(free_tier\S*,? limit: 0|limit: 0\b|BILLING_DISABLED|billing to be enabled|requires (an )?active billing)",
     "Эта модель Gemini не входит в бесплатный тариф — нужна оплата.",
     "Включите оплату (Billing) для проекта в AI Studio — ключ останется тем же — или выберите gemini-3.8-flash / gemini-3.5-flash-lite."),
    (r"((?-i:RESOURCE_EXHAUSTED)|exceeded your current quota)",
     "Исчерпан лимит запросов Gemini (бесплатный тариф).",
     "Подождите минуту и нажмите «Продолжить выбранное», выберите другую модель Gemini (например, Flash-Lite) или Ollama."),
    (r"((?-i:UNAVAILABLE)|model is overloaded|high demand)",
     "Gemini сейчас перегружен (ошибка 503). Это временно, на стороне Google.",
     "Подождите 1–2 минуты и нажмите «Продолжить выбранное» — или выберите другую модель / Ollama."),
    (r"(\b500 INTERNAL|\b502 Bad Gateway|(?-i:DEADLINE_EXCEEDED))",
     "Сервер Gemini ответил ошибкой.",
     "Обычно это временно: нажмите «Продолжить выбранное» через минуту или выберите другую модель."),
    (r"(404 NOT_FOUND.*models/|is not found for API version|not supported for generateContent)",
     "Такой модели Gemini нет или она не умеет генерировать текст.",
     "Выберите другую модель в списке (кнопка ↻ обновляет список)."),
    (r"(Server disconnected|RemoteProtocolError|Connection reset by peer|IncompleteRead)",
     "Сервер нейросети оборвал соединение, не дослав ответ.",
     "Это временный сбой на стороне провайдера: повторите через минуту или выберите другую нейросеть."),
    # ---- LLM: Ollama
    (r"Ollama is not reachable",
     "Ollama не запущена.",
     "Запустите в терминале `ollama serve` (или приложение Ollama) — либо выберите Gemini."),
    (r"Ollama model '([^']+)' not found",
     "В Ollama нет модели \\1.",
     "Скачайте её: `ollama pull \\1` — или выберите установленную модель в списке."),
    (r"(ReadTimeout|Read timed out).*11434|timed out.*ollama",
     "Ollama не успела ответить.",
     "Модель слишком медленная для этого компьютера: выберите модель поменьше или увеличьте LLM_TIMEOUT в .env."),
    (r"LLM failed to return valid JSON",
     "Нейросеть несколько раз вернула некорректный ответ.",
     "Нажмите «Новый разбор нейросетью» ещё раз или выберите модель покрупнее (Groq, Mistral, OpenRouter или qwen2.5:14b)."),
    (r"Transcript is empty",
     "В клипе не нашлось речи.",
     "Проверьте таймкоды Start/End и что в фрагменте есть диалог."),
    # ---- download
    (r"YouTube requires sign-in|confirm you.re not a bot",
     "YouTube требует подтвердить, что вы не бот.",
     "Добавьте в .env YTDLP_COOKIES_FROM_BROWSER=firefox (браузер, где вы вошли в YouTube) и нажмите «Продолжить выбранное»."),
    (r"(Private video|Video unavailable|This video is unavailable|has been removed)",
     "Видео недоступно (удалено, приватное или заблокировано в регионе).",
     "Выберите другой ролик."),
    (r"(Sign in to confirm your age|age-restricted)",
     "Видео с возрастным ограничением.",
     "Укажите cookies вошедшего аккаунта: YTDLP_COOKIES_FROM_BROWSER=firefox в .env."),
    (r"(Unsupported URL|is not a valid URL)",
     "Ссылка не поддерживается.",
     "Вставьте ссылку на ролик (YouTube и др.) или введите поисковый запрос и нажмите «Найти»."),
    (r"HTTP Error 403",
     "Сайт запретил скачивание (HTTP 403).",
     "Обновите yt-dlp: `uv pip install -U yt-dlp` — и попробуйте снова."),
    (r"(Temporary failure in name resolution|Name or service not known|Network is unreachable|getaddrinfo failed|Max retries exceeded|ConnectionError)",
     "Нет подключения к интернету (или сайт недоступен).",
     "Проверьте сеть и повторите."),
    (r"File not found: (.+)",
     "Файл не найден: \\1.",
     "Проверьте путь или загрузите файл через 📎."),
    (r"The clip has no audio track",
     "В видео нет звуковой дорожки — нечего распознавать.",
     "Выберите ролик со звуком."),
    (r"Bad timecode: '?([^'\s]*)'?",
     "Неверный таймкод «\\1».",
     "Пишите секунды (83), ММ:СС (1:23) или ЧЧ:ММ:СС (0:01:23)."),
    (r"end_time must be greater",
     "Конец фрагмента раньше начала.",
     "Исправьте Start/End."),
    (r"Source \(URL or file path\) is required",
     "Не указан источник видео.",
     "Вставьте ссылку, загрузите файл или найдите ролик по названию."),
    (r"Ответы нейросети не прошли проверку",
     "Ответы нейросети не прошли проверку.",
     "Выберите лучший вариант для каждого хода в окне вариантов (кнопка «Разобрать диалог» или «Продолжить выбранное»)."),
    (r"No such file or directory: '[^']*transcript\.json'",
     "Речь ещё не распознана.",
     "Сводка и разбор строятся по распознанной речи: нажмите «Разобрать диалог» (или «Создать видео») — распознавание идёт первым."),
    # ---- local tools
    (r"No such file or directory: '(ffmpeg|ffprobe)'",
     "Не установлен ffmpeg.",
     "Установите его пакетным менеджером (например, `emerge media-video/ffmpeg` или `apt install ffmpeg`)."),
    (r"No font found",
     "Не найден шрифт для отрисовки.",
     "Выполните `python app.py assets`, чтобы скачать Inter."),
    (r"ffmpeg failed",
     "ffmpeg не смог собрать видео.",
     "Запустите с `-v`, чтобы увидеть подробности; часто помогает освободить место на диске."),
    (r"No moves to render",
     "В разборе нет ни одного хода.",
     "Добавьте ходы в JSON или нажмите «Новый разбор LLM»."),
    (r"No such job",
     "Задание не найдено.",
     "Выберите задание из списка прошлых."),
    (r"Некорректный JSON",
     "JSON разбора содержит ошибку.",
     "Проверьте запятые и кавычки; поля описаны в README («Формат moves.json»)."),
    # ---- TikTok
    (r"Cookies file not found",
     "Нет файла cookies для TikTok.",
     "Войдите на tiktok.com, экспортируйте cookies (расширение «Get cookies.txt LOCALLY») в cookies.txt в папке проекта."),
    (r"no 'sessionid' cookie",
     "В cookies.txt нет входа в TikTok.",
     "Экспортируйте cookies заново, будучи залогиненным на tiktok.com."),
    (r"TikTok upload failed|Upload crashed",
     "TikTok не принял загрузку.",
     "Чаще всего протухли cookies — экспортируйте их заново; если не помогло: `uv pip install -U tiktok-uploader`."),
    (r"Description is (\d+) chars",
     "Описание слишком длинное (\\1 символов, можно 2200).",
     "Сократите описание."),
]


def explain(e: BaseException) -> Explained:
    # llm.LLMError (and friends with .title) already carry their Russian explanation: pass it through unchanged
    if getattr(e, "title", None):
        return Explained(e.title, getattr(e, "hint", "") or "")
    text = f"{type(e).__name__}: {e}"
    cause = e.__cause__ or e.__context__
    if cause is not None:
        text += f"\n{type(cause).__name__}: {cause}"
    for pattern, title, hint in _RULES:
        m = re.search(pattern, text, re.I | re.S)
        if m:
            return Explained(m.expand(title), m.expand(hint))
    if isinstance(e, KeyboardInterrupt):
        return Explained("Остановлено пользователем.")
    if isinstance(e, MemoryError):
        return Explained("Не хватило памяти.", "Возьмите фрагмент покороче или модель whisper поменьше (WHISPER_MODEL=base).")
    if isinstance(e, ValueError) and re.search(r"[а-яё]", str(e), re.I):
        return Explained(_first_line(str(e)))  # our own Russian validation messages
    detail = _first_line(str(e)) or type(e).__name__
    return Explained(f"Непредвиденная ошибка: {detail}", "Подробности — в терминале (запустите с -v для полного трейсбека).")


# ================================================================ AI failures (the «нейросеть не справилась» dialog)
AI_KINDS: dict[str, tuple[str, str]] = {
    "temporary": ("Сервис временно не справился",
                  "Обычно это проходит само: повторите с той же моделью через минуту или возьмите другую."),
    "limit": ("Исчерпан лимит запросов",
              "Бесплатные лимиты обновляются через минуту–сутки. Подождите или переключитесь на другой сервис."),
    "auth": ("Нужен ключ доступа",
             "Вставьте ключ сервиса (кнопка «Получить ключ» в разделе «Нейросеть») или выберите сервис без ключа."),
    "model": ("Модель недоступна",
              "У сервиса нет такой модели или она не скачана — выберите другую из списка."),
    "offline": ("Нет связи с сервисом",
                "Проверьте интернет; для Ollama запустите `ollama serve`. Или выберите другой сервис."),
    "output": ("Модель ответила некорректно",
               "Маленькие модели иногда путаются в формате ответа — обычно помогает модель покрупнее."),
    "billing": ("Нужна оплата",
                "Модель платная или на балансе закончились деньги. Пополните баланс (Claude — console.anthropic.com → "
                "Billing; Gemini — включите оплату проекта в AI Studio, ключ тот же) или выберите бесплатный сервис: "
                "Gemini Flash, Mistral, Groq, OpenRouter «:free» или Ollama."),
    "other": ("Сбой нейросети", "Повторите запрос или выберите другую модель."),
}
_AI_PATTERNS: list[tuple[str, str]] = [
    # first: a paid model / empty balance also carries 429/403-like texts (Gemini "limit: 0", Anthropic 400)
    ("billing", r"(нужна оплата|закончились кредиты|credit balance is too low|purchase credits|BILLING_DISABLED|"
                r"billing to be enabled|requires (an )?active billing|free_tier\S*,? limit: 0|insufficient (credit|balance|funds))"),
    ("auth", r"(не принял ключ|нужен ключ|не задан ключ|API key not valid|API_KEY_INVALID|UNAUTHENTICATED|"
             r"authentication_error|invalid x-api-key|\b401\b|\b403\b)"),
    ("limit", r"(лимит|\b429\b|\b402\b|quota|RESOURCE_EXHAUSTED|rate.?limit)"),
    ("model", r"(нет модели|не скачана|404|not found for API|not supported for generateContent|model .*not found|not_found_error)"),
    ("offline", r"(не запущена|нет связи|не удалось связаться|not reachable|Name or service not known|Network is unreachable|"
                r"Temporary failure in name resolution|ConnectionError|Max retries exceeded)"),
    ("temporary", r"(перегружен|временн|\b50[0234]\b|\b529\b|overloaded|UNAVAILABLE|DEADLINE_EXCEEDED|не ответил|"
                  r"не успела|timed? ?out|оборвал|Server disconnected|Connection reset)"),
    ("output", r"(некорректн|valid JSON|непонятн|пуст(ую|ой) (ответ|сводку)|рассуждения|обрезан|LLM failed)"),
]


def ai_kind(e: BaseException) -> str:
    text = " ".join(str(x) for x in (getattr(e, "title", ""), getattr(e, "hint", ""), e, e.__cause__ or ""))
    for kind, pattern in _AI_PATTERNS:
        if re.search(pattern, text, re.I):
            return kind
    return "other"


def is_ai(e: BaseException | None) -> bool:
    """A failure of a neural-network request (as opposed to a download, ffmpeg, a bad file…)."""
    if e is None:
        return False
    if getattr(e, "ai_stage", None):
        return True
    try:
        import llm

        if isinstance(e, llm.LLMError):
            return True
    except Exception:
        pass
    return bool(re.search(r"(LLM failed|Нейросеть вернула)", str(e)))
