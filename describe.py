"""Short publication descriptions from the source title — rule-based, nothing invented.

    suggest("Диалог в ресторане. Бесславные ублюдки", kind="film").text
    -> "Разбор диалога из фильма: Бесславные ублюдки #шахматы #разбор #бесславныеублюдки"

The title is cleaned (emoji, hashtags, quality/technical tags, the channel name),
split into parts and each part is classified as the scene ("Диалог в ресторане",
"Say My Name Scene"), noise ("Rotten Tomatoes TV", "4K Edit") or a candidate for
the work (film, series, …). The kind of the work is taken from the title and the
video's own metadata (description, tags, category, a year in parentheses) and only
when the evidence clearly points one way; otherwise the text stays neutral
("Разбор диалога: …"). A kind chosen by the user always wins.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

BASE_TAGS = ("шахматы", "разбор")
MAX_TAGS = 6
# the uploader's own hashtags are kept, except those about the platform, reach or format
GENERIC_TAGS = {
    "shorts", "short", "youtubeshorts", "ytshorts", "shortsvideo", "shortvideo", "viral", "viralvideo", "fyp", "foryou",
    "foryoupage", "fy", "trending", "trend", "tiktok", "reels", "reel", "edit", "edits", "movie", "movies", "film",
    "films", "moviescene", "moviescenes", "movieclips", "scene", "scenes", "clip", "clips", "series", "tvshow",
    "subscribe", "like", "рек", "реки", "рекомендации", "врек", "втоп", "хочуврек", "тренды", "тренд", "топ", "кино",
    "фильм", "фильмы", "сериал", "сериалы", "моменты", "момент", "сцена", "нарезка", "эдит", "шортс",
}

# kind id -> (label in the UI, phrase used in the description)
KINDS: dict[str, tuple[str, str]] = {
    "film": ("Фильм", "Разбор диалога из фильма"),
    "series": ("Сериал", "Разбор диалога из сериала"),
    "cartoon": ("Мультфильм", "Разбор диалога из мультфильма"),
    "anime": ("Аниме", "Разбор диалога из аниме"),
    "game": ("Игра", "Разбор диалога из игры"),
    "show": ("Шоу / передача", "Разбор диалога из шоу"),
    "interview": ("Интервью", "Разбор интервью"),
    "debate": ("Дебаты", "Разбор дебатов"),
    "podcast": ("Подкаст", "Разбор подкаста"),
    "stream": ("Стрим", "Разбор стрима"),
    "other": ("Другое", "Разбор диалога"),
}
KIND_CHOICES = [("Определить автоматически", "")] + [(label, k) for k, (label, _) in KINDS.items()]

_KIND_PATTERNS = {
    "series": [r"\bs\d{1,2}\s?e\d{1,3}\b", r"\bсезон\w*", r"\bсери[яиюей]\b", r"\bseason\b", r"\bepisode\b",
               r"\bсериал\w*", r"\b(tv )?series\b"],
    "film": [r"\bфильм\w*", r"\bкино\b", r"\bкинофильм\w*", r"\bmovies?\b", r"\bfilm\b"],
    "cartoon": [r"\bмультфильм\w*", r"\bмульт(ик|сериал)\w*", r"\bcartoons?\b", r"\banimated\b"],
    "anime": [r"\bаниме\b", r"\banime\b"],
    "game": [r"\bгеймплей\b", r"\bgameplay\b", r"\bкатсцен\w*", r"\bcutscenes?\b", r"\bvideo ?games?\b", r"\bиз игры\b"],
    "show": [r"\bшоу\b", r"\bпередач\w*", r"\btalk ?show\b"],
    "interview": [r"\bинтервью\b", r"\binterview\w*"],
    "debate": [r"\bдебат\w*", r"\bdebates?\b"],
    "podcast": [r"\bподкаст\w*", r"\bpodcasts?\b"],
    "stream": [r"\bстрим\w*", r"\blive ?stream\b", r"\bтрансляци\w*"],
}
_EVENT_KINDS = {"interview", "debate", "podcast", "stream"}
# words that make a title part a scene description rather than the name of the work
_SCENE_WORDS = r"(диалог\w*|разговор\w*|сцен[аыеу]\w*|допрос\w*|спор\b|беседа\w*|встреча\w*|конфликт\w*|монолог\w*|" \
               r"отрыв\w*|фрагмент\w*|момент\w*|эпизод\w*|scene|clip|dialogue|conversation|interrogation|moment|monologue)"
_GENERIC_SCENE = re.compile(rf"^\s*{_SCENE_WORDS}\b", re.I)
_SCENE_ANY = re.compile(rf"\b{_SCENE_WORDS}\b", re.I)
_NOISE = re.compile(
    r"\b(official( (video|clip|trailer))?|trailer|teaser|full ?hd|uhd|hdr|\d{3,4}p|\d0 ?fps|4k|8k|hd|"
    r"edit|edits|editz+|slowed|reverb|sped ?up|remix|amv|fmv|untitled ?\d*|shorts?|reels?|tiktok|"
    r"movieclips|rotten tomatoes( tv)?|netflix|hbo( max)?|amazon prime|prime video|disney\+?|кинопоиск|"
    r"ivi|okko|кион|premier|start|амедиатека|rus|eng|русская озвучка|озвучка|дубляж|перевод|субтитры|"
    r"полный фильм|смотреть онлайн|в хорошем качестве)\b", re.I)
_EPISODE = re.compile(r"\(?\b(s\d{1,2}\s?e\d{1,3}|season \d+( episode \d+)?|episode \d+|\d+ сезон( \d+ сери[яи])?|"
                      r"сезон \d+( сери[яи] \d+)?|\d+ сери[яи])\b\)?", re.I)
_YEAR = re.compile(r"\(\s*((?:19|20)\d{2})(?:\s*[-–—]\s*((?:19|20)\d{2})?)?\s*\)")
_JUNK_FILE = re.compile(r"^(img|vid|video|mov|clip|dsc|pxl|screen ?recording|запись экрана|whatsapp video|"
                        r"photo|movie|output|final|untitled)?[\s_-]*\d{2,}([\s_-]\d+)*$", re.I)
_SEPARATORS = re.compile(r"\s+[|•·/]\s+|\s+[-–—]\s+|\s*\|\s*|(?<=[^\d\s])\.\s+(?=\S)|:\s+")


@dataclass
class Suggestion:
    text: str                       # the whole description, hashtags included
    body: str
    tags: list[str]
    work: str = ""
    scene: str = ""
    kind: str = ""                  # final kind ("" = unknown)
    kind_source: str = ""           # "user" | "title" | "meta" | ""
    notes: list[str] = field(default_factory=list)


# ================================================================ cleaning
def strip_emoji(text: str) -> str:
    out = []
    for ch in text:
        cp = ord(ch)
        if cp >= 0x1F000 or 0x2600 <= cp <= 0x27BF or 0x2B00 <= cp <= 0x2BFF or 0xFE00 <= cp <= 0xFE0F or cp == 0x200D:
            continue
        out.append(ch)
    return "".join(out)


def _fix_case(text: str) -> str:
    letters = [c for c in text if c.isalpha()]
    if len(letters) >= 4 and all(c.isupper() for c in letters):
        if all("a" <= c.lower() <= "z" for c in letters):
            return " ".join(w[:1].upper() + w[1:].lower() for w in text.split())
        return text[:1].upper() + text[1:].lower()
    return text


def hashtags_in(text: str) -> list[str]:
    return [t.lower() for t in re.findall(r"#([\w\d]+)", text or "")]


_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "`": "'"})   # "Walter’s" must not become "Walter s"


def clean_title(title: str, channel: str = "") -> str:
    t = strip_emoji(title or "").translate(_APOSTROPHES)
    t = re.sub(r"#[\w\d]+", " ", t)                       # hashtags (kept separately as evidence)
    t = re.sub(r"[\[\{](.*?)[\]\}]", lambda m: " " if _NOISE.search(m.group(1)) or len(m.group(1)) < 12 else f" {m.group(1)} ", t)
    t = re.sub(r"\((?:[^()]*?)\)", lambda m: m.group(0) if _YEAR.fullmatch(m.group(0)) or _EPISODE.fullmatch(m.group(0))
               else " ", t)
    if channel:
        t = re.sub(re.escape(channel), " ", t, flags=re.I)
    t = t.replace("''", '"').replace("``", '"')
    t = re.sub(r"[^\w\s.,!?:;'\"«»„“”()\-–—|/&+]", " ", t)  # stray symbols
    t = re.sub(r"\s+", " ", t).strip(" .,-–—|/:;")
    return t


def _norm(s: str) -> str:
    return re.sub(r"[\s\"'«»„“”]+", " ", s).strip().lower()


# ================================================================ kind evidence
def kind_scores(title: str, meta: dict | None = None) -> dict[str, float]:
    meta = meta or {}
    scores: dict[str, float] = {}

    def add(kind: str, w: float) -> None:
        scores[kind] = scores.get(kind, 0.0) + w

    title_l = (title or "").lower()
    tags_in_title = " ".join(hashtags_in(title))
    desc = (meta.get("description") or "").lower()
    tags = [str(t).lower() for t in meta.get("tags") or []]
    for kind, pats in _KIND_PATTERNS.items():
        for p in pats:
            if re.search(p, title_l) or re.search(p, tags_in_title):
                add(kind, 3)
                break
        for p in pats:
            if re.search(p, desc):
                add(kind, 2)
                break
        hits = sum(1 for t in tags if any(re.search(p, t) for p in pats))
        if hits:
            add(kind, min(3, hits))
    year = _YEAR.search(title or "") or _YEAR.search(meta.get("description") or "")
    if year:
        # a single year next to a title is how films are written; a range belongs to a series
        add("series" if year.group(2) or re.search(r"\(\s*(19|20)\d{2}\s*[-–—]\s*\)", year.group(0)) else "film", 3)
    cats = " ".join(meta.get("categories") or []).lower()
    if "gaming" in cats:
        add("game", 3)
    if "film" in cats:
        add("film", 0.5)
        add("series", 0.5)
    return scores


def detect_kind(title: str, meta: dict | None = None) -> tuple[str, str]:
    """(kind, source) when the evidence clearly points one way, else ("", "")."""
    scores = kind_scores(title, meta)
    if not scores:
        return "", ""
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    best, top = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    if top >= 3 and top - second >= 2:
        title_only = kind_scores(title, None).get(best, 0) >= 3
        return best, "title" if title_only else "meta"
    return "", ""


# ================================================================ parsing
@dataclass
class Parsed:
    work: str = ""
    scene: str = ""
    scene_quoted: bool = False      # the scene is a quote/name worth showing
    kind_hint: str = ""             # a kind written next to the work ("фильм X", "сериал X")
    event_phrase: str = ""          # "Интервью Дудя с Навальным" style titles


def _kind_prefix(part: str) -> tuple[str, str, str] | None:
    """'(сцена) из фильма X' -> (kind, X, text before it)."""
    m = re.search(r"(?:^|\s)(?:из\s+)?(фильм[аеу]?|кинофильм[аеу]?|сериал[аеу]?|мультфильм[аеу]?|мультсериал[аеу]?|аниме|игр[аыи])\s+(.+)$",
                  part.strip(), re.I)
    if not m:
        return None
    word = m.group(1).lower()
    kind = ("series" if word.startswith(("сериал", "мультсериал")) and not word.startswith("мульт")
            else "cartoon" if word.startswith("мульт") else "anime" if word == "аниме"
            else "game" if word.startswith("игр") else "film")
    before = part.strip()[: m.start()].strip(" ,.-–—")
    before = re.sub(r"\s+из$", "", before, flags=re.I)
    return kind, m.group(2).strip(" «»\"'"), before


def parse_title(title: str, channel: str = "", meta: dict | None = None) -> Parsed:
    meta = meta or {}
    clean = clean_title(title, channel)
    res = Parsed()
    if not clean or _JUNK_FILE.match(clean):
        return res
    quoted = re.findall(r"[«“\"]([^«»“”\"]{2,80})[»”\"]", clean)
    # an episode marker separates the series name from the rest: "Breaking Bad S5E7 Say My Name"
    em = _EPISODE.search(clean)
    episode_work = clean[: em.start()].strip(" .,-–—|/:;(") if em and em.start() > 0 else ""
    if len(episode_work.split()) > 5 or _SEPARATORS.search(episode_work or " | "):
        episode_work = ""
    clean = _EPISODE.sub(" | ", clean)
    parts = [p.strip(" .,-–—|/:;") for p in _SEPARATORS.split(clean)]
    parts = [p for p in parts if p]
    seps = _SEPARATORS.findall(clean)
    candidates: list[tuple[int, str]] = []
    for i, raw in enumerate(parts):
        part = _EPISODE.sub(" ", raw)
        part = _YEAR.sub(" ", part)
        part = re.sub(r"\s+", " ", part).strip(" .,-–—|/:;()")
        if not part:
            continue
        if channel and _norm(part) == _norm(channel):
            continue
        noise_left = _NOISE.sub(" ", part).strip(" .,-–—")
        if not re.search(r"\w{2,}", noise_left):
            continue                                     # the whole part is noise ("4K Edit", "Netflix")
        kp = _kind_prefix(part)
        if kp:
            res.kind_hint, res.work, before = kp
            if before and not res.scene:
                res.scene = before
            continue
        event = next((k for k in _EVENT_KINDS if any(re.match(p.replace(r"\b", "", 1), part.lower()) for p in _KIND_PATTERNS[k][:1])), "")
        if event and not res.event_phrase:
            res.event_phrase, res.kind_hint = part, event
            continue
        if _GENERIC_SCENE.match(part):
            if not res.scene:
                res.scene = part
            continue
        named = re.match(rf"^(.+?)\s+{_SCENE_WORDS}\s*$", part, re.I)
        scene_name = named.group(1).strip(" .,-–—:\"'«»“”") if named else ""
        if scene_name[:1].isupper():                    # "Say My Name Scene" -> scene name "Say My Name"
            if not res.scene:
                res.scene, res.scene_quoted = scene_name, True
            continue
        if _SCENE_ANY.search(part):                      # "финальный монолог": a description, not a name
            if not res.scene:
                res.scene = part
            continue
        candidates.append((i, noise_left if len(noise_left) >= 2 else part))
    if not res.work and candidates:
        res.work = _pick_work(candidates, parts, seps, quoted, meta, episode_work)
        rest = [c for _, c in candidates if c != res.work]
        if rest and not res.scene:
            first = rest[0]
            res.scene = first
            res.scene_quoted = any(_norm(q) == _norm(first) for q in quoted)
    tags_l = [str(t).lower() for t in meta.get("tags") or []]
    if not res.work and res.scene_quoted and res.scene.lower() in tags_l:
        res.work, res.scene, res.scene_quoted = res.scene, "", False
    for q in quoted:                                    # a quote inside the work part is the scene name
        if res.work and _norm(q) != _norm(res.work) and _norm(q) in _norm(clean) and not res.scene:
            res.scene, res.scene_quoted = q, True
    res.work = _fix_case(res.work.strip(" «»\"'"))
    res.scene = _fix_case(res.scene.strip(" «»\"'"))
    if _norm(res.scene) == _norm(res.work):
        res.scene = ""
    return res


def _pick_work(cands: list[tuple[int, str]], parts: list[str], seps: list[str], quoted: list[str], meta: dict,
               episode_work: str = "") -> str:
    if len(cands) == 1:
        idx, text = cands[0]
        words = len(text.split())
        if words > 6 or re.search(r"[!?]|,\s", text):   # a sentence: it's a line from the scene, not a title
            return ""
        return text
    tags = " | ".join(str(t).lower() for t in meta.get("tags") or [])
    desc = (meta.get("description") or "").lower()
    best, best_score = "", -1e9
    for idx, text in cands:
        words = len(text.split())
        score = -0.4 * words
        if re.search(r"[!?]|,\s", text):
            score -= 1.5
        if any(_norm(q) == _norm(text) for q in quoted):
            score += 0.5
        before = seps[idx - 1].strip() if 0 < idx <= len(seps) else ""
        after = seps[idx].strip() if idx < len(seps) else ""
        if before == "|" or before == "." or before == "":
            score += 0.6 if before in ("|", ".") else 0.0
        if idx == 0 and after in ("-", "–", "—"):
            score += 0.6                                  # "Show - Scene" (clip channels)
        if after == ":":
            score += 0.8                                  # "Work: subtitle / scene"
        low = text.lower()
        if episode_work and _norm(text) == _norm(episode_work):
            score += 3
        if low and low in tags:
            score += 2
        if low and low in desc:
            score += 1
        if score > best_score:
            best, best_score = text, score
    return best


# ================================================================ assembly
def hashtag(text: str) -> str:
    tag = re.sub(r"[^\w]+", "", (text or "").lower().replace("ё", "е"), flags=re.U).replace("_", "")
    return tag if 2 <= len(tag) <= 40 and re.search(r"[^\W\d_]", tag) else ""


def _work_tags(work: str) -> list[str]:
    people = re.split(r"\s+(?:vs\.?|против|и)\s+", work, flags=re.I)
    if len(people) > 1:
        return [t for t in (hashtag(p) for p in people[:2]) if t]
    t = hashtag(work)
    return [t] if t and len(work.split()) <= 4 and len(t) <= 24 else []   # a long glued phrase is no tag


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text and not text[:2].isupper() else text


def suggest(title: str, kind: str = "", channel: str = "", meta: dict | None = None,
            names: dict[str, str] | None = None, work: str = "") -> Suggestion:
    """Description for a source title. `kind` = the user's choice ("" = detect);
    `work` = the name of the work if known for sure (it must occur in the title)."""
    meta = meta or {}
    parsed = parse_title(title, channel or meta.get("channel", ""), meta)
    if work and _norm(work) in _norm(f"{title} {meta.get('description', '')}"):
        if _norm(parsed.scene) == _norm(work):
            parsed.scene = ""
        parsed.work = _fix_case(work.strip(" «»\"'"))
    notes: list[str] = []
    final_kind, kind_source = "", ""
    if kind in KINDS:
        final_kind, kind_source = kind, "user"
    elif parsed.kind_hint:
        final_kind, kind_source = parsed.kind_hint, "title"
    else:
        final_kind, kind_source = detect_kind(title, meta)
    work, scene = parsed.work, parsed.scene
    tags = list(BASE_TAGS)
    if parsed.event_phrase and not work:
        body = f"Шахматный разбор: {_lower_first(parsed.event_phrase)}"
        rest = re.sub(rf"^{re.escape(parsed.event_phrase.split()[0])}\s+", "", parsed.event_phrase)
        if re.search(r"\s(vs\.?|против)\s", rest, re.I):
            tags += _work_tags(rest)
    elif work:
        lead = KINDS[final_kind][1] if final_kind else "Разбор диалога"
        body = f"{lead}: {work}"
        if scene and parsed.scene_quoted:
            body += f" — «{scene}»"
        tags += _work_tags(work)
        if not final_kind:
            notes.append("тип первоисточника не определён — выберите его, если нужно «из фильма/сериала»")
    elif scene:
        lead = KINDS[final_kind][1] if final_kind else "Разбор диалога"
        if _GENERIC_SCENE.match(scene):
            body = f"Шахматный разбор: {_lower_first(scene)}" if not final_kind else f"{lead}: {_lower_first(scene)}"
        else:
            body = f"{lead}: «{scene}»"
        notes.append("в названии не найдено произведение")
    else:
        names = names or {}
        w, b = (names.get("white") or "").strip(), (names.get("black") or "").strip()
        if w and b and (w, b) != ("Белые", "Чёрные"):
            body = f"Разбор диалога: {w} vs {b}"
        else:
            body = "Шахматный разбор диалога"
        notes.append("нет названия — описание собрано без него")
    own = [t for t in (hashtag(x) for x in hashtags_in(title)) if t and t not in GENERIC_TAGS]
    tags[len(BASE_TAGS):len(BASE_TAGS)] = own[:3]          # the uploader's topic tags come right after ours
    seen: list[str] = []
    for t in tags:
        if t and t not in seen and len(seen) < MAX_TAGS:
            seen.append(t)
    body = re.sub(r"\s+", " ", body).strip()
    text = f"{body} " + " ".join(f"#{t}" for t in seen)
    return Suggestion(text.strip(), body, seen, work, scene, final_kind, kind_source, notes)


# ================================================================ optional LLM refinement
def refine_with_llm(title: str, meta: dict | None, provider) -> tuple[str, str]:
    """Ask the LLM which work and kind the title refers to. Anything that is not
    literally in the title/description (work) or not a known kind is discarded."""
    import analyzer

    meta = meta or {}
    kinds = ", ".join(k for k in KINDS if k != "other")
    prompt = (
        "По названию видео определи, из какого произведения этот фрагмент и что это за произведение.\n"
        f"Название: {title}\n"
        + (f"Описание видео (начало): {meta.get('description', '')[:400]}\n" if meta.get("description") else "")
        + "Ответь JSON: {\"work\": \"название произведения ТОЧНО как в названии видео или пустая строка\", "
        f"\"kind\": \"одно из: {kinds}, unknown\", \"confidence\": число 0..1}}. "
        "Не выдумывай: если не уверен — kind = unknown."
    )
    reply = provider.chat([{"role": "user", "content": prompt}], None)
    data = analyzer.extract_json(reply)
    work = str(data.get("work") or "").strip(" «»\"'")
    kind = str(data.get("kind") or "").strip().lower()
    conf = float(data.get("confidence") or 0)
    hay = _norm(f"{title} {meta.get('description', '')}")
    if work and _norm(work) not in hay:
        log.info("LLM назвала «%s», но этого нет в названии — игнорирую", work)
        work = ""
    if kind not in KINDS or conf < 0.6:
        kind = ""
    return work, kind
