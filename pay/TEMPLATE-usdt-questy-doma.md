Приём оплаты USDT (сеть TON) — рабочий скрипт
Код  взят  1-в-1  из  живого  боевого  воркера  бота  «Квесты  Дома»  (проверен  логикой  сети;  свежий
срез  2026-08-28).  Адреса  заменены  заглушками  <АДРЕС_КАССЫ_UQ...>  и
<АДРЕС_USDT_MINTER_EQ...>  —  подставьте  свои.  Эмодзи  из  строковых  литералов  опущены
(PDF-шрифты), на логику это не влияет. Длинные строки перенесены меткой «»».
Как это работает (6 шагов)
1.  Сервер  создаёт  заказ:  amount  строкой  ровно  «3»/«10»  (никаких  копеечных  хвостов  —  сеть
капитализирует только целые nano) и метка верняка id.
2. Покупателю даётся ссылка /pay?o=<id> — веб-страница с TonConnect-кнопкой.
3. Страница ДО перевода проверяет балансы кошелька (нужно X USDT + ~0,12 TON на комиссию).
Недостача = красная подсказка, кнопка молчит — комиссия не сгорает впустую.
4.  JettonTransfer  собирается  TonWeb  вручную  (Cell):  сумма  в  nano,  касса-получатель,  ответ  —
комментарий-метка 'kvest:'+id.
5.  Сервер  сверяет  сеть  через  tonapi  events:  сумма==nano,  ts>создания,  получатель==касса;
опознание  по  id  в  комментарии;  фолбэк  —  ровно  один  pending  с  такой  суммой.  Анти-реплей:
event_id пишется в KV.
6.  Найдено  →  заказ  paid  →  товар  отсылается  документом;  отправка  упала  →  заказ  в  очередь,
админам автосообщение.
Присадочный чеклист (что заменить под другой чат/бот)
• Адреса кассы и USDT-MINTER (и их RAW-hex формы 0:... в константах MASTER_RAW/CASH_RAW).
• Префикс метки «kvest:» → свой (Страница и verifyUsdt должны совпасть — сверка ищет id заказа
в комментарии перевода).
• Префикс KV-ключей qord:/qtonev:/qb:/qq: → свой (TTL: заказ 3 дня, анти-реплей 14 дней).
•  NEED_TON  —  порог  комиссии  в  TON  (сейчас  0.12),  провайдеры  toncenter.com  /  tonapi.io  (можно
свои).
• Цены/каталог/текст выдачи (caption sendDocument) под свой товар.
• На своём хосте развернуть: /pay, /tonconnect-manifest.json, /api/order, /api/check.
А. Создание заказа на сервере (ровная сумма + nano + TTL)
function newOrderId() {
  return "k" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
}
async function mkOrder(env, chat, sku, title, price, items) {
  const oid = newOrderId();
  const amount = String(price);   // РОВНАЯ сумма без копеечного хвоста: 3 -> "3", 10 -> "10"
  const ord = {
    id: oid, chat, sku, title, price,
    amount, nano: String(Math.round(price * 1e6)),
    created: Date.now(), status: "pending"
  };
  if (items) ord.items = items;
  await env.KV.put("qord:" + oid, JSON.stringify(ord), { expirationTtl: 86400 * 3 });
  try { await env.KV.put("qlastord", oid); } catch (e) {}
  return ord;
А2. Сверка платежа по tonapi: считываем jetton-переводы
function evJettons(j) {
  let list = [];
  const evs = j.events || j.transfers || [];
  for (const ev of evs) {
    const eid = ev.event_id || ev.lt_and_hash || ev.hash || "";
    const ts = Number(ev.timestamp || ev.utime || 0);
    if (Array.isArray(ev.actions)) {
      for (const a of ev.actions) {
        const tr = a.JettonTransfer || a.jetton_transfer || (a.type === "JettonTransfer" ? a : null);
        if (tr) list.push({ eid, ts, tr });
      }
    } else if (ev.JettonTransfer || ev.jetton_transfer) {
      list.push({ eid, ts, tr: ev.JettonTransfer || ev.jetton_transfer });
    }
  }
  return list;
}
function hashOf(addrObj) {
  const a = typeof addrObj === "string" ? addrObj : (addrObj && addrObj.address) || "";
  return a;
}
async function verifyUsdt(env, ord) {
  const { CASH, MASTER } = cfg(env);
  const needNano = BigInt(ord.nano);
  const minTs = Math.floor(ord.created / 1000) - 300;
  let noComment = null;
  const urls = [
    "https://tonapi.io/v2/accounts/" + CASH + "/jettons/" + MASTER + "/history?limit=40",
    "https://tonapi.io/v2/accounts/" + CASH + "/events?limit=40"
  ];
  for (const u of urls) {
    try {
      const r = await fetch(u);
      if (!r.ok) continue;
      const j = await r.json();
      for (const { eid, ts, tr } of evJettons(j)) {
        const jet = hashOf(tr.jetton);
        const jetMatch = jet === MASTER || jet === MASTER_RAW || !jet;
        if (!jetMatch) continue;
        const rec = hashOf(tr.recipient);
        if (rec && rec !== CASH && rec !== CASH_RAW) continue;
        if (ts && ts < minTs) continue;
        let amt;
        try { amt = BigInt(String(tr.amount || "0")); } catch (e) { continue; }
        if (amt !== needNano) continue;
        if (eid && await env.KV.get("qtonev:" + eid)) continue;
        // главный ключ опознания — метка заказа в комментарии перевода (kvest:{oid})
        if (JSON.stringify(tr).indexOf(ord.id) !== -1) return eid || "ok" + ts;
        if (!noComment) noComment = eid || ("ok" + ts); // кандидат без метки — уйдёт в fallback
      }
    } catch (e) {}
  }
  // fallback: перевод без комментария принимаем, только если нет других pending с такой же суммой
  if (noComment) {
    try {
      let pend = 0;
      for (const k of await kvList(env, "qord:")) {
        try {
          const o = JSON.parse(await env.KV.get(k));
          if (o && o.status === "pending" && o.nano === ord.nano && o.created > Date.now() - 3600e3) pend++;
        } catch (e) {}
      }
      if (pend <= 1) return noComment;
    } catch (e) {}
  }
  return null;
}
А3.  Выдача  после  подтверждения  +  анти-накладка  (очередь  +  уведомление
админов)
async function deliverSku(env, cat, sku, chat) {
  const item = catItem(cat, sku);
  if (!item) throw new Error("no sku " + sku);
  if (!item.file_id) throw new Error("no file for " + sku);
  await tg(env, "sendDocument", {
    chat_id: chat,
    document: item.file_id,
    caption: item.title + "\n Распечатай, спрячь карточки — и праздник начался!\n Инструкция и ответы — на первой
» странице."
  });
}
async function recordSale(env, ord) {
  try {
    await env.KV.put("qsale:" + Date.now() + ":" + ord.chat, JSON.stringify({
      chat: ord.chat, sku: ord.sku, title: ord.title, amount: ord.amount, at: new Date().toISOString()
    }), { expirationTtl: 86400 * 400 });
    const st = JSON.parse((await env.KV.get("qstats")) || '{"n":0,"sum":0}');
    st.n += 1; st.sum = Math.round((st.sum + Number(ord.amount)) * 100) / 100;
    await env.KV.put("qstats", JSON.stringify(st));
  } catch (e) {}
}
async function fulfill(env, oid) {
  const ord = JSON.parse((await env.KV.get("qord:" + oid)) || "null");
  if (!ord) return { ok: false, why: "no_order" };
  if (ord.status === "paid") return { ok: true, already: true };
  if (ord.status !== "pending") return { ok: false, why: ord.status };
  const ev = await verifyUsdt(env, ord);
  if (!ev) return { ok: false, why: "not_found" };
  if (ev !== "ok") { try { await env.KV.put("qtonev:" + ev, oid, { expirationTtl: 86400 * 14 }); } catch (e) {} }
  ord.status = "paid"; ord.paidAt = Date.now(); ord.event = ev;
  await env.KV.put("qord:" + oid, JSON.stringify(ord), { expirationTtl: 86400 * 30 });
  const cat = await getCatalog(env);
  try {
    if (ord.items && ord.items.length) {
      for (const s of ord.items) await deliverSku(env, cat, s, ord.chat);
      try { await env.KV.delete("qb:" + ord.chat); } catch (e2) {}
    } else {
      await deliverSku(env, cat, ord.sku, ord.chat);
    }
  } catch (e) {
    try { await env.KV.put("qq:" + Date.now() + ":" + ord.chat, JSON.stringify(ord)); } catch (e2) {}
    await notifyAdmins(env, " Оплачено, но выдача упала (" + e.message + "). Заказ " + oid + " в очереди qq:");
    await say(env, ord.chat, "Оплата прошла  Файлы собираем и пришлём сюда в ближайшие минуты — всё под
» контролем!");
    return { ok: true, queued: true };
  }
  await say(env, ord.chat,
    " Отличного праздника! А расскажешь, как прошло? За тёплый отзыв — плюшка-квест ",
    [[{ text: " Оставить отзыв", callback_data: "rev" }, { text: " Каталог квестов", callback_data: "cat" }]]);
  await recordSale(env, ord);
  await notifyAdmins(env, " Продажа: " + ord.title + " · " + ord.amount + " USDT · чат " + ord.chat);
  return { ok: true };
}
Б. Страница оплаты /pay (TonConnect + TonWeb, гейт балансов, jetton-перевод с
меткой)
function payHtml(env) {
  const { CASH, MASTER, BASE } = cfg(env);
  return `<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cove
» r">
<title>Оплата USDT</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<script src="https://unpkg.com/@tonconnect/ui@2.0.9/dist/tonconnect-ui.min.js"></script>
<script src="https://unpkg.com/tonweb@0.0.66/dist/tonweb.js"></script>
<style>
html,body{margin:0;padding:0;width:100%;max-width:100%;overflow-x:hidden;background:#14101f;color:#f4efff;font:16px/
» 1.5 system-ui,sans-serif}
*,*::before,*::after{box-sizing:border-box}
body{position:fixed;inset:0;overflow-x:hidden;overflow-y:auto;padding:20px 20px
» 40px;-webkit-overflow-scrolling:touch}
h1{font-size:22px;margin:0 0 6px}
.muted{color:#a99ec7;font-size:14px}
.card{background:#221b38;border-radius:16px;padding:18px;margin:16px 0;max-width:100%;overflow:hidden;border:1px
» solid #37295c}
.amt{font-size:30px;font-weight:800;color:#ffd166}
button.pay{width:100%;border:0;background:#ff8c42;color:#1f1530;font-weight:800;padding:15px;border-radius:14px;font
» -size:17px;margin-top:12px}
button.pay:disabled{opacity:.45}
#connect{margin:12px 0;max-width:100%;overflow:hidden}
#connect *{max-width:100%!important}
.ok{color:#9ff0c0}.err{color:#ff9aaa}
.badge{display:inline-block;background:#37295c;color:#ffd166;border-radius:8px;padding:2px
» 10px;font-size:13px;margin-bottom:6px}
</style>
</head>
<body>
<h1> Оплата USDT</h1>
<p class="muted" id="title">…</p>
<div class="card">
  <span class="badge">сеть TON · USDT (Tether)</span>
  <div class="amt" id="amt">— USDT</div>
  <p class="muted">Сумма и метка заказа подставлены автоматически — просто подтверди перевод в кошельке <br>Перевод
» уйдёт РОВНО на столько, сколько написано выше, одним платежом.<br> Комиссия сети ≈ 0,1 TON спишется кошельком
» отдельно.<br> Перед отправкой страница сама проверит балансы USDT и TON — если чего-то не хватает, кнопка не даст
» потратить комиссию впустую.</p>
  <div id="connect"></div>
  <button class="pay" id="pay" disabled>Оплатить</button>
  <p class="muted" id="st"></p>
</div>
<p class="muted" style="font-size:12px">После оплаты PDF прилетит в чат с ботом за ~10 секунд </p>
<script>
const tg = window.Telegram && Telegram.WebApp;
if (tg) { tg.ready(); tg.expand(); try { tg.disableVerticalSwipes && tg.disableVerticalSwipes(); } catch (e) {} }
const order = new URLSearchParams(location.search).get('o') || '';
const CASH = ${JSON.stringify(CASH)};
const MASTER = ${JSON.stringify(MASTER)};
const st = document.getElementById('st');
const payBtn = document.getElementById('pay');
const connectEl = document.getElementById('connect');
let ORD = null;
async function loadOrder() {
  try {
    const r = await fetch(location.origin + '/api/order?o=' + encodeURIComponent(order));
    const j = await r.json();
    if (!j.ok) { document.getElementById('title').textContent = 'Заказ не найден или уже закрыт '; payBtn.remove();
» return; }
    ORD = j;
    document.getElementById('title').textContent = j.title;
    document.getElementById('amt').textContent = j.amount + ' USDT';
    refresh();
  } catch (e) { document.getElementById('title').textContent = 'Сеть шалит — обновите страницу'; }
}
loadOrder();
const provider = new TonWeb.HttpProvider('https://toncenter.com/api/v2/jsonRPC');
const NEED_TON = BigInt(TonWeb.utils.toNano('0.12').toString());
let BAL = { ton: null, usdt: null, jw: null };
function fmtT(bn) { try { return (Number(bn.toString()) / 1e9).toFixed(2); } catch (e) { return '?'; } }
function fmtU(bn) { try { return (Number(bn.toString()) / 1e6).toFixed(2).replace(/\.00$/, '').replace(/(\.\d)0$/,
» '$1'); } catch (e) { return '0'; } }
async function readBalances(addrStr) {
  BAL = { ton: null, usdt: null, jw: null };
  try {
    const addr = new TonWeb.utils.Address(addrStr);
    const ton = new TonWeb.utils.BN(await provider.getBalance(addr.toString(false)));
    let usdt = new TonWeb.utils.BN('0');
    let jw = null;
    try {
      const minter = new TonWeb.token.jetton.JettonMinter(provider, { address: MASTER });
      jw = await minter.getJettonWalletAddress(addr);
      const jwc = new TonWeb.token.jetton.JettonWallet(provider, { address: jw.toString(true, true, true) });
      const d = await jwc.getData();
      usdt = d.balance;
    } catch (e) { /* jetton-кошелька ещё нет = баланс 0 USDT */ }
    BAL = { ton: ton, usdt: usdt, jw: jw };
  } catch (e) { BAL = { ton: null, usdt: null, jw: null }; }
}
function refresh() {
  const w = ui.wallet;
  if (!w || !ORD) { payBtn.disabled = true; return; }
  if (!BAL || BAL.ton === null) { st.className = 'muted'; st.textContent = 'Проверяю балансы кошелька…';
» payBtn.disabled = true; return; }
  const need = BigInt(ORD.nano);
  const usdtBal = BAL.usdt ? BigInt(BAL.usdt.toString()) : 0n;
  const tonBal = BigInt(BAL.ton.toString());
  if (usdtBal < need) {
    st.className = 'err';
    st.innerHTML = 'Не хватает USDT  У тебя <b>' + fmtU(BAL.usdt) + '</b>, нужно <b>' + ORD.amount + '</b>. Пополни
» USDT в сети TON (обмен прямо в кошельке) — и жми снова. Никаких списаний не будет: кнопка заблокирована.';
    payBtn.disabled = true; return;
  }
  if (tonBal < NEED_TON) {
    st.className = 'err';
    st.innerHTML = 'USDT хватает  Но на комиссию сети нужно ~0,12 TON, а есть <b>' + fmtT(BAL.ton) + '</b> TON.
» Докинь чуть-чуть TON — и вперёд!';
    payBtn.disabled = true; return;
  }
  st.className = 'ok';
  st.textContent = 'Баланс в порядке  Жми «Оплатить» — кошелёк попросит подтвердить ровно ' + ORD.amount + ' USDT.';
  payBtn.disabled = false;
}
const ui = new TON_CONNECT_UI.TonConnectUI({ manifestUrl: location.origin + '/tonconnect-manifest.json',
» buttonRootId: 'connect' });
ui.onStatusChange(w => {
  if (!w) { BAL = { ton: null, usdt: null, jw: null }; st.className = 'muted'; st.textContent = ''; payBtn.disabled
» = true; return; }
  BAL = { ton: null, usdt: null, jw: null };
  refresh();
  readBalances(w.account.address).then(refresh).catch(refresh);
});
payBtn.onclick = async () => {
  payBtn.disabled = true;
  st.className = 'muted';
  st.textContent = 'Готовлю перевод…';
  try {
    const w = ui.wallet;
    if (!w) return;
    st.textContent = 'Проверяю балансы…';
    await readBalances(w.account.address);
    const need2 = BigInt(ORD.nano);
    const usdtBal2 = BAL.usdt ? BigInt(BAL.usdt.toString()) : 0n;
    const tonBal2 = BAL.ton ? BigInt(BAL.ton.toString()) : 0n;
    if (usdtBal2 < need2 || tonBal2 < NEED_TON) { refresh(); return; }
    const minter = new TonWeb.token.jetton.JettonMinter(provider, { address: MASTER });
    const jwAddr = BAL.jw || await minter.getJettonWalletAddress(new TonWeb.utils.Address(w.account.address));
    // тело jetton-transfer с комментарием-меткой заказа
    const cell = new TonWeb.boc.Cell();
    cell.bits.writeUint(0xf8a7ea5, 32);
    cell.bits.writeUint(Date.now(), 64);
    cell.bits.writeCoins(new TonWeb.utils.BN(String(ORD.nano)));
    cell.bits.writeAddress(new TonWeb.utils.Address(CASH));
    cell.bits.writeAddress(new TonWeb.utils.Address(w.account.address));
    cell.bits.writeBit(false);
    cell.bits.writeCoins(TonWeb.utils.toNano('0.05'));
    cell.bits.writeBit(true);
    const comment = new TonWeb.boc.Cell();
    comment.bits.writeUint(0, 32);
    comment.bits.writeString('kvest:' + order);
    cell.refs.push(comment);
    const boc = await cell.toBoc();
    const payloadB64 = TonWeb.utils.bytesToBase64(boc);
    st.textContent = 'Подтвердите в кошельке…';
    await ui.sendTransaction({
      validUntil: Math.floor(Date.now() / 1000) + 600,
      messages: [{ address: jwAddr.toString(true, true, true), amount: TonWeb.utils.toNano('0.1').toString(),
» payload: payloadB64 }]
    });
    connectEl.style.display = 'none';
    payBtn.style.display = 'none';
    st.textContent = 'Перевод улетел! Проверяю сеть… ';
    let tries = 0;
    const timer = setInterval(async () => {
      tries++;
      try {
        const r = await fetch(location.origin + '/api/check?o=' + encodeURIComponent(order));
        const j = await r.json();
        if (j.paid) {
          clearInterval(timer);
          st.textContent = ' Оплачено! Квест уже летит в чат с ботом ';
          st.className = 'ok';
          if (tg) setTimeout(() => tg.close(), 1600);
          return;
        }
      } catch (e) {}
      if (tries > 40) {
        clearInterval(timer);
        st.textContent = 'Сеть думает дольше обычного. Откройте бота и нажмите «Я оплатил — проверить» ';
      } else if (tries % 5 === 0) { st.textContent = 'Ищу перевод в сети… (' + Math.min(tries*4, 120) + ' сек)'; }
    }, 4000);
  } catch (e) {
    st.textContent = 'Отменено или отклонено кошельком. Можно попробовать ещё раз ';
    st.className = 'err';
    payBtn.disabled = false;
  }
};
</script>
</body>
</html>`;
}
В. API для страницы: выдать заказ и принять «проверить оплату»
async function apiOrder(env, url) {
  const oid = url.searchParams.get("o") || "";
  const ord = JSON.parse((await env.KV.get("qord:" + oid)) || "null");
  if (!ord || ord.status !== "pending") return json({ ok: false });
  return json({ ok: true, title: ord.title, amount: ord.amount, nano: ord.nano, sku: ord.sku });
}
async function apiCheck(env, url) {
  const oid = url.searchParams.get("o") || "";
  const r = await fulfill(env, oid);
  const ord = JSON.parse((await env.KV.get("qord:" + oid)) || "null");
  return json({ ok: true, paid: !!(ord && ord.status === "paid") });
}