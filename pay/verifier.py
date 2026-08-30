#!/usr/bin/env python3
"""
Бухгалтер (pay-verifier). Запускается GitHub Actions каждые 3 минуты.

Что делает:
  1. Читает state/orders.json (заказы создаёт бот, когда клиент жмёт «💳 Оплатить»).
  2. Спрашивает toncenter историю входящих переводов кассы.
  3. Находит перевод: сумма == nano заказа И метка «lp:<id>» в комментарии
     (fallback: точная сумма + единственный заказ с такой суммой).
  4. Помечает заказ paid, пишет это обратно в orders.json,
     шлёт клиенту «оплата подтверждена» и админу «продажа».
  5. Логит в state/pay-verifier-log.txt (в т.ч. сырой in_msg — для отладки формата метки).

Товар (включение радара) выдаёт сам бот: он видит paid в orders.json и включает премиум.
"""
import base64
import json
import os
import time
import urllib.error
import urllib.request

REPO = os.environ["GITHUB_REPOSITORY"]
GH_TOKEN = os.environ["GITHUB_TOKEN"]
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
BRANCH = "state"
CASH = "UQDL_6KhMWWJo7-25Nl8mCkdQZgiyNdgx8PofVZcHESDVwUk"
BASE = f"https://api.github.com/repos/{REPO}/contents/state"
TTL_MS = 3 * 86400 * 1000  # заказ живёт 3 дня
ORDER_PREFIX = "lp:"


def http(url, data=None, token=None):
    req = urllib.request.Request(url)
    headers = {"Content-Type": "application/json", "User-Agent": "launchpilot-verifier"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data is not None else None
    try:
        with urllib.request.urlopen(req, data=body, timeout=30) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:  # noqa: BLE001
        return -1, str(e)


def gh_read(path):
    """-> (sha, text) или (None, None)"""
    st, body = http(f"{BASE}/{path}?ref={BRANCH}", token=GH_TOKEN)
    if st != 200:
        return None, None
    try:
        j = json.loads(body)
        return j.get("sha"), base64.b64decode(j.get("content", "")).decode()
    except Exception:  # noqa: BLE001
        return None, None


def gh_write(path, text, sha, msg):
    payload = {
        "message": msg,
        "content": base64.b64encode(text.encode()).decode(),
        "branch": BRANCH,
    }
    if sha:
        payload["sha"] = sha
    st, body = http(f"{BASE}/{path}", payload, token=GH_TOKEN)
    if st not in (200, 201):
        print(f"WRITE FAIL {path}: {st} {body[:200]}")
        return False
    return True


def tg_msg(chat_id, text):
    if not BOT_TOKEN:
        return False
    st, body = http(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        {"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
    )
    if st != 200:
        print(f"TG FAIL {chat_id}: {st} {body[:200]}")
        return False
    return True


def toncenter_txs(limit=60):
    st, body = http("https://toncenter.com/api/v2/getTransactions",
                    {"address": CASH, "limit": limit})
    if st != 200:
        raise RuntimeError(f"toncenter {st}: {body[:200]}")
    j = json.loads(body)
    return j.get("result") or []


def comment_of(in_msg):
    """Текстовая метка перевода, если toncenter её расшифровал (decoded_body)."""
    d = in_msg.get("decoded_body")
    if isinstance(d, str) and d:
        return d
    return ""


def find_admin():
    """Админ из state/users.json (первый с admin=true, иначе первый пользователь)."""
    _, text = gh_read("users.json")
    if not text:
        return None
    try:
        users = json.loads(text)
    except Exception:  # noqa: BLE001
        return None
    for uid, e in users.items():
        if isinstance(e, dict) and e.get("admin"):
            return int(uid)
    for uid in users:
        try:
            return int(uid)
        except Exception:  # noqa: BLE001
            continue
    return None


def main():
    sha, text = gh_read("orders.json")
    if text:
        try:
            doc = json.loads(text)
        except Exception:  # noqa: BLE001
            doc = {"meta": {"used_tx": []}, "orders": []}
    else:
        doc = {"meta": {"used_tx": []}, "orders": []}
    doc.setdefault("meta", {"used_tx": []})
    orders = doc.get("orders", [])

    now = time.time()
    now_ms = int(now * 1000)
    used = set(doc["meta"].get("used_tx", []))
    changed = False
    debug = []

    # просроченные заказы
    for o in orders:
        if o.get("status") == "pending" and o.get("created", 0) + TTL_MS < now_ms:
            o["status"] = "expired"
            changed = True
            debug.append(f"EXPIRED: {o.get('id')}")

    pending = [o for o in orders if o.get("status") == "pending"]
    txs = []
    if pending:
        txs = toncenter_txs(60)
        for tx in txs[:3]:
            if tx.get("in_msg"):
                debug.append("TX-DUMP: " + json.dumps(
                    {"hash": tx.get("hash"), "utime": tx.get("utime"),
                     "in_msg": tx["in_msg"]}, ensure_ascii=False)[:1500])

        by_nano = {}
        for o in pending:
            by_nano.setdefault(str(o.get("nano", "")), []).append(o)

        for tx in txs:
            im = tx.get("in_msg")
            if not im:
                continue
            txhash = tx.get("hash", "")
            if txhash in used:
                continue
            value = str(im.get("value", ""))
            utime = tx.get("utime", 0)
            # кандидаты: только ещё pending (одна tx не оплатит два заказа)
            cands = [c for c in by_nano.get(value, []) if c.get("status") == "pending"]
            if not cands:
                continue
            if not utime or min(c["created"] for c in cands) / 1000 - 600 > utime:
                continue
            cmt = comment_of(im)
            target = None
            if cmt:
                for c in cands:
                    tag = ORDER_PREFIX + str(c.get("id", ""))
                    if utime >= c.get("created", 0) / 1000 - 300 and tag in cmt:
                        target = c
                        break
            elif len(cands) == 1:
                # без метки: только если с такой суммой заказ один
                if utime >= cands[0].get("created", 0) / 1000 - 300:
                    target = cands[0]
                    debug.append(f"NO-TAG-FALLBACK: tx {txhash} -> {target.get('id')}")
            if target is None:
                debug.append(f"AMBIGUOUS: tx {txhash} nano={value} cands={len(cands)} — ждёт админ")
                continue

            target["status"] = "paid"
            target["paid_at"] = int(now)
            target["tx"] = txhash
            used.add(txhash)
            changed = True
            chat = target.get("chat")
            title = target.get("title", "Радар")
            ton = target.get("ton", "?")
            usd = target.get("usd", "?")
            user = target.get("user", "?")
            debug.append(f"PAID: {target.get('id')} tx={txhash} chat={chat}")
            if chat:
                tg_msg(chat, "✅ Платёж подтверждён! Радар включается — через минуту-две увидишь его прямо в этом чате.")
            admin = find_admin()
            if admin:
                tg_msg(admin, f"💰 Продажа: {title} · {ton} TON (${usd})\nКлиент: {user} (чат {chat})\nЗаказ: {target.get('id')}")

    if changed:
        doc["meta"]["used_tx"] = sorted(used)[-500:]
        gh_write("orders.json", json.dumps(doc, ensure_ascii=False, indent=1), sha, "pay: orders update")

    log_sha, log_text = gh_read("pay-verifier-log.txt")
    line = (f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S UTC')} --- "
            f"pending={len(pending)} txs={len(txs)} changed={changed}\n")
    for d in debug:
        line += d + "\n"
    new_log = ((log_text or "")[-6000:]) + line
    gh_write("pay-verifier-log.txt", new_log, log_sha, "pay: verifier log")
    print(f"verifier ok: pending={len(pending)} txs={len(txs)} changed={changed}")


if __name__ == "__main__":
    main()
