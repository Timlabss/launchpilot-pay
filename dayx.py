# -*- coding: utf-8 -*-
"""
День X: «комната комментариев» (v0.2.0)
Радар поверх официального Product Hunt API (только чтение):
  новый комментарий -> ИИ пишет черновик ответа (или шаблон) -> Telegram
  каждые 2 часа -> сводка (голоса, комментарии, позиция дня)
  конец дня запуска -> финальный отчёт.
Состояние: dayx.json (workflow на GitHub копит его на ветке state).
"""
import asyncio
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

STATE_FILE = Path(__file__).parent / "dayx.json"
PH_GQL_URL = "https://api.producthunt.com/v2/api/graphql"
PH_TOKEN_URL = "https://api.producthunt.com/v2/oauth/token"
PH_API_KEY = os.getenv("PH_API_KEY", "")
PH_API_SECRET = os.getenv("PH_API_SECRET", "")

POLL_SECS = 120          # новый комментарий ловим за ~2 минуты
SUMMARY_EVERY = 7200     # сводка каждые 2 часа
WAIT_PING = 4 * 3600     # «ждём публичный запуск» — напоминаем раз в 4 часа
END_AFTER = 26 * 3600    # день запуска закончился

_tok = {"tok": None, "at": 0.0}


# ---------- состояние ----------

def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False))
    except Exception:
        pass


# ---------- «мозг»: LLM-адаптер (включаем ключ — черновики становятся живыми) ----------

async def llm_draft(product: str, tagline: str, comment: str):
    """Черновик ответа. Возвращает None, если ни одного LLM недоступно -> шаблон."""
    prompt = (
        "You are the maker replying to a comment on your Product Hunt launch.\n"
        f"Product: {product}\nTagline: {tagline}\nComment: {comment[:600]}\n"
        "Write ONE short reply (max 2 sentences) in the same language as the comment. "
        "Tone: warm, human, non-defensive, specific. No hashtags, no links, no emoji spam. "
        "If it is a question you cannot answer precisely, invite them to DM you. "
        "Output ONLY the reply text."
    )
    cands = []
    if os.getenv("OPENROUTER_API_KEY"):
        cands.append(("openrouter", "https://openrouter.ai/api/v1/chat/completions",
                      os.getenv("OPENROUTER_API_KEY"),
                      os.getenv("LLM_MODEL", "deepseek/deepseek-chat-v3-0324:free")))
    if os.getenv("CEREBRAS_API_KEY"):
        cands.append(("cerebras", "https://api.cerebras.ai/v1/chat/completions",
                      os.getenv("CEREBRAS_API_KEY"),
                      os.getenv("LLM_MODEL", "llama-3.3-70b")))
    if os.getenv("HF_API_KEY"):
        cands.append(("hf", "https://api-inference.huggingface.co/v1/chat/completions",
                      os.getenv("HF_API_KEY"),
                      os.getenv("LLM_MODEL", "deepseek-ai/DeepSeek-V3")))
    for name, url, key, model in cands:
        headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
        if name == "openrouter":
            headers["HTTP-Referer"] = "https://launchpilot"
            headers["X-Title"] = "launchpilot"
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post(url, headers=headers,
                                 json={"model": model,
                                       "messages": [{"role": "user", "content": prompt}],
                                       "max_tokens": 200},
                                 timeout=30)
            if r.status_code == 200:
                txt = (r.json()["choices"][0]["message"]["content"] or "").strip()
                if txt:
                    return txt
            else:
                print(f"⚠️ LLM {name}: HTTP {r.status_code}", flush=True)
        except Exception as e:
            print(f"⚠️ LLM {name}: {e!r}", flush=True)
    return None


def template_reply(comment: str) -> str:
    """Офлайн-запас: честные шаблоны по типу комментария (PH — англоязычная сцена)."""
    c = comment.lower()
    if any(w in c for w in ("thank", "thanks", "appreciate", "спасибо")):
        return "Thank you! Really appreciate you taking the time 🙌"
    if any(w in c for w in ("love", "loved", "great", "awesome", "amazing", "impressed", "cool")):
        return "Thank you so much, means a lot to us! 💙"
    if ("?" in comment) or any(w in c for w in ("how", "what", "why", "when", "is there",
                                                 "can you", "could you", "pricing", "how does")):
        return "Great question! I'd rather give you the full picture — ping me here or in DMs."
    if any(w in c for w in ("concern", "worry", "missing", "lacks", "problem", "however", "dealbreaker")):
        return "Fair point, thanks for the honesty. That's exactly the feedback we build with — would love to hear more in DMs."
    return "Thanks for the feedback — noted and much appreciated!"


def _clean_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", s).strip()


# ---------- Product Hunt (чтение) ----------

async def _ph_token(client: httpx.AsyncClient):
    if _tok["tok"] and time.time() - _tok["at"] < 3000:
        return _tok["tok"]
    if not (PH_API_KEY and PH_API_SECRET):
        return None
    try:
        r = await client.post(PH_TOKEN_URL, timeout=20, json={
            "grant_type": "client_credentials",
            "client_id": PH_API_KEY, "client_secret": PH_API_SECRET})
        t = r.json().get("access_token")
        if t:
            _tok.update(tok=t, at=time.time())
        return t
    except Exception:
        return None


def _pt_midnight_iso() -> str:
    """Полночь по Пасифике (PH-день) в ISO UTC."""
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("America/Los_Angeles"))
        pt = now.replace(hour=0, minute=0, second=0, microsecond=0)
    except Exception:
        pt = (datetime.now(timezone.utc) - timedelta(hours=7)).replace(hour=0, minute=0, second=0, microsecond=0)
    return pt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


async def fetch_launch(slug: str):
    """(post, [comments]) или (None, []) — запуск не найден/не публичный."""
    async with httpx.AsyncClient(timeout=25) as client:
        tok = await _ph_token(client)
        if not tok:
            return None, []
        H = {"Authorization": "Bearer " + tok, "Content-Type": "application/json"}
        q = ('query { post(slug: "%s") { name tagline description url website createdAt featuredAt '
             'votesCount commentsCount '
             'comments(first: 20, order: NEWEST) { edges { node { id createdAt votesCount body '
             'user { username followersCount } } } } } }') % slug
        try:
            r = await client.post(PH_GQL_URL, headers=H, json={"query": q}, timeout=25)
            d = r.json()
        except Exception:
            return None, []
        p = (d.get("data") or {}).get("post")
        if not p:
            return None, []
        post = {k: p.get(k) for k in ("name", "tagline", "description", "url", "website",
                                      "createdAt", "featuredAt", "votesCount", "commentsCount")}
        comments = []
        for e in (p.get("comments") or {}).get("edges", []):
            n = e.get("node") or {}
            body = _clean_html(n.get("body"))
            if not body:
                continue
            u = n.get("user") or {}
            comments.append({"id": n.get("id"), "createdAt": n.get("createdAt", ""),
                             "body": body, "votes": n.get("votesCount", 0),
                             "user": u.get("username"), "followers": u.get("followersCount", 0)})
        return post, comments


async def fetch_today_rank(votes: int) -> str:
    """Позиция среди сегодняшних запусков (через топ-100 по голосам)."""
    async with httpx.AsyncClient(timeout=25) as client:
        tok = await _ph_token(client)
        if not tok:
            return "—"
        H = {"Authorization": "Bearer " + tok, "Content-Type": "application/json"}
        q = ('query { posts(first: 100, order: VOTES, postedAfter: "%s") '
             '{ edges { node { votesCount } } } }') % _pt_midnight_iso()
        try:
            r = await client.post(PH_GQL_URL, headers=H, json={"query": q}, timeout=25)
            d = r.json()
        except Exception:
            return "—"
        edges = ((d.get("data") or {}).get("posts") or {}).get("edges", [])
        if not edges:
            return "—"
        ahead = sum(1 for e in edges if (e.get("node") or {}).get("votesCount", 0) > votes)
        return "100+" if ahead >= len(edges) else str(ahead + 1)


# ---------- сам радар ----------

async def dayx_poller(bot) -> None:
    state = load_state()
    print(f"📡 День X: радар запущен, запусков под наблюдением: {list(state.keys()) or 'пока нет'}",
          flush=True)
    while True:
        try:
            await _tick(bot, state)
        except Exception as e:
            print("⚠️ день X tick:", repr(e), flush=True)
        await asyncio.sleep(POLL_SECS)


async def _tick(bot, state: dict) -> None:
    if not state:
        return
    for slug, st in list(state.items()):
        if st.get("finished"):
            continue
        post, comments = await fetch_launch(slug)
        if post is None:
            # ещё не публичный — мягко напоминаем, что ждём
            if time.time() - st.get("last_wait", 0) > WAIT_PING:
                st["last_wait"] = time.time()
                try:
                    await bot.send_message(st["chat_id"],
                                           "⏳ Запуск ещё не стал публичным — продолжаю следить (каждые 2 минуты).")
                except Exception:
                    pass
            save_state(state)
            continue

        if not st.get("seen_live"):
            st["seen_live"] = True
            live = (post.get("featuredAt") or post.get("createdAt") or "")[:16].replace("T", " ")
            try:
                await bot.send_message(
                    st["chat_id"],
                    f"🚀 {post.get('name')} — LIVE на Product Hunt (с {live} UTC).\n"
                    "Радар на полном ходу: за каждый новый комментарий пришлю готовый ответ.")
            except Exception:
                pass

        seen = set(st.get("seen", []))
        new = [c for c in comments if c["id"] not in seen]
        for c in new[:10]:
            seen.add(c["id"])
            draft = await llm_draft(post.get("name", ""), post.get("tagline", ""), c["body"])
            draft = draft or template_reply(c["body"])
            who = (c["user"] + " ") if c.get("user") else ""
            wt = (c.get("createdAt") or "")[:16].replace("T", " ")
            txt = (f"💬 Новый комментарий ({wt} UTC, {who}{c.get('followers', 0)} подписчиков):\n"
                   f"“{c['body'][:450]}”\n\n"
                   f"→ Ответ (долгое нажатие → копировать → вставить в поле комментария на PH):\n"
                   f"“{draft[:600]}”")
            try:
                await bot.send_message(st["chat_id"], txt)
            except Exception as e:
                print("⚠️ день X отправка:", repr(e), flush=True)
        st["seen"] = list(seen)[-600:]
        st["last_votes"] = post.get("votesCount")

        if time.time() - st.get("last_summary", 0) > SUMMARY_EVERY:
            st["last_summary"] = time.time()
            rank = await fetch_today_rank(post.get("votesCount", 0) or 0)
            st["last_rank"] = rank
            try:
                await bot.send_message(
                    st["chat_id"],
                    f"📊 Сводка радара:\n"
                    f"Голоса: {post.get('votesCount')} · Комментариев: {post.get('commentsCount')} · Позиция дня: №{rank}")
            except Exception:
                pass

        live_iso = post.get("featuredAt") or post.get("createdAt") or ""
        if live_iso:
            try:
                age = (datetime.now(timezone.utc)
                       - datetime.fromisoformat(live_iso.replace("Z", "+00:00"))).total_seconds()
                if age > END_AFTER:
                    st["finished"] = True
                    try:
                        await bot.send_message(
                            st["chat_id"],
                            f"🏁 День запуска завершён.\n"
                            f"Итог: {post.get('votesCount')} голосов, {post.get('commentsCount')} комментариев, "
                            f"позиция №{st.get('last_rank', '—')}.\nОтличная работа — до новых запусков! 🚀")
                    except Exception:
                        pass
            except Exception:
                pass
        save_state(state)
