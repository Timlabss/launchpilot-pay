"""
Launch Pilot (@PHlaunchpilot_bot) — MVP v0.1.5
==============================================
v0.1.5: собственный поллинг с «watchdog» — бот сам следит за своими «ушами»:
         если песочница «заснула» и Telegram перестал отвечать (wall-clock > 2.5 мин),
         бот сам сбрасывает сессию и переподключается. Оффсеты — в state_offset.txt.
Что умеет:
  /start        — приветствие «за ручку»
  ссылка        — бесплатная диагностика продукта (главный крючок)
  3 вопроса     — режим, если страницу не открыть (защита)
  кнопки        — «Что такое Product Hunt», «Чек-лист», «Подготовка $49»

Слои данных (принцип «неубиваемость = избыточность»):
  PH-ссылка:  0) официальный API PH (GraphQL, токен приложения из .env) — мгновенно
               1) открытый поиск PH (Algolia, публичные ключи PH) — мгновенно
               2) веб-архив (свежая копия через Save Page Now, 2-4 мин)
  любая ссылка: 1) прямой httpx  2) curl_cffi (маскировка chrome)
                3) allorigins (бесплатный публичный прокси)
                4) r.jina.ai (открытый reader)  5) (только PH) Wayback-архив
  бонус:  https://www.producthunt.com/feed — Atom-фид запусков дня (задел под «радар»)
  если всё не вышло — режим 3 вопросов, честная оценка по ответам.
  текст:  пока правила (без ИИ) -> дальше: Groq -> Gemini -> локальная -> шаблон

ВАЖНО: один и тот же бот может поллить только один экземпляр.
       Перед запуском: pkill -f 'python3.*bot\\.py'

Запуск:  python3 -u bot.py
"""

import asyncio
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx
from bs4 import BeautifulSoup
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

# ---------- настройки ----------

def _load_env() -> None:
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


_load_env()
BOT_TOKEN = os.environ["BOT_TOKEN"]
SUPPORT_CHANNEL = "https://t.me/ProductHuntBoosting"  # канал-витрина

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

router = Router()

# ---------- «глаза»: живые слои ----------
# Слой 0 (для PH): собственная открытая витрина-поиск Product Hunt (Algolia) —
# ключи опубликованы PH официально в вики producthunt-api. Живые очки/комменты.
ALGOLIA_URL = "https://0h4smabbsg-dsn.algolia.net/1/indexes/Post_production"
ALGOLIA_HEADERS = {
    "X-Algolia-API-Key": "9670d2d619b9d07859448d7628eea5f3",
    "X-Algolia-Application-Id": "0H4SMABBSG",
    "User-Agent": "Mozilla/5.0",
}

# Слой «нулевой» (топ для PH): официальный GraphQL API Product Hunt.
# Приложение «Launch Pilot» создано в дашборде PH; ключи в .env:
# PH_API_KEY + PH_API_SECRET (client_credentials) и PH_TOKEN (developer token).
PH_GQL_URL = "https://api.producthunt.com/v2/api/graphql"
PH_TOKEN_URL = "https://api.producthunt.com/v2/oauth/token"
PH_API_KEY = os.getenv("PH_API_KEY", "")
PH_API_SECRET = os.getenv("PH_API_SECRET", "")

_ph_client_tok = {"token": None, "ts": 0.0}
PH_POST_SELECTION = (
    "name tagline description votesCount commentsCount featuredAt scheduledAt "
    "createdAt url website thumbnail { url type } media { url type videoUrl } "
    "topics { nodes { name } }"
)


async def algolia_lookup(slug: str) -> dict | None:
    """Точный поиск продукта в открытом поиске PH (живые данные)."""
    try:
        async with httpx.AsyncClient(timeout=20, headers=ALGOLIA_HEADERS) as client:
            r = await client.get(ALGOLIA_URL, params={"query": slug, "hitsPerPage": 60})
            if r.status_code != 200:
                return None
            hits = r.json().get("hits", [])
        sl = slug.lower()
        for h in hits:
            if h.get("slug", "").lower() == sl:
                return h
        want_name = slug.replace("-", " ")
        for h in hits:
            if h.get("name", "").lower() in (want_name, slug):
                return h
    except Exception:
        pass
    return None


def analyze_ph_live(hit: dict) -> str:
    """Диагностика по живым данным открытого поиска PH."""
    name = hit.get("name", "продукт")
    tagline = hit.get("tagline", "")
    votes = hit.get("vote_count", 0) or 0
    comments = hit.get("comments_count", 0) or 0
    featured = bool(hit.get("is_featured"))
    media = [m for m in (hit.get("media") or []) if isinstance(m, dict)]
    has_video = any(
        "video" in str(m.get("type", "")).lower() or "youtube" in str(m.get("url", "")).lower()
        for m in media
    )
    topics = [t.get("name") for t in (hit.get("topics") or []) if isinstance(t, dict)][:5]

    score = 15
    lines = [f"🔍 Посмотрел {name} — живые данные из открытого поиска PH"]

    if featured:
        score += 5
        lines.append("✅ Продукт фичерен на PH (запущен и показан публике)")
    else:
        lines.append("ℹ️ Пока не фичерен (coming soon / запуск впереди)")

    if len(tagline) >= 40:
        score += 10
        lines.append(f"✅ Слоган «мясо» ({len(tagline)} символов): «{tagline[:70]}»")
    elif tagline:
        score += 5
        lines.append(f"⚠️ Слоган короткий ({len(tagline)} символов) — лучше одна конкретная выгода")
    else:
        lines.append("⚠️ Слогана нет — без него продукт не продать")

    if has_video:
        score += 15
        lines.append("✅ Есть видео! Самый ценный актив для PH")
    else:
        lines.append("⚠️ Видео не нашёл — демо на 60 секунд даёт самый большой прирост")

    if len(media) >= 4:
        score += 5
        lines.append(f"✅ Галерея полная ({len(media)} изображения)")
    elif media:
        score += 3
        lines.append(f"⚠️ Галерея тонкая ({len(media)} — рекомендуется 4+)")
    else:
        lines.append("⚠️ Изображений нет вовсе — галерея из 4 картинок обязательна")

    if comments >= 10:
        score += 5
        lines.append(f"✅ Комьюнити живое: {comments} комментариев")

    lines.append(f"📊 Живые цифры: {votes} очков, {comments} комментариев")
    if topics:
        lines.append("🏷️ Категории: " + ", ".join(topics))

    out = "\n".join(lines)
    out += _score_line(score, score + 30, "(данные — открытый поиск Product Hunt, живые)")
    return out


async def ph_api_token(client: httpx.AsyncClient) -> str | None:
    """Токен только-чтение (client_credentials), кешируется на 10 часов."""
    now = time.time()
    if _ph_client_tok["token"] and now - _ph_client_tok["ts"] < 36000:
        return _ph_client_tok["token"]
    try:
        r = await client.post(
            PH_TOKEN_URL,
            json={"grant_type": "client_credentials",
                  "client_id": PH_API_KEY, "client_secret": PH_API_SECRET},
            timeout=20,
        )
        tok = (r.json() or {}).get("access_token")
        if tok:
            _ph_client_tok.update(token=tok, ts=now)
            return tok
    except Exception:
        pass
    return None


async def ph_api_lookup(client: httpx.AsyncClient, slug: str) -> dict | None:
    """Точный поиск запуска по slug в официальном API PH."""
    if not slug or not PH_API_KEY:
        return None
    token = await ph_api_token(client)
    if not token:
        return None
    q = 'query { post(slug: "%s") { %s } }' % (slug, PH_POST_SELECTION)
    try:
        r = await client.post(
            PH_GQL_URL,
            headers={"Authorization": "Bearer " + token,
                     "Content-Type": "application/json"},
            json={"query": q},
            timeout=20,
        )
        return ((r.json() or {}).get("data") or {}).get("post")
    except Exception:
        return None


def ph_slug_from_url(url: str) -> list[str]:
    """Slugs для официального API, по убыванию точности."""
    u = urlparse(url)
    slugs: list[str] = []
    for pair in (u.query or "").split("&"):
        k, _, v = pair.partition("=")
        if k == "launch" and v:
            slugs.append(v)
    path = u.path
    m = re.search(r"/products/([a-zA-Z0-9-]+)/launches/([a-zA-Z0-9-]+)", path)
    if m:
        slugs.append(m.group(2))
        slugs.append(m.group(1))
    else:
        m = re.search(r"/(?:products|posts)/([a-zA-Z0-9-]+)", path)
        if m:
            slugs.append(m.group(1))
    if not slugs:
        parts = [p for p in path.split("/") if p]
        if len(parts) == 1 and re.fullmatch(r"[a-zA-Z0-9-]+", parts[0]):
            slugs.append(parts[0])
    seen: set[str] = set()
    out: list[str] = []
    for s in slugs:
        if s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


def _fmt_date(iso: str) -> str:
    if not iso or len(iso) < 10:
        return ""
    d = iso[:10]
    return f"{d[8:10]}.{d[5:7]}.{d[0:4]}"


def analyze_ph_api(post: dict) -> str:
    """Диагностика по официальному API (самый авторитетный слой)."""
    name = post.get("name", "продукт")
    tagline = post.get("tagline") or ""
    votes = post.get("votesCount") or 0
    comments = post.get("commentsCount") or 0
    featured_at = post.get("featuredAt")
    media = [m for m in (post.get("media") or []) if isinstance(m, dict)]
    has_video = any(m.get("type") == "video" or m.get("videoUrl") for m in media)
    n_img = sum(1 for m in media if m.get("type") == "image")
    topics = [t.get("name") for t in ((post.get("topics") or {}).get("nodes") or [])
              if t.get("name")][:5]

    score = 15
    lines = [f"🔍 Посмотрел {name} — официальные живые данные Product Hunt (API)"]

    if featured_at:
        score += 5
        lines.append(f"✅ Фичерен на PH (запуск: {_fmt_date(featured_at)})")
    else:
        lines.append("ℹ️ Публичный запуск ещё не состоялся (coming soon / запланирован)")

    if len(tagline) >= 40:
        score += 10
        lines.append(f"✅ Слоган «мясо» ({len(tagline)} символов): «{tagline[:70]}»")
    elif tagline:
        score += 5
        lines.append(f"⚠️ Слоган короткий ({len(tagline)} символов) — лучше одна конкретная выгода")
    else:
        lines.append("⚠️ Слогана нет — без него продукт не продать")

    if has_video:
        score += 15
        lines.append("✅ Есть видео! Самый ценный актив для PH")
    else:
        lines.append("⚠️ Видео не нашёл — демо на 60 секунд даёт самый большой прирост")

    if n_img >= 4:
        score += 5
        lines.append(f"✅ Галерея полная ({n_img} изображений)")
    elif media:
        score += 3
        lines.append(f"⚠️ Галерея тонкая ({len(media)} актива — рекомендуется 4+ изображения)")
    else:
        lines.append("⚠️ Изображений нет вовсе — галерея из 4 картинок обязательна")

    if comments >= 10:
        score += 5
        lines.append(f"✅ Комьюнити живое: {comments} комментариев")

    lines.append(f"📊 Живые цифры: {votes} очков, {comments} комментариев")
    if topics:
        lines.append("🏷️ Категории: " + ", ".join(topics))

    return "\n".join(lines) + _score_line(
        score, score + 30, "(данные — официальный API Product Hunt, живые)"
    )


def _source_note(source: str) -> str:
    if source == "proxy":
        return "\n\n(посмотрел через бесплатный открытый прокси — данные живые)"
    if source == "reader":
        return "\n\n(посмотрел через открытый reader — оценка предварительная)"
    if source == "curl":
        return "\n\n(посмотрел через маскировку браузера — оценка предварительная)"
    if source.startswith("wayback:"):
        info = source.split(":", 1)[1]
        if info.endswith(":fresh"):
            return (f"\n\n(посмотрел через открытый веб-архив — свежая копия от "
                    f"{info[:-6]}, данные практически живые)")
        return (f"\n\n(посмотрел через открытый веб-архив, снимок от {info} — "
                f"активы страницы те же, живые цифры прилетят с бесплатным API PH)")
    return ""


# ---------- «глаза»: 5 слоёв ----------

def _ts_date(ts: str) -> str:
    return f"{ts[:4]}.{ts[4:6]}.{ts[6:8]}" if len(ts) >= 8 else "недавно"


async def _wayback_snap(client, ph_url: str):
    """Свежайший снимок в архиве: (url_snap, timestamp) или (None, None)."""
    try:
        avail = await client.get(
            "http://archive.org/wayback/available", params={"url": ph_url}, timeout=20
        )
        snap = (avail.json() or {}).get("archived_snapshots", {}).get("closest", {})
        if snap.get("available") and snap.get("url"):
            return snap["url"], snap.get("timestamp", "")
    except Exception:
        pass
    return None, None


async def _fetch_wayback_html(client, snap_url: str) -> str | None:
    try:
        r = await client.get(snap_url, timeout=40)
        if r.status_code == 200 and len(r.text) > 1000:
            return r.text
    except Exception:
        pass
    return None


async def fetch_page(url: str) -> tuple[str | None, str]:
    """Возвращает (html, источник): direct | curl | reader | wayback:ГГГГ.ММ.ДД | fail."""
    is_ph = "producthunt.com" in url

    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=BROWSER_HEADERS) as client:
        # слой 1: прямо
        try:
            resp = await client.get(url)
            if resp.status_code == 200 and len(resp.text) > 500 and "Just a moment" not in resp.text[:2000]:
                return resp.text, "direct"
        except Exception:
            pass

        # слой 2: curl_cffi — маска реального chrome (TLS-отпечаток)
        try:
            from curl_cffi import requests as cr
            resp2 = cr.get(url, timeout=25, impersonate="chrome")
            if resp2.status_code == 200 and len(resp2.text) > 500 and "Just a moment" not in resp2.text[:2000]:
                return resp2.text, "curl"
        except Exception:
            pass

        # слой 3: allorigins — бесплатный публичный прокси (проходит Cloudflare PH)
        try:
            resp3 = await client.get(
                "https://api.allorigins.win/raw?url=" + quote(url, safe=""), timeout=45
            )
            if resp3.status_code == 200 and len(resp3.text) > 500 and "Just a moment" not in resp3.text[:2000]:
                return resp3.text, "proxy"
        except Exception:
            pass

        # слой 4: открытый reader
        try:
            resp4 = await client.get("https://r.jina.ai/" + url, timeout=40)
            if resp4.status_code == 200 and len(resp4.text) > 200:
                wrapped = "<html><body><div>" + resp4.text + "</div></body></html>"
                return wrapped, "reader"
        except Exception:
            pass

        # слой 5: PH-страницы — открытый веб-архив
        # 5а: просим свежий снимок (Save Page Now, бесплатно, ~2-4 мин)
        # 5б: если свежего не дожались — отдаём последний снимок
        if is_ph:
            ph_url = f"https://www.producthunt.com{urlparse(url).path}"
            try:
                await client.get(
                    "https://web.archive.org/save/" + ph_url, timeout=60, follow_redirects=True
                )
            except Exception:
                pass

            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y%m%d%H%M%S")
            deadline = time.time() + 240
            old_snap = None
            while time.time() < deadline:
                await asyncio.sleep(15)
                snap_url, ts = await _wayback_snap(client, ph_url)
                if not snap_url:
                    continue
                if ts and ts >= cutoff:
                    html5 = await _fetch_wayback_html(client, snap_url)
                    if html5:
                        return html5, f"wayback:{_ts_date(ts)}:fresh"
                else:
                    old_snap = (snap_url, ts)

            if old_snap:
                html5 = await _fetch_wayback_html(client, old_snap[0])
                if html5:
                    return html5, f"wayback:{_ts_date(old_snap[1])}"

    return None, "fail"

# ---------- «мозг v1»: честная диагностика по правилам ----------

def _meta(soup: BeautifulSoup, names: list[str]) -> str:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return ""


def _score_line(score: int, with_prep: int, note: str) -> str:
    return (
        f"\n📊 Ваш шанс попасть в топ-5 прямо сейчас: ~{min(score, 60)}%\n"
        f"📈 Если подготовимся вместе 6 недель: ~{min(with_prep, 90)}%\n\n{note}"
    )


def analyze(html: str, url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    is_ph = "producthunt.com" in url
    domain = urlparse(url).netloc or url

    title = _meta(soup, ["og:title", "twitter:title"]) or (
        soup.title.get_text(strip=True) if soup.title else ""
    )
    desc = _meta(soup, ["og:description", "description", "twitter:description"])
    image = _meta(soup, ["og:image"])

    text_sample = soup.get_text(" ", strip=True)[:4000]
    cyrillic = len(re.findall(r"[\u0400-\u04FF]", text_sample))
    is_english = bool(text_sample) and (cyrillic / max(len(text_sample), 1)) < 0.15

    video = bool(soup.find("video"))
    for iframe in soup.find_all("iframe"):
        src = iframe.get("src", "")
        if "youtube.com" in src or "youtu.be" in src or "vimeo.com" in src:
            video = True

    points = None
    if is_ph:
        m = re.search(r'"points":\s*(\d+)', html)
        if m:
            points = int(m.group(1))

    score = 15  # базовый шанс любого случайного продукта
    lines = []

    if title:
        score += 5
        lines.append(f"✅ Название на месте: «{title[:70]}»")
    else:
        lines.append("⚠️ Название не нашёл — возможно, открылась не та страница")

    if len(desc) >= 60:
        score += 10
        lines.append("✅ Описание «мясо» (>= 60 символов) — аудитория поймёт, что это")
    elif desc:
        score += 5
        lines.append("⚠️ Описание коротковато — на PH решение принимают по одной фразе")
    else:
        lines.append("⚠️ Описание не нашёл — без него продукт не продать (нужно 1–2 предложения)")

    if image:
        score += 5
        lines.append("✅ Обложка есть (для PH нужно ещё 3–4 изображения в галерею)")
    else:
        lines.append("⚠️ Обложку не нашёл — галерея из 4 картинок обязательна")

    if video:
        score += 15
        lines.append("✅ Есть видео! Самый ценный актив для PH")
    else:
        lines.append("⚠️ Видео не нашёл — демо на 60 секунд даёт больше всего рейтинга")

    if is_english:
        score += 5
        lines.append("✅ Текст на английском — правильно для PH")
    else:
        lines.append("⚠️ Текст на другом языке — для PH нужен английский (переведём вместе)")

    if is_ph:
        extra = f" (в архиве {points} очков)" if points is not None else ""
        score += 5
        lines.append(f"ℹ️ Продукт уже на Product Hunt{extra}")
        lines.append(
            "ℹ️ Как устроена позиция: лидерборд сбрасывается каждый день в 00:01 "
            "по Калифорнии, и «движется» позиция только в 24 часа дня запуска. "
            "Победа дня = бейдж «Product of the Day», он остаётся на странице "
            "навсегда. Поэтому: день X впереди — план на 6 недель готовит к нему, "
            "а в сам день я дежурю на пульте 24 часа. День уже прошёл — бейдж и "
            "очки остаются, каждое крупное обновление = новый запуск."
        )

    out = [f"🔍 Посмотрел: {domain}", ""]
    out.extend(lines)
    out.append(_score_line(score, score + 30,
                            "Это предварительная оценка по странице — официальный API PH "
                            "данных по продукту пока не отдал (продукт не фичерен / новый)."))
    return "\n".join(out)


# ---------- режим «3 вопроса» (если глаза всё не увидели) ----------

QUIZ = {}  # user_id -> {"stage": int, "desc": str, "video": bool, "english": bool}

QUIZ_INTRO = (
    "😔 Страницу открыть не вышло — там сильная защита от ботов.\n"
    "Ничего страшного: спрошу 3 вопроса и оцену по вашим ответам — так даже честнее.\n\n"
    "❓ Вопрос 1/3: расскажите в 1–2 предложениях, что делает ваш продукт и для кого он?"
)
QUIZ_Q2 = "❓ Вопрос 2/3: на сайте есть демо-видео? (просто «да» или «нет»)"
QUIZ_Q3 = "❓ Вопрос 3/3: сайт на английском? («да» или «нет»)"


def quiz_score(st: dict) -> str:
    score = 15
    lines = ["🔍 Оценка по вашим ответам:"]

    desc = st.get("desc", "")
    if len(desc) >= 60:
        score += 10
        lines.append("✅ Описание «мясо» — вы чётко умеете объяснять продукт")
    elif len(desc) >= 20:
        score += 5
        lines.append("⚠️ Описание коротковато — помогу докрутить до одной бьющей в цель фразы")
    else:
        lines.append("⚠️ Начнём с одной фразы о продукте — без неё не продаётся ничего")

    if st.get("video"):
        score += 15
        lines.append("✅ Есть видео! Самый ценный актив для PH")
    else:
        lines.append("⚠️ Нет видео — демо на 60 секунд даст самый большой прирост")

    if st.get("english"):
        score += 5
        lines.append("✅ Сайт на английском — правильно для PH")
    else:
        lines.append("⚠️ Сайт не на английском — переведём ключевые тексты (смогу и сам)")

    lines.append(
        "ℹ️ Вашу страницу на PH вживую я не вижу (защита), и официальный API PH "
        "о ней пока ничего не знает — продукт ещё не фичерен."
    )
    return "\n".join(lines) + _score_line(score, score + 30, "")


# ---------- тексты (голос бота: «за ручку») ----------

WELCOME = """Привет! Я Launch Pilot 🚀

Проведу ваш продукт от «у меня есть идея» до бейджа «Product of the Day» на Product Hunt.

Как это работает:
1️⃣ Вы присылаете ссылку на продукт — я бесплатно проверяю и честно говорю, ваш ли это шанс на топ
2️⃣ Если да — ставлю на план на 6 недель: одна задача в день, все тексты пишу я
3️⃣ В день запуска сижу на пульте: позиция, каждый комментарий — вы только жмёте кнопки
4️⃣ Утром — отчёт + список всех, кто комментировал (ваши будущие клиенты)

👉 Пришлите ссылку на ваш продукт (https://...) — проверка бесплатная, займёт до минуты."""

WHAT_IS_PH = """🏛️ Product Hunt — витрина, где каждый день в 00:01 (по Калифорнии) продукты конкурируют за место в топе. Люди голосуют и комментируют, победитель получает бейдж «Product of the Day».

Почему фаундеры запускаются там:
• Бейдж = «нас заметили» — строка на сайте, в презентациях и холодных письмах
• Бесплатная ссылка с очень авторитетного сайта (SEO это очень ценит)
• Первые 500–5000 посетителей + список тех, кому продукт реально интересен

Моя работа — довести вас туда по шагам, без чтения 30 англоязычных гайдов.
Пришлите ссылку на продукт — проверю бесплатно 🙂"""

CHECKLIST = """📋 Мини-чек-лист (я проведу по нему вас пошагово, каждый день по одной задаче):

Недели 1–2 — «разогрев»:
• Профиль PH: фото, имя, одна строка «кто вы»
• 3 полезных комментария в день у запусков вашей категории
• Дата: вт–чт (максимум трафика) или выходные (шанс на #1)

Недели 3–4 — контенты:
• Видео на 60 секунд (самый важный актив!)
• 4 картинки в галерею
• Слоган: до 60 символов, одна конкретная выгода
• Первый комментарий «от основателя»: история, не реклама

Недели 5–6 — аудитория:
• Список 200–400 «тёплых» людей (друзья, подписчики, клиенты)
• Личное сообщение каждому (не копия-вставка!)
• Лендинг: место под бейдж + ссылка на PH

День запуска:
• 00:01 по Калифорнии — публикация, первый комментарий за 5 минут
• Ответ на каждый комментарий за 15 минут, 6 часов
• Позиция ниже топ-10? — второй публичный пост

После (48 часов):
• Спасибо каждому комментатору (это лиды!)
• 10 директорий, куда закинуть свежий след
• Бейдж на сайт + «Featured on Product Hunt» в письмах"""

PREP = f"""💳 Оплата (USDT / карта / Telegram Stars) подключается в ближайшие дни.

Пока — делаем всё вручную, результат тот же:
1) Напишите в канал {SUPPORT_CHANNEL}
2) Пришлите ссылку на продукт
3) В течение дня получите план + все тексты (всё то, что скоро будет делать бот сам)

Когда бот докручу до полного автопилота — всё то же, но нажимаешь только кнопки. 🤖"""

NOT_A_LINK = "Я понимаю только ссылки 🙂\nПришлите ссылку на ваш продукт (https://...) или на вашу страницу в Product Hunt — проверю бесплатно."

MAIN_BUTTONS = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🚀 Подготовить мой запуск — $49", callback_data="prep")],
    [InlineKeyboardButton(text="📋 Чек-лист", callback_data="checklist"),
     InlineKeyboardButton(text="❓ Что такое PH", callback_data="what")],
])

# ---------- обработчики (порядок важен: quiz -> ссылка -> прочее) ----------

@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❓ Что такое Product Hunt", callback_data="what")],
        [InlineKeyboardButton(text="📋 Чек-лист бесплатно", callback_data="checklist")],
    ])
    await message.answer(WELCOME, reply_markup=kb)


@router.callback_query(F.data == "what")
async def cb_what(cb: CallbackQuery) -> None:
    await cb.message.answer(WHAT_IS_PH)
    await cb.answer()


@router.callback_query(F.data == "checklist")
async def cb_checklist(cb: CallbackQuery) -> None:
    await cb.message.answer(CHECKLIST)
    await cb.answer()


@router.callback_query(F.data == "prep")
async def cb_prep(cb: CallbackQuery) -> None:
    await cb.message.answer(PREP)
    await cb.answer()


@router.message(F.text, lambda m: m.from_user and m.from_user.id in QUIZ)
async def handle_quiz(message: Message) -> None:
    st = QUIZ[message.from_user.id]
    ans = message.text.strip().lower()

    if st["stage"] == 0:
        st["desc"] = message.text.strip()
        st["stage"] = 1
        await message.answer(QUIZ_Q2)
    elif st["stage"] == 1:
        st["video"] = ans.startswith(("да", "yes", "y", "есть", "д"))
        st["stage"] = 2
        await message.answer(QUIZ_Q3)
    else:
        st["english"] = ans.startswith(("да", "yes", "y", "д"))
        del QUIZ[message.from_user.id]
        await message.answer(quiz_score(st), reply_markup=MAIN_BUTTONS)


@router.message(F.text.regexp(r"https?://\S+"))
async def handle_link(message: Message) -> None:
    url = re.search(r"https?://\S+", message.text).group(0)
    is_ph = "producthunt.com" in url

    if is_ph:
        # каскад: официальный API (мгновенно) -> открытый поиск (мгновенно)
        #         -> веб-архив (2-4 мин) -> вопросы
        status = await message.answer("🔎 Ищу ваш продукт в официальном API Product Hunt...")
        slugs = ph_slug_from_url(url)
        found = None
        async with httpx.AsyncClient(timeout=25) as api_client:
            for sl in slugs:
                found = await ph_api_lookup(api_client, sl)
                if found:
                    break
        if found:
            await status.edit_text(analyze_ph_api(found), reply_markup=MAIN_BUTTONS)
            return
        hit = await algolia_lookup(slugs[0]) if slugs else None
        if hit:
            await status.edit_text(analyze_ph_live(hit), reply_markup=MAIN_BUTTONS)
            return
        await status.edit_text(
            "📡 Продукта ещё нет в публичном поиске PH (coming soon / не фичерен).\n"
            "Снимаю свежую копию страницы через открытый веб-архив... (до 4 минут)"
        )
        html, source = await fetch_page(url)
        if html is None:
            QUIZ[message.from_user.id] = {"stage": 0}
            await status.edit_text(QUIZ_INTRO)
            return
        text = analyze(html, url) + _source_note(source)
        await status.edit_text(text, reply_markup=MAIN_BUTTONS)
        return

    status = await message.answer("🤔 Открываю страницу и смотрю... (до минуты)")
    html, source = await fetch_page(url)
    if html is None:
        QUIZ[message.from_user.id] = {"stage": 0}
        await status.edit_text(QUIZ_INTRO)
        return
    text = analyze(html, url) + _source_note(source)
    await status.edit_text(text, reply_markup=MAIN_BUTTONS)


@router.message(F.text)
async def handle_other(message: Message) -> None:
    await message.answer(NOT_A_LINK)

# ---------- старт (v0.1.5: свой поллинг + «watchdog») ----------
# Бесплатная песочница «засыпает», когда никто не работает: сокет до Telegram
# висирует, а стандартный aiogram-поллинг тихо умирает. Поэтому — свой цикл:
#  - wall-clock тайм-аут: нет ответа от Telegram > 2.5 мин -> сброс сессии
#  - ретраи с экспоненциальным бэкоффом на любые ошибки
#  - offset сохраняется в файл: после перезапуска бот добирает пропущенное

dp = Dispatcher()
dp.include_router(router)

OFFSET_FILE = Path(__file__).parent / "state_offset.txt"


def _read_offset() -> int | None:
    try:
        v = OFFSET_FILE.read_text().strip()
        return int(v) if v else None
    except Exception:
        return None


def _save_offset(update_id: int) -> None:
    try:
        OFFSET_FILE.write_text(str(update_id))
    except Exception:
        pass


async def resilient_polling(bot: Bot) -> None:
    state = {"last_ok": datetime.now(timezone.utc), "grace": timedelta(0)}
    stop_wd = asyncio.Event()
    last_beat = 0.0

    async def watchdog() -> None:
        while not stop_wd.is_set():
            await asyncio.sleep(30)
            grace_s = state["grace"].total_seconds()
            idle = (datetime.now(timezone.utc) - state["last_ok"]).total_seconds() - grace_s
            if idle > 150:
                print(f"⚠️ watchdog: Telegram молчит {int(idle + grace_s)} c — сбрасываю сессию",
                      flush=True)
                try:
                    await bot.session.close()
                except Exception:
                    pass
                state["last_ok"] = datetime.now(timezone.utc)
                state["grace"] = timedelta(minutes=2)

    wd = asyncio.create_task(watchdog())
    offset = _read_offset()
    backoff = 1.0
    try:
        while True:
            try:
                updates = await bot.get_updates(offset=offset, timeout=10)
                state["last_ok"] = datetime.now(timezone.utc)
                state["grace"] = timedelta(0)
                backoff = 1.0
                for update in updates:
                    offset = update.update_id + 1
                    _save_offset(update.update_id)
                    try:
                        await dp.feed_update(bot, update)
                    except Exception as e:
                        print("Ошибка обработчика:", repr(e), flush=True)
                if time.time() - last_beat > 300:
                    last_beat = time.time()
                    print(f"💓 уши OK (последний update: {offset})", flush=True)
            except Exception as e:
                state["last_ok"] = datetime.now(timezone.utc)
                print(f"⚠️ поллинг: {e!r} — повтор через {backoff:.0f} c", flush=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.6, 60.0)
    finally:
        stop_wd.set()
        await wd


async def main() -> None:
    bot = Bot(token=BOT_TOKEN)
    await bot.set_my_commands([
        BotCommand(command="start", description="Старт — бесплатная проверка продукта"),
    ])
    me = await bot.me()
    print(f"🤖 Бот @{me.username} запущен (v0.1.5, watchdog): /start -> ссылка -> диагностика",
          flush=True)
    await resilient_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
