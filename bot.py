"""
Launch Pilot (@PHlaunchpilot_bot) — v0.4.0 (радар-only модель)
==============================================================
v0.4.0: продукт = «Радар на день X» ($19 / ~TON).
  - новые «за ручку» тексты: бесплатный чек (только PH-ссылки) + гайд бесплатно,
    радар — один платный (кнопка «💳 Оплатить» -> страница TonConnect на GitHub Pages)
  - заказ создаётся в state/orders.json (GitHub API), «бухгалтер» (pay-verifier.yml)
    сверяет сеть каждые 3 минуты, бот сам видит paid и включает премиум (orders_poller)
  - запуск-автоматизация удалена (решение 2026-08-30)
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
import base64
import json
import math
import os
import random
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode, urlparse

import httpx
from bs4 import BeautifulSoup

import dayx
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
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

# ---------- оплата (v0.4.0) ----------
# Заказ -> state/orders.json (ветка state). «Бухгалтер» pay-verifier.yml сверяет
# сеть (toncenter) каждые 3 минуты и помечает paid. orders_poller в этом же боте
# видит paid и включает премиум + шлёт «радар включён».
GH_TOKEN = os.environ.get("GH_TOKEN", "").strip()
REPO_SLUG = os.environ.get("GITHUB_REPOSITORY", "").strip()
PAY_BASE = "https://timlabss.github.io/launchpilot-pay/"
RADAR_USD = 19.0
TON_RATE_FALLBACK = 1.35  # если все курсовые API легли — консервативная оценка

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

router = Router()

# ---------- юзеры и админка (v0.3.0) ----------

USERS_FILE = Path(__file__).parent / "users.json"
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()
ADMIN_MODE = {}    # admin_uid -> {"mode": "broadcast"|"give"|"take"|"reply", "to": int}
SUPPORT_WAIT = {}  # user_uid -> True: ждём вопрос поддержки


def _load_users() -> dict:
    try:
        return json.loads(USERS_FILE.read_text())
    except Exception:
        return {}


def _save_users(users: dict) -> None:
    try:
        USERS_FILE.write_text(json.dumps(users, ensure_ascii=False))
    except Exception:
        pass


def _touch_user(fu) -> None:
    """Завести/обновить запись пользователя. Первый в реестре = админ (владелец бота)."""
    uid = str(fu.id)
    users = _load_users()
    is_new = uid not in users
    e = users.setdefault(uid, {})
    if is_new:
        if not any(v.get("admin") for v in users.values()) and not ADMIN_CHAT_ID:
            e["admin"] = True
        e["first_seen"] = time.time()
    e["username"] = fu.username or e.get("username", "")
    e["name"] = fu.first_name or e.get("name", "")
    e["last_seen"] = time.time()
    if ADMIN_CHAT_ID and uid == ADMIN_CHAT_ID:
        e["admin"] = True
    _save_users(users)


def _is_admin(uid) -> bool:
    if ADMIN_CHAT_ID and str(uid) == ADMIN_CHAT_ID:
        return True
    return bool(_load_users().get(str(uid), {}).get("admin"))


def _find_user_ref(ref: str):
    """'ID' или '@username' -> (uid или None, users)."""
    users = _load_users()
    ref = ref.strip().lstrip("@").strip()
    if ref.isdigit():
        return (ref if ref in users else None), users
    low = ref.lower()
    for uid, e in users.items():
        if (e.get("username") or "").lower() == low:
            return uid, users
    return None, users


def _ts(v) -> str:
    if not v:
        return "—"
    d = time.strftime("%d.%m", time.localtime(float(v)))
    return d


def _client_kb(uid) -> InlineKeyboardMarkup:
    """Главное меню: всё кнопками, коротко. Кнопка «Админ» — только у админа."""
    rows = [
        [InlineKeyboardButton(text="🔎 Бесплатная проверка", callback_data="check")],
        [InlineKeyboardButton(text="📖 Гайд к запуску — бесплатно", callback_data="guide")],
        [InlineKeyboardButton(text="📡 Радар на день X — $19", callback_data="radar")],
        [InlineKeyboardButton(text="💬 Поддержка", callback_data="support")],
    ]
    if _is_admin(uid):
        rows.append([InlineKeyboardButton(text="⚙️ Админ", callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

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

WELCOME = """Привет! Я Launch Pilot 📡

Смотрю за запуском твоего продукта на Product Hunt в самый день X:
позиция, каждый комментарий — ответ пишу я, ты просто копируешь и вставляешь.

Сначала бесплатно:
• 🔎 Пришли ссылку на свой продукт на PH — покажу, что у тебя уже есть
• 📖 Гайд — пошаговый план подготовки к запуску

В день запуска — 📡 радар: 19 долларов, всё объясню по кнопке."""

WHAT_IS_PH = """🏛️ Product Hunt — витрина, где каждый день в 00:01 (по Калифорнии) продукты конкурируют за место в топе. Люди голосуют и комментируют, победитель получает бейдж «Product of the Day».

Почему фаундеры запускаются там:
• Бейдж = «нас заметили» — строка на сайте, в презентациях и холодных письмах
• Бесплатная ссылка с очень авторитетного сайта (SEO это очень ценит)
• Первые 500–5000 посетителей + список тех, кому продукт реально интересен

Моя работа — довести вас туда по шагам, без чтения 30 англоязычных гайдов.
Пришлите ссылку на продукт — проверю бесплатно 🙂"""

CHECK_INTRO = """🔎 Бесплатная проверка.

Пришли ссылку на страницу своего продукта на Product Hunt (producthunt.com).

Покажу, что у тебя уже есть: слоган, фото, видео, цифры, комменты — и честно скажу, чего не хватает до запуска.

Только для продуктов, которые уже на PH. Ещё не запустился? Жми «📖 Гайд»."""

RADAR_OFFER = """📡 Радар на день X — 19 долларов

В день запуска я слежу за продуктом за тебя:
• новый комментарий на PH → я пишу готовый осмысленный ответ — ты копируешь и вставляешь (10 секунд)
• каждые 2 часа — сводка: позиция, голоса, комменты
• утром — финальный отчёт + список всех, кто комментировал (твои будущие клиенты)

Оплата одной кнопкой, прямо в твоём кошельке Tonkeeper.
Как только платёж найдётся в сети (~5 минут), радар включу сам."""

PAY_TEXT = """🧾 Заказ: Радар на день X — {ton} TON (≈$19)

1️⃣ Жми кнопку ниже
2️⃣ Выбери кошелёк (Tonkeeper) и подтверди перевод
3️⃣ Всё — я найду платёж в сети и включу радар сам (~5 минут)

Если TON не хватает — пополни прямо в кошельке (обмен внутри): страница сама проверит баланс и не даст уйти впустую."""

NOT_A_LINK = "Я понимаю только ссылки 🙂\nПришлите ссылку на ваш продукт в Product Hunt (producthunt.com) — проверю бесплатно."

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



DAYX_INTRO = (
    "📡 День X: комната комментариев.\n\n"
    "Пришли ссылку на свой запуск на Product Hunt (или на страницу продукта):\n"
    "https://www.producthunt.com/posts/твой-продукт\n\n"
    "Что будет дальше:\n"
    "• новый комментарий → я мгновенно пишу ответ за тебя (под твой продукт)\n"
    "• ты копируешь текст и вставляешь в PH (10 секунд)\n"
    "• каждые 2 часа — сводка: голоса, комментарии, позиция дня\n"
    "• конец дня запуска — финальный отчёт + список тех, кто комментировал"
)

# ---------- обработчики (порядок важен: quiz -> ссылка -> прочее) ----------

@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(WELCOME, reply_markup=_client_kb(message.from_user.id))


@router.callback_query(F.data == "what")
async def cb_what(cb: CallbackQuery) -> None:
    await cb.message.answer(WHAT_IS_PH)
    await cb.answer()


@router.callback_query(F.data.in_({"guide", "checklist"}))
async def cb_guide(cb: CallbackQuery) -> None:
    await cb.message.answer(CHECKLIST)
    await cb.answer()


@router.callback_query(F.data == "check")
async def cb_check(cb: CallbackQuery) -> None:
    CHECK_WAIT[cb.from_user.id] = True
    await cb.message.answer(CHECK_INTRO)
    await cb.answer()


@router.callback_query(F.data.in_({"radar", "dayx"}))
async def cb_radar(cb: CallbackQuery) -> None:
    """Радар: у клиентов/админа — сразу регистрация запуска, у остальных — оффер + оплата."""
    uid = cb.from_user.id
    e = _load_users().get(str(uid), {})
    if _is_admin(uid) or e.get("premium"):
        DAYX_WAIT[uid] = True
        await cb.message.answer(DAYX_INTRO)
    else:
        await cb.message.answer(RADAR_OFFER, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить 19 $", callback_data="pay")],
            [InlineKeyboardButton(text="📖 Сначала гайд", callback_data="guide")],
        ]))
    await cb.answer()


@router.callback_query(F.data == "pay")
async def cb_pay(cb: CallbackQuery) -> None:
    uid = cb.from_user.id
    e = _load_users().get(str(uid), {})
    if _is_admin(uid) or e.get("premium"):
        DAYX_WAIT[uid] = True
        await cb.message.answer(DAYX_INTRO)
        await cb.answer()
        return
    if not (GH_TOKEN and REPO_SLUG):
        await cb.message.answer(
            "💳 Оплата сейчас временно недоступна (серверы отдыхают). Минут через 10 — попробуй ещё раз, "
            "или напиши в 💬 Поддержку."
        )
        await cb.answer()
        return
    await cb.answer()
    st = await cb.message.answer("🧾 Создаю заказ...")
    user_ref = f"@{cb.from_user.username}" if cb.from_user.username else str(uid)
    order = await create_order(uid, user_ref)
    if not order:
        await st.edit_text(
            "Не получилось создать заказ (сеть подзадержалась). Попробуй ещё раз через минуту — "
            "или нажми 💬 Поддержка."
        )
        return
    users = _load_users()
    users[str(uid)]["last_order"] = order["id"]
    _save_users(users)
    link = PAY_BASE + "?" + urlencode({
        "o": order["id"], "t": order["title"], "ton": order["ton"],
        "n": order["nano"], "m": order["created"],
    })
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💳 Оплатить {order['ton']} TON", web_app=WebAppInfo(url=link))],
        [InlineKeyboardButton(text="💬 Не понял — поддержка", callback_data="support")],
    ])
    await st.edit_text(PAY_TEXT.format(ton=order["ton"]), reply_markup=kb)


@router.callback_query(F.data == "support")
async def cb_support(cb: CallbackQuery) -> None:
    if _is_admin(cb.from_user.id):
        await cb.message.answer("Ты админ: тикеты пользователей приходят к тебе автоматически (кнопка «Ответить»).")
        await cb.answer()
        return
    SUPPORT_WAIT[cb.from_user.id] = True
    await cb.message.answer(
        "💬 Напиши свой вопрос — сразу уйдёт админу (он увидит тебя и твой ID).\n"
        "Ответ придёт прямо в этот чат."
    )
    await cb.answer()


# ---------- админка (кнопки) ----------

ADMIN_MENU_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
     InlineKeyboardButton(text="📣 Рассылка", callback_data="broadcast")],
    [InlineKeyboardButton(text="➕ Дать клиентом", callback_data="give"),
     InlineKeyboardButton(text="➖ Забрать клиентом", callback_data="take")],
])


@router.callback_query(F.data == "admin")
async def cb_admin(cb: CallbackQuery) -> None:
    if not _is_admin(cb.from_user.id):
        await cb.answer("Только для админа", show_alert=True)
        return
    await cb.message.answer("⚙️ Админка", reply_markup=ADMIN_MENU_KB)
    await cb.answer()


@router.callback_query(F.data == "stats")
async def cb_stats(cb: CallbackQuery) -> None:
    if not _is_admin(cb.from_user.id):
        await cb.answer("Только для админа", show_alert=True)
        return
    users = _load_users()
    total = len(users)
    clients = sum(1 for e in users.values() if e.get("premium"))
    tickets = sum(int(e.get("support", 0)) for e in users.values())
    lines = [f"📊 Статистика:\nПользователей: {total} · Клиентов: {clients} · Тикетов: {tickets}", ""]
    for uid, e in sorted(users.items(), key=lambda kv: kv[1].get("last_seen", 0), reverse=True)[:25]:
        tags = []
        if e.get("admin"):
            tags.append("админ")
        if e.get("premium"):
            tags.append("клиент")
        un = f"@{e['username']}" if e.get("username") else "без username"
        line = f"{uid} · {un} · с {_ts(e.get('first_seen'))} · был {_ts(e.get('last_seen'))}"
        if tags:
            line += " · " + ", ".join(tags)
        lines.append(line)
    await cb.message.answer("\n".join(lines))
    await cb.answer()


@router.callback_query(F.data == "broadcast")
async def cb_broadcast(cb: CallbackQuery) -> None:
    if not _is_admin(cb.from_user.id):
        await cb.answer("Только для админа", show_alert=True)
        return
    ADMIN_MODE[cb.from_user.id] = {"mode": "broadcast"}
    await cb.message.answer(
        "📣 Пришли текст рассылки (плюшки, промо, анонс — что хочешь).\n"
        "Я отправлю его каждому пользователю бота."
    )
    await cb.answer()


@router.callback_query(F.data.in_({"give", "take"}))
async def cb_give_take(cb: CallbackQuery) -> None:
    if not _is_admin(cb.from_user.id):
        await cb.answer("Только для админа", show_alert=True)
        return
    ADMIN_MODE[cb.from_user.id] = {"mode": cb.data}
    verb = "дать" if cb.data == "give" else "забрать"
    await cb.message.answer(f"➕/➖ {verb} статус «клиент».\nПришли ID пользователя (цифры) или @username.")
    await cb.answer()


@router.callback_query(F.data.startswith("rply_"))
async def cb_reply(cb: CallbackQuery) -> None:
    if not _is_admin(cb.from_user.id):
        await cb.answer("Только для админа", show_alert=True)
        return
    to = int(cb.data.split("_", 1)[1])
    ADMIN_MODE[cb.from_user.id] = {"mode": "reply", "to": to}
    await cb.message.answer(f"✍️ Напиши ответ — доставлю его в чат пользователя {to}.")
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
        await message.answer(quiz_score(st), reply_markup=_client_kb(message.from_user.id))


# ---------- оплата: заказ + опрос «бухгалтера» (v0.4.0) ----------

async def ton_rate() -> float:
    """Курс TON в USD: Coinbase spot -> CoinGecko -> консервативный fallback."""
    async with httpx.AsyncClient(timeout=15) as client:
        for url, pick in (
            ("https://api.coinbase.com/v2/prices/TON-USD/spot",
             lambda j: float(((j or {}).get("data") or {}).get("amount") or 0)),
            ("https://api.coingecko.com/api/v3/simple/price?ids=toncoin&vs_currencies=usd",
             lambda j: float(((j or {}).get("toncoin") or {}).get("usd") or 0)),
        ):
            try:
                r = await client.get(url)
                if r.status_code == 200:
                    v = pick(r.json())
                    if 0.2 < v < 20:  # здравый диапазон, иначе — следующий источник
                        return v
            except Exception:
                pass
    return TON_RATE_FALLBACK


async def _gh_read_orders() -> tuple[str | None, str | None]:
    """-> (sha, json-текст) state/orders.json или (None, None)."""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(
                f"https://api.github.com/repos/{REPO_SLUG}/contents/state/orders.json?ref=state",
                headers={"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"},
            )
            if r.status_code == 404:
                return None, None
            if r.status_code != 200:
                print("orders: read fail", r.status_code, flush=True)
                return None, None
            j = r.json()
            return j.get("sha"), base64.b64decode(j.get("content", "")).decode()
    except Exception as e:
        print("orders: read err", repr(e), flush=True)
        return None, None


async def _gh_write_orders(content: str, sha: str | None) -> bool:
    payload = {
        "message": "pay: orders update (bot)",
        "content": base64.b64encode(content.encode()).decode(),
        "branch": "state",
    }
    if sha:
        payload["sha"] = sha
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.put(
                f"https://api.github.com/repos/{REPO_SLUG}/contents/state/orders.json",
                headers={"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"},
                json=payload,
            )
            return r.status_code in (200, 201)
    except Exception as e:
        print("orders: write err", repr(e), flush=True)
        return False


async def create_order(uid: int, user_ref: str) -> dict | None:
    """Создаёт заказ «Радар» в state/orders.json (до 5 попыток против sha-конфликтов)."""
    rate = await ton_rate()
    ton = math.ceil(RADAR_USD / rate * 10) / 10  # вверх до 0.1 TON
    nano = int(round(ton * 1e9))
    oid = "lp_" + format(int(time.time()), "x") + "".join(
        random.choices("abcdefghjkmnpqrstuvwxyz23456789", k=4))
    order = {
        "id": oid, "chat": uid, "user": user_ref,
        "title": "Радар на день X", "usd": RADAR_USD,
        "ton": f"{ton:g}", "nano": str(nano),
        "created": int(time.time() * 1000), "status": "pending",
    }
    for attempt in range(5):
        sha, text = await _gh_read_orders()
        doc = {"meta": {"used_tx": []}, "orders": []}
        if text:
            try:
                doc = json.loads(text)
            except Exception:
                pass
        doc.setdefault("meta", {"used_tx": []})
        doc.setdefault("orders", [])
        doc["orders"].append(order)
        if await _gh_write_orders(json.dumps(doc, ensure_ascii=False), sha):
            print(f"order created: {oid} ton={order['ton']} rate={rate}", flush=True)
            return order
        await asyncio.sleep(1 + attempt)
    return None


async def orders_poller(bot: Bot) -> None:
    """Каждые 45 секунд: новый paid-заказ -> премиум + «радар включён»."""
    if not (GH_TOKEN and REPO_SLUG):
        print("💰 orders_poller: нет GH_TOKEN — отключён (оплата не будет подтверждаться)", flush=True)
        return
    print("💰 orders_poller: смотрю state/orders.json каждые 45 с", flush=True)
    while True:
        await asyncio.sleep(45)
        try:
            _, text = await _gh_read_orders()
            if not text:
                continue
            doc = json.loads(text)
            users = _load_users()
            changed = False
            for o in doc.get("orders", []):
                if o.get("status") != "paid":
                    continue
                uid = str(o.get("chat"))
                e = users.get(uid)
                if not e or e.get("premium"):
                    continue
                e["premium"] = True
                e.setdefault("paid_orders", []).append(o.get("id"))
                users[uid] = e
                changed = True
                try:
                    await bot.send_message(
                        int(uid),
                        "✅ Платёж найден в сети — радар включён! 📡\n\n" + DAYX_INTRO,
                    )
                except Exception as ex:
                    print("orders_poller: send fail:", repr(ex), flush=True)
            if changed:
                _save_users(users)
                print("orders_poller: premium включён", flush=True)
        except Exception as ex:
            print("orders_poller err:", repr(ex), flush=True)


# ---------- День X: «комната комментариев» (v0.2.0) ----------
# Регистрация: кнопка «📡 Радар» (или /dayx) -> клиент шлёт ссылку на запуск -> slug -> API PH.
# Дальше радар (dayx_poller) живёт сам: новый комментарий -> черновик ответа.
# ВАЖНО: регистрируем ДО handle_link, иначе ссылка улетит в бесплатную диагностику.

DAYX_WAIT = {}   # chat_id -> True: ждём ссылку на запуск
CHECK_WAIT = {}  # chat_id -> True: ждём PH-ссылку для бесплатной проверки

RADAR_ENTRY_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="💳 Оплатить 19 $", callback_data="pay")],
    [InlineKeyboardButton(text="📖 Сначала гайд", callback_data="guide")],
])


def _radar_gate(uid: int) -> bool:
    """True, если у юзера есть доступ к радару (админ или клиент/премиум)."""
    if _is_admin(uid):
        return True
    return bool(_load_users().get(str(uid), {}).get("premium"))


@router.message(F.text.startswith("/dayx"))
async def cmd_dayx(message: Message) -> None:
    # запасной вход (главный — кнопка «📡 Радар»)
    uid = message.from_user.id
    if _radar_gate(uid):
        DAYX_WAIT[uid] = True
        await message.answer(DAYX_INTRO)
    else:
        await message.answer(RADAR_OFFER, reply_markup=RADAR_ENTRY_KB)


@router.message(F.text, lambda m: m.from_user.id in ADMIN_MODE)
async def handle_admin_msg(message: Message) -> None:
    """Текст админа, когда он что-то нажал в админке (рассылка / дать / забрать / ответ)."""
    st = ADMIN_MODE.pop(message.from_user.id, None)
    if not st:
        return
    txt = message.text.strip()
    mode = st.get("mode")

    if mode == "broadcast":
        users = _load_users()
        ok, fail = 0, 0
        for uid, _e in users.items():
            if int(uid) == message.from_user.id:
                continue
            try:
                await message.bot.send_message(int(uid), txt)
                ok += 1
            except Exception:
                fail += 1
            if ok and ok % 25 == 0:
                await asyncio.sleep(1.0)
        await message.answer(f"📣 Рассылка завершена: доставлено {ok}, не доставлено {fail}.")

    elif mode in ("give", "take"):
        uid, users = _find_user_ref(txt)
        if not uid:
            await message.answer("Не нашёл такого пользователя. Пришли ID из статистики (кнопка 📊) или @username.")
            return
        users[uid]["premium"] = (mode == "give")
        _save_users(users)
        un = users[uid].get("username", "без username")
        word = "дан" if mode == "give" else "снят"
        await message.answer(f"✅ Статус «клиент» {word} пользователю {uid} (@{un}).")

    elif mode == "reply":
        try:
            await message.bot.send_message(st["to"], "🎫 Поддержка:\n" + txt)
            await message.answer(f"✅ Ответ доставлен пользователю {st['to']}.")
        except Exception:
            await message.answer("Не смог доставить (возможно, пользователь заблокировал бота).")


@router.message(F.text, lambda m: m.from_user.id in SUPPORT_WAIT)
async def handle_support_msg(message: Message) -> None:
    """Вопрос поддержки -> тикет в чат админа (с кнопкой «Ответить»)."""
    SUPPORT_WAIT.pop(message.from_user.id, None)
    u = message.from_user
    users = _load_users()
    e = users.get(str(u.id), {})
    e["support"] = int(e.get("support", 0)) + 1
    users[str(u.id)] = e
    _save_users(users)

    admin_uid = None
    if ADMIN_CHAT_ID:
        admin_uid = int(ADMIN_CHAT_ID)
    else:
        for uid, ee in users.items():
            if ee.get("admin"):
                admin_uid = int(uid)
                break
    if not admin_uid:
        await message.answer("Поддержка сейчас не подключена. Напиши в канал https://t.me/ProductHuntBoosting")
        return
    un = f"@{u.username}" if u.username else "без username"
    try:
        await message.bot.send_message(
            admin_uid,
            f"🎫 Тикет: {un} (ID: {u.id})\n\n“{message.text}”",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="↩️ Ответить", callback_data=f"rply_{u.id}")],
            ]),
        )
        await message.answer("✅ Вопрос ушёл админу. Ответ придёт прямо в этот чат.")
    except Exception:
        await message.answer("Не смог отправить админу. Напиши в канал https://t.me/ProductHuntBoosting")


@router.message(F.text.regexp(r"https?://\S+"),
                lambda m: m.from_user.id in DAYX_WAIT)
async def handle_dayx_link(message: Message) -> None:
    uid = message.from_user.id
    DAYX_WAIT.pop(uid, None)
    url = re.search(r"https?://\S+", message.text).group(0)

    if "producthunt.com" not in url:
        await message.answer(
            "Нужна ссылка именно на Product Hunt (producthunt.com), например:\n"
            "https://www.producthunt.com/posts/твой-продукт\n\n"
            "Пришли её — подключу радар."
        )
        return

    status = await message.answer("🔎 Нахожу запуск в официальном API Product Hunt...")
    found, found_slug = None, None
    async with httpx.AsyncClient(timeout=25) as api_client:
        for sl in ph_slug_from_url(url):
            f = await ph_api_lookup(api_client, sl)
            if f:
                found, found_slug = f, sl
                break
    if not found or not found_slug:
        await status.edit_text(
            "Хм, по этой ссылке запуск в API PH не нашёлся.\n\n"
            "Варианты:\n"
            "• это coming-soon / запуск ещё не опубликован — пришли ссылку позже, "
            "когда запуск станет публичным (радар включится сам)\n"
            "• ссылка не из producthunt.com\n\n"
            "Пока могу бесплатно проверить страницу: просто пришли её ещё раз "
            "без /dayx 🙂"
        )
        return

    post, comments = await dayx.fetch_launch(found_slug)
    if post is None:
        await status.edit_text("Не смог открыть запуск. Проверь ссылку и пришли ещё раз.")
        return

    state = dayx.load_state()
    state[found_slug] = {
        "chat_id": uid,
        "name": post.get("name"),
        "tagline": (post.get("tagline") or "")[:200],
        "seen": [c["id"] for c in comments],
        "added_at": time.time(),
        "last_summary": time.time(),
        "last_wait": time.time(),
        "seen_live": False,
        "finished": False,
    }
    dayx.save_state(state)
    live = (post.get("featuredAt") or post.get("createdAt") or "")[:16].replace("T", " ")
    await status.edit_text(
        f"✅ «{post.get('name')}» подключён к радару.\n\n"
        f"Сейчас: {post.get('votesCount')} голосов, {post.get('commentsCount')} комментариев.\n"
        f"Запуск: {live} UTC.\n\n"
        "Как работает:\n"
        "• новый комментарий → я пишу ответ под твой продукт и шлю его сюда\n"
        "• ты: долгое нажатие → копировать → вставить в поле комментария PH → отправить\n"
        "• каждые 2 часа — сводка (голоса / комментарии / позиция дня)\n"
        "• конец дня запуска — финальный отчёт"
    )


@router.message(F.text.regexp(r"https?://\S+"))
async def handle_link(message: Message) -> None:
    url = re.search(r"https?://\S+", message.text).group(0)
    is_ph = "producthunt.com" in url

    if message.from_user.id in CHECK_WAIT:
        CHECK_WAIT.pop(message.from_user.id, None)
        if not is_ph:
            await message.answer(
                "Бесплатная проверка — только для продуктов, которые уже на Product Hunt. "
                "Пришли ссылку вида https://www.producthunt.com/posts/твой-продукт 🙂"
            )
            return

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
            await status.edit_text(analyze_ph_api(found), reply_markup=_client_kb(message.from_user.id))
            return
        hit = await algolia_lookup(slugs[0]) if slugs else None
        if hit:
            await status.edit_text(analyze_ph_live(hit), reply_markup=_client_kb(message.from_user.id))
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
        await status.edit_text(text, reply_markup=_client_kb(message.from_user.id))
        return

    status = await message.answer("🤔 Открываю страницу и смотрю... (до минуты)")
    html, source = await fetch_page(url)
    if html is None:
        QUIZ[message.from_user.id] = {"stage": 0}
        await status.edit_text(QUIZ_INTRO)
        return
    text = analyze(html, url) + _source_note(source)
    await status.edit_text(text, reply_markup=_client_kb(message.from_user.id))


@router.message(F.text)
async def handle_other(message: Message) -> None:
    if _is_admin(message.from_user.id):
        await message.answer("Ты админ: все действия — кнопками (⚙️ Админ). Текст я понимаю только в режимах админки.")
        return
    await message.answer(NOT_A_LINK)

# ---------- старт (v0.1.5: свой поллинг + «watchdog») ----------
# Бесплатная песочница «засыпает», когда никто не работает: сокет до Telegram
# висирует, а стандартный aiogram-поллинг тихо умирает. Поэтому — свой цикл:
#  - wall-clock тайм-аут: нет ответа от Telegram > 2.5 мин -> сброс сессии
#  - ретраи с экспоненциальным бэкоффом на любые ошибки
#  - offset сохраняется в файл: после перезапуска бот добирает пропущенное

dp = Dispatcher()
dp.include_router(router)


class _UserTouchMiddleware:
    """Каждое сообщение/нажатие — в реестр users.json (первый = админ)."""

    async def __call__(self, handler, event, data):
        try:
            fu = event.from_user
            if fu and getattr(fu, "id", None):
                _touch_user(fu)
        except Exception:
            pass
        return await handler(event, data)


dp.message.middleware(_UserTouchMiddleware())
dp.callback_query.middleware(_UserTouchMiddleware())

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
        BotCommand(command="start", description="Старт — бесплатная проверка + гайд"),
        BotCommand(command="dayx", description="Радар на день запуска (платный)"),
    ])
    me = await bot.me()
    print(f"🤖 Бот @{me.username} запущен (v0.4.0: радар-only, оплата TON, автоподключение)", flush=True)
    asyncio.create_task(dayx.dayx_poller(bot))
    asyncio.create_task(orders_poller(bot))
    await resilient_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
