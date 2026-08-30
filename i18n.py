# -*- coding: utf-8 -*-
"""
i18n: все тексты бота (RU/EN) + выбор языка пользователя.
users.json: {uid: {"lang": "ru"|"en", ...}} (по умолчанию ru).
"""
import json
from pathlib import Path

USERS_FILE = Path(__file__).parent / "users.json"

TEXTS = {
"ru": {
  "welcome": (
      "Привет! Я Launch Pilot 📡\n\n"
      "В день X слежу за запуском твоего продукта на Product Hunt: "
      "позиция, голоса, каждый комментарий — ответ пишу я, ты просто копируешь.\n\n"
      "1️⃣ Жмёшь «📡 День X» — кидаешь ссылку на запуск\n"
      "2️⃣ Показываю, что у тебя уже есть, и подключаю радар бесплатно — живые цифры у тебя в чате\n"
      "3️⃣ Когда будешь готов — включай полный радар: ответы на каждый комментарий, сводки, "
      "финальный отчёт со списком клиентов\n\nМеню ниже 👇"
  ),
  "dayx_ask": (
      "📡 День X: день твоего запуска\n\n"
      "Я слежу за твоим запуском на Product Hunt с 00:01 (по Калифорнии) и до конца дня.\n\n"
      "Подключение: пришли мне ссылку на запуск:\n"
      "https://www.producthunt.com/posts/твой-продукт\n\n"
      "Что будет дальше:\n"
      "• Покажу, что у тебя уже есть: слоган, фото, видео, цифры — и что недоставало бы до запуска\n"
      "• Подключу радар бесплатно: пришлю живые цифры (голоса, комменты, позиция) и сообщу, когда стартует запуск\n"
      "• Когда захочешь — включишь полный радар: пишу готовый ответ под каждый новый комментарий (ты просто "
      "копируешь), каждые 2 часа — детальная сводка, утром — финальный отчёт + список всех, кто комментировал (твои клиенты)\n\n"
      "Кидай ссылку 👇"
  ),
  "pay_text": (
      "🧾 Заказ: Полный радар на День X — {ton} TON (≈$19)\n\n"
      "Жми кнопку — откроется экран оплаты:\n"
      "1️⃣ Подключи кошелёк (или вставь адрес из @wallet — он запомнится)\n"
      "2️⃣ Экран проверит баланс: не хватит — напишет, сколько докинуть\n"
      "3️⃣ Жмёшь «📤 Перевести» — кошелёк открывается с готовыми суммой и адресом, ты только подтверждаешь\n\n"
      "Дальше я сам найду платёж в сети (~5 минут)."
  ),
  "pay_unavail": (
      "💳 Оплата сейчас временно недоступна (серверы отдыхают). "
      "Минут через 10 — попробуй ещё раз, или напиши в 💬 Поддержку."
  ),
  "premium_on": (
      "✅ Платёж найден в сети — полный радар включён! 📡\n\n"
      "Теперь я:\n"
      "• пишу готовый ответ под каждый новый комментарий (ты просто копируешь)\n"
      "• каждые 2 часа — сводка: позиция, голоса, комменты\n"
      "• утром — финальный отчёт + список всех, кто комментировал (твои клиенты)"
  ),
  "dayx_connected_free": (
      "✅ Радар подключён (бесплатно):\n"
      "• Запуск: {live}{live_note}\n"
      "• Сейчас: {votes} голосов · {comments} комментариев\n"
      "• Пришлю цифры примерно каждые 3 часа"
  ),
  "dayx_connected_full": (
      "✅ Радар подключён:\n"
      "• Запуск: {live}{live_note}\n"
      "• Сейчас: {votes} голосов · {comments} комментариев\n"
      "• Полный режим: ответы на комментарии, сводка каждые 2 часа, утренний отчёт"
  ),
  "cta_pay": (
      "💡 Хочешь, чтобы я делал это за тебя?\n"
      "Жми кнопку — и радар будет:\n"
      "• писать готовый ответ под каждый новый комментарий (ты просто копируешь — 10 секунд)\n"
      "• каждые 2 часа — детальная сводка\n"
      "• утром — финальный отчёт + список всех, кто комментировал (твои будущие клиенты)"
  ),
  "cta_premium_note": "✅ У тебя уже полный радар: ответы на комментарии, сводки каждые 2 часа, утренний отчёт.",
  "already_premium": (
      "✅ Полный радар уже включён.\n"
      "Чтобы подключить новый запуск — нажми «📡 День X» и пришли ссылку."
  ),
  "not_found": (
      "🔍 Этот запуск я пока не нашёл на Product Hunt.\n\n"
      "Варианты:\n"
      "• это coming-soon / запуск ещё не опубликован — пришли ссылку ещё раз, когда он станет публичным, "
      "радар подключится сам\n"
      "• ссылка не из producthunt.com\n\n"
      "Помочь: 💬 Поддержка"
  ),
  "link_only_ph": (
      "📡 Я понимаю только ссылки на запуск в Product Hunt:\n"
      "https://www.producthunt.com/posts/твой-продукт\n\n"
      "Пришли её — подключу радар."
  ),
  "not_a_link": (
      "📡 Я понимаю только ссылки на запуск в Product Hunt 🙂\n"
      "https://www.producthunt.com/posts/твой-продукт\n\n"
      "Пришли её — подключу радар и покажу цифры."
  ),
  "support_ask": (
      "💬 Напиши свой вопрос — сразу уйдёт админу (он увидит тебя и твой ID).\n"
      "Ответ придёт прямо в этот чат."
  ),
  "support_sent": "✅ Вопрос ушёл админу. Ответ придёт прямо в этот чат.",
  "support_na": "Поддержка сейчас не подключена. Напиши в канал https://t.me/ProductHuntBoosting",
  "lang_set_ru": "🇷🇺 Язык: русский. Меню ниже 👇",
  "lang_set_en": "🇬🇧 Language: English. Menu below 👇",
  "loading": "🔎 Ищу ваш запуск в официальном API Product Hunt...",
  "live_note": " — ещё не старт, сообщу в момент старта",
  "not_found_api": (
      "⚠️ Запуск виден в публичном поиске, но ещё не в официальном API (coming soon / новый) — "
      "радар подключится, когда он станет публичным. Пришли ссылку позже — подключу."
  ),
  "btn_radar": "📡 День X",
  "btn_support": "💬 Поддержка",
  "btn_pay": "💳 Полный радар — $19",
  "btn_help": "💬 Не понял — поддержка",
  "order_creating": "🧾 Создаю заказ...",
  "order_fail": "Не получилось создать заказ (сеть подзадержалась). Попробуй ещё раз через минуту — или нажми 💬 Поддержка.",
  "btn_pay_ton": "💳 Оплатить {ton} TON",
  "note_api": "(данные — официальный API Product Hunt, живые)",
  "note_live": "(данные — открытый поиск Product Hunt, живые)",
  "an_featured_short": "✅ Фичерен на PH",
  # --- диагностика (как в бесплатной проверке) ---
  "an_open": "🔍 Посмотрел: {domain}",
  "an_api": "🔍 «{name}» — официальные живые данные Product Hunt (API)",
  "an_live": "🔍 «{name}» — живые данные из открытого поиска PH",
  "an_featured": "✅ Фичерен на PH (запуск: {date})",
  "an_coming": "ℹ️ Публичный запуск ещё не состоялся (coming soon / запланирован)",
  "an_name_ok": "✅ Название на месте: «{v}»",
  "an_name_no": "⚠️ Название не нашёл — возможно, открылась не та страница",
  "an_tag_meat": "✅ Слоган «мясо» ({n} симв.): «{v}»",
  "an_tag_short": "⚠️ Слоган короткий ({n} симв.) — лучше одна конкретная выгода",
  "an_tag_no": "⚠️ Слогана не нашёл — без него продукт не продать",
  "an_video": "✅ Есть видео! Самый ценный актив на PH",
  "an_novideo": "⚠️ Видео нет — демо на 60 секунд даст самый большой прирост",
  "an_gal_full": "✅ Галерея полная ({n} изображений)",
  "an_gal_thin": "⚠️ Галерея тонкая ({n} изображений — рекомендуется 4+)",
  "an_gal_no": "⚠️ Изображений нет вовсе — 4 картинки в галерее обязательны",
  "an_gal_cover": "✅ Обложка есть (для PH нужно ещё 3–4 изображения в галерею)",
  "an_comm": "✅ Комьюнити живое: {n} комментариев",
  "an_numbers": "📊 Живые цифры: {votes} очков, {comments} комментариев",
  "an_topics": "🏷️ Категории: {topics}",
  "an_lang_en": "✅ Текст на английском — правильно для PH",
  "an_lang_other": "⚠️ Текст на другом языке — на PH нужен английский (переведём вместе)",
  "an_already": "ℹ️ Продукт уже на Product Hunt{extra}",
  "score_line": (
      "\n📊 Ваш шанс попасть в топ-5 прямо сейчас: ~{now}%\n"
      "📈 Если подготовимся вместе 6 недель: ~{prep}%\n\n{note}"
  ),
  "note_pre": ("(это предварительная оценка по странице — официальный API PH данных по продукту "
                "пока не отдаёт: продукт не фичерен / новый)"),
  # --- радар (dayx) ---
  "wx_wait": "⏳ Запуск ещё не стал публичным — продолжаю следить (каждые 2 минуты).",
  "wx_live_full": (
      "🚀 {name} — LIVE на Product Hunt (с {live} UTC).\n"
      "Радар на полном ходу: за каждый новый комментарий пришлю готовый ответ."
  ),
  "wx_live_free": (
      "🚀 {name} — LIVE на Product Hunt (с {live} UTC).\n"
      "Дальше буду присылать цифры. Для готовых ответов — «💳 Полный радар» в меню."
  ),
  "wx_comment": (
      "💬 Новый комментарий ({wt} UTC, {who}{f} подписчиков):\n"
      "“{body}”\n\n"
      "→ Ответ (долгое нажатие → копировать → вставить в поле комментария на PH):\n"
      "“{draft}”"
  ),
  "wx_summary": (
      "📊 Сводка радара:\n"
      "Голоса: {votes} · Комментариев: {comments} · Позиция дня: №{rank}"
  ),
  "wx_free_status": "📡 {name}: {votes} голосов · {comments} комментариев · Позиция дня: №{rank}",
  "wx_finish": (
      "🏁 День запуска завершён.\n"
      "Итог: {votes} голосов, {comments} комментариев, позиция №{rank}.\n"
      "Отличная работа — до новых запусков! 🚀"
  ),
},
"en": {
  "welcome": (
      "Hi! I'm Launch Pilot 📡\n\n"
      "On Day X I watch your product launch on Product Hunt: position, votes, every comment — "
      "I write the reply, you just copy it.\n\n"
      "1️⃣ Press «📡 Day X» — send me a link to your launch\n"
      "2️⃣ I show what you already have and connect the radar for free — live numbers in your chat\n"
      "3️⃣ When you're ready — turn on the full radar: replies to every comment, summaries, "
      "final report with a customer list\n\nMenu below 👇"
  ),
  "dayx_ask": (
      "📡 Day X: your launch day\n\n"
      "I watch your Product Hunt launch from 00:01 (Pacific Time) until the end of the day.\n\n"
      "To connect: send me a link to your launch:\n"
      "https://www.producthunt.com/posts/your-product\n\n"
      "What happens next:\n"
      "• I show what you already have: tagline, photos, video, numbers — and what's missing\n"
      "• I connect the radar for free: live figures (votes, comments, position) and a ping the moment your launch goes live\n"
      "• When you want — turn on the full radar: I write a ready reply for every new comment (you just copy it), "
      "a detailed summary every 2 hours, and a final report + a list of everyone who commented (your customers) in the morning\n\n"
      "Send the link 👇"
  ),
  "pay_text": (
      "🧾 Order: Full Day X Radar — {ton} TON (≈$19)\n\n"
      "Press the button — the payment screen opens:\n"
      "1️⃣ Connect your wallet (or paste your @wallet address — it will be remembered)\n"
      "2️⃣ The screen checks your balance: if it's not enough, it tells you how much to add\n"
      "3️⃣ Press «📤 Send» — your wallet opens with the amount pre-filled, you just confirm\n\n"
      "I'll find the payment on the network myself (~5 minutes)."
  ),
  "pay_unavail": (
      "💳 Payment is temporarily unavailable (servers are resting). "
      "Try again in ~10 minutes, or write to 💬 Support."
  ),
  "premium_on": (
      "✅ Payment found on the network — full radar is ON! 📡\n\n"
      "Now I:\n"
      "• write a ready reply for every new comment (you just copy it)\n"
      "• send a summary every 2 hours: position, votes, comments\n"
      "• in the morning — final report + a list of everyone who commented (your customers)"
  ),
  "dayx_connected_free": (
      "✅ Radar connected (free):\n"
      "• Launch: {live}{live_note}\n"
      "• Now: {votes} votes · {comments} comments\n"
      "• I'll send you the numbers about every 3 hours"
  ),
  "dayx_connected_full": (
      "✅ Radar connected:\n"
      "• Launch: {live}{live_note}\n"
      "• Now: {votes} votes · {comments} comments\n"
      "• Full mode: comment replies, summary every 2 hours, morning report"
  ),
  "cta_pay": (
      "💡 Want me to do it for you?\n"
      "Press the button and the radar will:\n"
      "• write a ready reply for every new comment (you just copy it — 10 seconds)\n"
      "• send a detailed summary every 2 hours\n"
      "• in the morning — final report + a list of everyone who commented (your future customers)"
  ),
  "cta_premium_note": "✅ You already have the full radar: comment replies, summary every 2 hours, morning report.",
  "already_premium": (
      "✅ The full radar is already on.\n"
      "To connect a new launch — press «📡 Day X» and send me a link."
  ),
  "not_found": (
      "🔍 I couldn't find this launch on Product Hunt yet.\n\n"
      "Options:\n"
      "• it's coming-soon / not published yet — send the link again once it goes public, "
      "the radar will connect on its own\n"
      "• the link is not from producthunt.com\n\n"
      "Help: 💬 Support"
  ),
  "link_only_ph": (
      "📡 I only understand Product Hunt launch links:\n"
      "https://www.producthunt.com/posts/your-product\n\n"
      "Send it — I'll connect the radar."
  ),
  "not_a_link": (
      "📡 I only understand Product Hunt launch links 🙂\n"
      "https://www.producthunt.com/posts/your-product\n\n"
      "Send it — I'll connect the radar and show you the numbers."
  ),
  "support_ask": (
      "💬 Write your question — it goes straight to the admin (they'll see you and your ID).\n"
      "The reply will come right in this chat."
  ),
  "support_sent": "✅ Your question is with the admin. The reply will come right in this chat.",
  "support_na": "Support is not connected right now. Write to the channel https://t.me/ProductHuntBoosting",
  "lang_set_ru": "🇷🇺 Язык: русский. Меню ниже 👇",
  "lang_set_en": "🇬🇧 Language: English. Menu below 👇",
  "loading": "🔎 Finding your launch in the official Product Hunt API...",
  "live_note": " — not live yet, I'll ping you the moment it starts",
  "not_found_api": (
      "⚠️ The launch is visible in public search, but not in the official API yet (coming soon / new) — "
      "the radar will connect once it goes public. Send me the link later — I'll connect it."
  ),
  "btn_radar": "📡 Day X",
  "btn_support": "💬 Support",
  "btn_pay": "💳 Full radar — $19",
  "btn_help": "💬 Help — support",
  "order_creating": "🧾 Creating your order...",
  "order_fail": "Couldn't create the order (network lag). Try again in a minute — or press 💬 Support.",
  "btn_pay_ton": "💳 Pay {ton} TON",
  "note_api": "(live data — official Product Hunt API)",
  "note_live": "(live data — public Product Hunt search)",
  "an_featured_short": "✅ Featured on PH",
  "an_open": "🔍 Checked: {domain}",
  "an_api": "🔍 «{name}» — live official Product Hunt data (API)",
  "an_live": "🔍 «{name}» — live data from public PH search",
  "an_featured": "✅ Featured on PH (launched: {date})",
  "an_coming": "ℹ️ Public launch hasn't happened yet (coming soon / scheduled)",
  "an_name_ok": "✅ Name is in place: «{v}»",
  "an_name_no": "⚠️ No name found — possibly the wrong page opened",
  "an_tag_meat": "✅ Tagline has substance ({n} chars): «{v}»",
  "an_tag_short": "⚠️ Tagline is short ({n} chars) — one concrete benefit is better",
  "an_tag_no": "⚠️ No tagline found — nothing sells without one",
  "an_video": "✅ There's a video! The most valuable asset on PH",
  "an_novideo": "⚠️ No video — a 60-second demo gives the biggest boost",
  "an_gal_full": "✅ Gallery is complete ({n} images)",
  "an_gal_thin": "⚠️ Gallery is thin ({n} images — 4+ recommended)",
  "an_gal_no": "⚠️ No images at all — 4 gallery images are mandatory",
  "an_gal_cover": "✅ Cover image present (3–4 more images needed for the gallery)",
  "an_comm": "✅ Community is alive: {n} comments",
  "an_numbers": "📊 Live numbers: {votes} points, {comments} comments",
  "an_topics": "🏷️ Categories: {topics}",
  "an_lang_en": "✅ Text is in English — correct for PH",
  "an_lang_other": "⚠️ Text is in another language — PH needs English (we'll translate together)",
  "an_already": "ℹ️ Product is already on Product Hunt{extra}",
  "score_line": (
      "\n📊 Your chance of top-5 right now: ~{now}%\n"
      "📈 If we prepare together for 6 weeks: ~{prep}%\n\n{note}"
  ),
  "note_pre": ("(preliminary assessment from the page — the official PH API doesn't have data "
                "on this product yet: not featured / new)"),
  "wx_wait": "⏳ The launch is not public yet — still watching (every 2 minutes).",
  "wx_live_full": (
      "🚀 {name} — LIVE on Product Hunt (since {live} UTC).\n"
      "Radar at full speed: I'll send a ready reply for every new comment."
  ),
  "wx_live_free": (
      "🚀 {name} — LIVE on Product Hunt (since {live} UTC).\n"
      "I'll keep sending the numbers. For ready replies — «💳 Full radar» in the menu."
  ),
  "wx_comment": (
      "💬 New comment ({wt} UTC, {who}{f} followers):\n"
      "“{body}”\n\n"
      "→ Reply (long-press → copy → paste into the comment field on PH):\n"
      "“{draft}”"
  ),
  "wx_summary": (
      "📊 Radar summary:\n"
      "Votes: {votes} · Comments: {comments} · Today's position: #{rank}"
  ),
  "wx_free_status": "📡 {name}: {votes} votes · {comments} comments · Today's position: #{rank}",
  "wx_finish": (
      "🏁 The launch day is over.\n"
      "Result: {votes} votes, {comments} comments, position #{rank}.\n"
      "Great work — see you at the next launch! 🚀"
  ),
},
}

def _users() -> dict:
    try:
        return json.loads(USERS_FILE.read_text())
    except Exception:
        return {}


def get_lang(uid) -> str:
    try:
        lang = _users().get(str(uid), {}).get("lang", "ru")
        return lang if lang in TEXTS else "ru"
    except Exception:
        return "ru"


def T(uid, key: str, **kw) -> str:
    """Текст для пользователя в его языке. uid не обязателен: None -> ru."""
    lang = get_lang(uid) if uid is not None else "ru"
    d = TEXTS.get(lang, TEXTS["ru"])
    s = d.get(key)
    if s is None:
        s = TEXTS["ru"].get(key, key)
    try:
        return s.format(**kw)
    except Exception:
        return s
