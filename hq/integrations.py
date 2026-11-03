"""
Центр (этап 8): интеграции.

Каждая интеграция — отдельный адаптер с настройками, проверкой подключения, журналом обмена,
обработкой ошибок и повторной отправкой (очередь заданий с нарастающей паузой: 1 мин → 5 мин → 30 мин → 2 ч → 6 ч).

Состояния:
  active    — работает;
  off       — настроена, но выключена;
  inactive  — нужен ключ / токен (интерфейс готов, обмена нет);
  prepared  — нужна документация и доступ от сервиса: настройки сохраняются, обмен НЕ имитируется;
  builtin   — работает внутри системы (Excel, ОФД через кассы, Payme/Click на кассах).

Реально работающие обмены: Telegram (Bot API), Google Sheets (=IMPORTDATA по ссылке с ключом),
1С — выгрузка CommerceML и проверка OData-подключения, Claude (AI-помощник) — по ключу API.
"""
import io
import json
import secrets
import threading
import time
import uuid
import zipfile
from datetime import datetime, timedelta
from xml.sax.saxutils import escape as xe

import requests
from flask import Blueprint, Response, g, jsonify, request

bp = Blueprint("integrations", __name__)
H = None
F = "%Y-%m-%d %H:%M:%S"
MASK = "••••••"
BACKOFF = [60, 300, 1800, 7200, 21600]
LOCK = threading.Lock()
import os  # noqa: E402
TG_BASE = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")      # переопределяется только в тестах
AN_BASE = os.environ.get("ANTHROPIC_API_BASE", "https://api.anthropic.com").rstrip("/")
SCHEMA = """
CREATE TABLE IF NOT EXISTS integrations (
  key TEXT PRIMARY KEY, enabled INTEGER DEFAULT 0, settings TEXT, last_ok TEXT, last_error TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS integration_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, kind TEXT NOT NULL, title TEXT, payload TEXT,
  status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER DEFAULT 0, next_at TEXT, created_at TEXT, done_at TEXT,
  response TEXT, error TEXT);
CREATE INDEX IF NOT EXISTS ix_ij_due ON integration_jobs(status, next_at);
CREATE INDEX IF NOT EXISTS ix_ij_key ON integration_jobs(key, id);
"""


class NotReady(Exception):
    pass


# ================================================================ адаптеры
class Adapter:
    key = title = desc = ""
    fields = []            # (имя, подпись, секрет?, подсказка)
    kind = "inactive"      # что показывать без настроек: inactive | prepared | builtin
    need = ""              # что нужно для подключения
    implemented = True     # есть ли реальный обмен

    def ready(self, cfg):
        miss = [lbl for name, lbl, sec, _ in self.fields if sec != "opt" and not (cfg.get(name) or "").strip()]
        return (not miss, "Не заполнено: " + ", ".join(miss) if miss else "")

    def test(self, cfg):
        raise NotReady("Проверка подключения для этого сервиса появится вместе с документацией API")

    def send(self, kind, payload, cfg):
        raise NotReady("Обмен не реализован")


class Telegram(Adapter):
    key, title = "telegram", "Telegram-бот"
    desc = "Ежедневный отчёт по сети, перемещения на согласование, завершённые перемещения и тревоги — владельцу или в группу руководителей."
    fields = [("bot_token", "Токен бота (@BotFather)", True, "123456:ABC…"), ("chat_id", "Chat ID (личный или группы)", False, "-100…"),
              ("daily_hour", "Час ежедневного отчёта (0–23)", "opt", "21")]
    need = "Создайте бота у @BotFather, добавьте его в чат и напишите /start. Chat ID можно узнать кнопкой «Найти chat_id»."
    events = [("daily", "Ежедневный отчёт по сети"), ("transfer_pending", "Перемещение ждёт согласования"),
              ("transfer_done", "Перемещение принято"), ("alerts", "Тревоги: нет связи с кассой, нулевые остатки")]

    def _url(self, cfg, method):
        return f"{TG_BASE}/bot{cfg['bot_token'].strip()}/{method}"

    def test(self, cfg):
        r = requests.get(self._url(cfg, "getMe"), timeout=15).json()
        if not r.get("ok"):
            raise RuntimeError(r.get("description") or "Telegram не принял токен")
        if (cfg.get("chat_id") or "").strip():
            self.send("text", {"text": "✅ Центр сети подключён. Сюда будут приходить отчёты и уведомления."}, cfg)
        return f"Бот @{r['result'].get('username')} на связи"

    def find_chat(self, cfg):
        r = requests.get(self._url(cfg, "getUpdates"), timeout=15).json()
        if not r.get("ok"):
            raise RuntimeError(r.get("description") or "Telegram не принял токен")
        chats = {}
        for u in r.get("result", []):
            ch = (u.get("message") or u.get("my_chat_member") or u.get("channel_post") or {}).get("chat")
            if ch:
                chats[str(ch["id"])] = ch.get("title") or " ".join(x for x in (ch.get("first_name"), ch.get("last_name")) if x) or ch.get("username") or ""
        return [{"id": k, "name": v} for k, v in chats.items()]

    def send(self, kind, payload, cfg):
        r = requests.post(self._url(cfg, "sendMessage"), json={"chat_id": cfg["chat_id"].strip(), "text": payload["text"][:4000],
                                                               "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=20)
        js = r.json()
        if not js.get("ok"):
            raise RuntimeError(js.get("description") or f"HTTP {r.status_code}")
        return f"message_id {js['result']['message_id']}"


class Sheets(Adapter):
    key, title = "sheets", "Google Sheets"
    desc = "Живые таблицы для владельца: продажи по дням, остатки, каталог, планы. В Google Таблице — формула =IMPORTDATA(ссылка), данные обновляются сами."
    fields = []
    kind = "off"
    need = "Включите — появятся ссылки с секретным ключом. Ссылку можно отозвать, выпустив новый ключ."

    def ready(self, cfg):
        return True, ""

    def test(self, cfg):
        return "Ссылки работают — вставьте их в Google Таблицу"


class OneC(Adapter):
    key, title = "1c", "1С:Предприятие"
    desc = "Выгрузка каталога, цен и остатков по магазинам в формате CommerceML 2 (обмен с сайтом / УТ / Розница). Проверка подключения к 1С через стандартный OData-интерфейс."
    fields = [("odata_url", "Адрес OData (…/odata/standard.odata)", False, "https://1c.company.uz/base/odata/standard.odata"),
              ("login", "Пользователь 1С", False, ""), ("password", "Пароль", True, "")]
    need = ("Выгрузка CommerceML работает сразу. Для автоматического обмена в 1С нужно опубликовать OData-интерфейс на веб-сервере и выдать пользователя; "
            "загрузка документов из 1С включается после сопоставления полей вашей конфигурации.")

    def test(self, cfg):
        url = cfg["odata_url"].strip().rstrip("/") + "/$metadata"
        r = requests.get(url, auth=(cfg["login"], cfg["password"]), timeout=20)
        if r.status_code == 401:
            raise RuntimeError("1С не приняла логин или пароль")
        if not r.ok:
            raise RuntimeError(f"1С ответила HTTP {r.status_code}")
        n = r.text.count("<EntityType")
        has = "Catalog_Номенклатура" in r.text
        return f"Подключено: {n} объектов в OData" + ("" if has else " (справочник «Номенклатура» не опубликован — добавьте его в состав OData)")


class Didox(Adapter):
    key, title = "didox", "Didox (ЭДО)"
    desc = "Электронные счета-фактуры и накладные с поставщиками: входящие документы → поставки, исходящие при перемещениях между юрлицами."
    fields = [("inn", "ИНН организации", False, ""), ("login", "Логин API", False, ""), ("password", "Пароль / ключ API", True, "")]
    kind, implemented = "prepared", False
    need = "Нужны договор на API с Didox, документация и тестовый доступ. Обмен включится после проверки на тестовом контуре — до этого документы не отправляются."


class Tasnif(Adapter):
    key, title = "tasnif", "Tasnif (ИКПУ)"
    desc = "Контроль кодов ИКПУ в каталоге: товары без кода и с неверным форматом (нужно 17 цифр) — без них касса не пробьёт фискальный чек."
    fields = [("api_key", "Ключ API Tasnif (если выдан)", True, "")]
    kind, implemented = "prepared", False
    need = "Проверка формата и поиск товаров без ИКПУ работают сразу. Автоподбор кода из классификатора — после получения доступа к API tasnif.soliq.uz."


class Uzum(Adapter):
    key, title = "uzum", "Uzum Market"
    desc = "Остатки и цены для витрины на Uzum Market из выбранного магазина-склада; заказы маркетплейса — в продажи."
    fields = [("token", "Токен Seller API", True, ""), ("shop_id", "ID магазина на Uzum", False, ""), ("store_id", "Магазин сети, откуда отгружаем", "opt", "")]
    kind, implemented = "prepared", False
    need = "Нужен токен Seller API из кабинета продавца Uzum и сопоставление товаров (SKU). Выгрузка остатков включится после проверки на вашем кабинете — сейчас доступен предпросмотр."


class Claude(Adapter):
    key, title = "claude", "Claude (AI-помощник)"
    desc = "Помощник отвечает на свободные вопросы своими словами по реальным данным сети. Без ключа помощник работает на встроенных расчётах."
    fields = [("api_key", "Ключ Anthropic API", True, "sk-ant-…"), ("model", "Модель", "opt", "claude-sonnet-5-5")]
    need = "Ключ создаётся в console.anthropic.com. Данные сети уходят в запрос только в виде сводных цифр по вашему вопросу."

    def test(self, cfg):
        return "Ответ модели: " + ask_claude(cfg, "Ответь одним словом: готов?", "", max_tokens=10)


class Builtin(Adapter):
    kind = "builtin"

    def ready(self, cfg):
        return True, ""


class Excel(Builtin):
    key, title = "excel", "Excel"
    desc = "Выгрузка любого отчёта в Excel и PDF; загрузка каталога, остатков, клиентов и истории продаж из Excel / 1С / BILLZ / МойСклад (Переезд данных)."


class Payments(Builtin):
    key, title = "payments", "Payme / Click / Uzum QR"
    desc = "QR-оплата на кассах с выбором провайдера; у каждого магазина свой мерчант. Ключи вводятся на кассе: Настройки → Платёжные системы. Автопроверка оплаты — после выдачи ключей провайдером."


class OFD(Builtin):
    key, title = "ofd", "ОФД / фискальный модуль"
    desc = "Фискализация каждого чека на кассе через FiscalDriveService (ОФД). Здесь видно, где остались чеки без фискализации."


ADAPTERS = {a.key: a for a in (Telegram(), Sheets(), OneC(), Excel(), Payments(), OFD(), Didox(), Tasnif(), Uzum(), Claude())}


def ask_claude(cfg, question, context, max_tokens=900, system=None):
    r = requests.post(f"{AN_BASE}/v1/messages", timeout=60, headers={
        "x-api-key": cfg["api_key"].strip(), "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": (cfg.get("model") or "").strip() or "claude-sonnet-5-5", "max_tokens": max_tokens,
              "system": system or "Ты помощник. Отвечай кратко.",
              "messages": [{"role": "user", "content": (f"Данные сети (JSON):\n{context}\n\n" if context else "") + f"Вопрос: {question}"}]})
    js = r.json()
    if r.status_code >= 400:
        raise RuntimeError((js.get("error") or {}).get("message") or f"HTTP {r.status_code}")
    return "".join(b.get("text", "") for b in js.get("content", []) if b.get("type") == "text").strip()


# ================================================================ хранение
def cfg_of(key, c=None):
    row = (c.execute("SELECT * FROM integrations WHERE key=?", (key,)).fetchone() if c else H.q("SELECT * FROM integrations WHERE key=?", (key,), one=True))
    row = dict(row) if row else {"key": key, "enabled": 0, "settings": "{}", "last_ok": None, "last_error": None}
    row["cfg"] = json.loads(row.get("settings") or "{}")
    return row


def state(a, row):
    if a.kind == "builtin":
        return "builtin"
    ok, why = a.ready(row["cfg"])
    if not a.implemented:
        return "prepared"
    if not ok:
        return "inactive"
    return "active" if row["enabled"] else "off"


def active(key, c=None):
    a = ADAPTERS[key]
    row = cfg_of(key, c)
    return row["cfg"] if state(a, row) == "active" else None


def enqueue(key, kind, title, payload, c=None):
    """Поставить задание в очередь (если интеграция активна). Вызывается внутри транзакции бизнес-операции."""
    if not active(key, c):
        return False
    own = c is None
    c = c or H.con()
    t = H.now()
    c.execute("INSERT INTO integration_jobs(key,kind,title,payload,status,attempts,next_at,created_at) VALUES(?,?,?,?,'queued',0,?,?)",
              (key, kind, title, json.dumps(payload, ensure_ascii=False), t, t))
    if own:
        c.commit()
        c.close()
    return True


def run_due(limit=20):
    """Отправка заданий из очереди. Ошибка → повтор с нарастающей паузой; после 6 попыток — «не доставлено», можно повторить вручную."""
    if not LOCK.acquire(blocking=False):
        return 0
    try:
        now = H.now()
        jobs = H.q("SELECT * FROM integration_jobs WHERE status IN ('queued','retry') AND next_at<=? ORDER BY id LIMIT ?", (now, limit))
        n = 0
        for j in jobs:
            a = ADAPTERS.get(j["key"])
            row = cfg_of(j["key"])
            c = H.con()
            try:
                if not a or state(a, row) != "active":
                    raise NotReady("Интеграция выключена или не настроена")
                resp = a.send(j["kind"], json.loads(j["payload"] or "{}"), row["cfg"])
                c.execute("UPDATE integration_jobs SET status='done', attempts=attempts+1, done_at=?, response=?, error=NULL WHERE id=?",
                          (H.now(), str(resp)[:500], j["id"]))
                c.execute("UPDATE integrations SET last_ok=?, last_error=NULL WHERE key=?", (H.now(), j["key"]))
            except Exception as e:
                att = (j["attempts"] or 0) + 1
                dead = att > len(BACKOFF) or isinstance(e, NotReady)
                nxt = (H.tnow() + timedelta(seconds=BACKOFF[min(att - 1, len(BACKOFF) - 1)])).strftime(F)
                msg = f"{e.__class__.__name__}: {e}" if not isinstance(e, (RuntimeError, NotReady)) else str(e)
                c.execute("UPDATE integration_jobs SET status=?, attempts=?, next_at=?, error=? WHERE id=?",
                          ("dead" if dead else "retry", att, nxt, msg[:500], j["id"]))
                c.execute("UPDATE integrations SET last_error=? WHERE key=?", (f"{H.now()[:16]} {msg}"[:300], j["key"]))
            c.commit()
            c.close()
            n += 1
        return n
    finally:
        LOCK.release()


# ================================================================ Telegram: события и ежедневный отчёт
def fmt(v):
    return f"{round(v or 0):,}".replace(",", " ")


def tg_enabled(event):
    cfg = active("telegram")
    return cfg if cfg and event in (cfg.get("events") or ["daily", "transfer_pending", "transfer_done", "alerts"]) else None


def on_transfer_event(c, uid, status):
    if status not in ("pending", "completed", "received"):
        return
    if status == "received" and not H.transfers.has_discrepancy(c, uid):
        return          # без расхождений сразу придёт «завершено»
    ev = "transfer_pending" if status == "pending" else "transfer_done"
    if not tg_enabled(ev):
        return
    d = c.execute("""SELECT d.number, fs.name fr, ts.name tt, (SELECT COUNT(*) FROM doc_lines l WHERE l.doc_uid=d.uid) n,
                     (SELECT COALESCE(SUM(qty),0) FROM doc_lines l WHERE l.doc_uid=d.uid) q FROM docs d
                     LEFT JOIN stores fs ON fs.id=d.from_store_id LEFT JOIN stores ts ON ts.id=d.to_store_id WHERE d.uid=?""", (uid,)).fetchone()
    if not d or not d["fr"]:
        return
    head = {"pending": "🟡 Перемещение ждёт согласования", "received": "📦 Перемещение принято с расхождением", "completed": "✅ Перемещение завершено"}[status]
    enqueue("telegram", "text", f"{head}: {d['number']}",
            {"text": f"<b>{head}</b>\n{xe(d['number'] or '')}: {xe(d['fr'])} → {xe(d['tt'])}\nПозиций: {d['n']}, всего {d['q']:g} ед."}, c)


def daily_report():
    now = H.tnow()
    d0 = now.strftime("%Y-%m-%d")
    rows = H.q("""SELECT st.name, COALESCE(SUM(CASE WHEN s.operation='sale' THEN s.total ELSE -s.total END),0) net,
                  COUNT(CASE WHEN s.operation='sale' THEN 1 END) checks FROM stores st
                  LEFT JOIN sales s ON s.store_id=st.id AND s.created_at BETWEEN ? AND ? WHERE st.active=1 GROUP BY st.name ORDER BY net DESC""",
               (d0 + " 00:00:00", d0 + " 23:59:59"))
    tot = sum(r["net"] for r in rows)
    ch = sum(r["checks"] for r in rows)
    lines = [f"<b>📊 Итоги дня {now.strftime('%d.%m.%Y')}</b>", f"Выручка сети: <b>{fmt(tot)}</b> · чеков {ch}" + (f" · ср. чек {fmt(tot / ch)}" if ch else ""), ""]
    lines += [f"{i}. {xe(r['name'])} — {fmt(r['net'])} ({r['checks']})" for i, r in enumerate(rows, 1)]
    ym = now.strftime("%Y-%m")
    plans = H.q("""SELECT st.name, p.amount, (SELECT COALESCE(SUM(CASE WHEN s.operation='sale' THEN s.total ELSE -s.total END),0) FROM sales s
                   WHERE s.store_id=st.id AND s.created_at>=?) fact FROM store_plans p JOIN stores st ON st.id=p.store_id WHERE p.month=? AND p.amount>0""",
                (ym + "-01 00:00:00", ym))
    if plans:
        import calendar
        last = calendar.monthrange(now.year, now.month)[1]
        lines += ["", "<b>План месяца</b> (прогноз)"]
        for p in plans:
            fc = p["fact"] / now.day * last
            lines.append(f"{'✅' if fc >= p['amount'] else '⚠️'} {xe(p['name'])}: {round(p['fact'] / p['amount'] * 100)}% → {round(fc / p['amount'] * 100)}%")
    return "\n".join(lines)


def tick_daily():
    cfg = tg_enabled("daily")
    if not cfg:
        return
    now = H.tnow()
    hour = int(H.num(cfg.get("daily_hour"), 21))
    row = cfg_of("telegram")
    if now.hour < hour or row["cfg"].get("daily_sent") == now.strftime("%Y-%m-%d"):
        return
    with H.app.test_request_context():
        g.user = {"id": 0, "name": "система", "role": "superadmin", "perms": sorted(H.rbac._ALL), "stores": None}
        text = daily_report()
    row["cfg"]["daily_sent"] = now.strftime("%Y-%m-%d")
    c = H.con()
    c.execute("UPDATE integrations SET settings=? WHERE key='telegram'", (json.dumps(row["cfg"], ensure_ascii=False),))
    enqueue("telegram", "text", "Ежедневный отчёт", {"text": text}, c)
    c.commit()
    c.close()


def tick_alerts():
    """Раз в час: кассы без связи больше 30 минут (одно сообщение на магазин в сутки)."""
    cfg = tg_enabled("alerts")
    if not cfg:
        return
    now = H.tnow()
    row = cfg_of("telegram")
    sent = row["cfg"].get("alert_sent") or {}
    new = []
    for s in H.q("SELECT id, name, last_seen FROM stores WHERE active=1 AND last_seen IS NOT NULL"):
        try:
            gap = (now - datetime.strptime(s["last_seen"][:19], F)).total_seconds() / 60
        except ValueError:
            continue
        key = f"off{s['id']}"
        if gap > 30 and 7 <= now.hour <= 23 and sent.get(key) != now.strftime("%Y-%m-%d"):
            new.append(f"🔴 {xe(s['name'])}: нет связи с кассой {int(gap // 60)} ч {int(gap % 60)} мин")
            sent[key] = now.strftime("%Y-%m-%d")
    if new:
        row["cfg"]["alert_sent"] = sent
        c = H.con()
        c.execute("UPDATE integrations SET settings=? WHERE key='telegram'", (json.dumps(row["cfg"], ensure_ascii=False),))
        enqueue("telegram", "text", "Тревога: касса без связи", {"text": "<b>Тревога</b>\n" + "\n".join(new)}, c)
        c.commit()
        c.close()


def worker():
    last_alert = 0
    while True:
        try:
            tick_daily()
            if time.time() - last_alert > 3600:
                tick_alerts()
                last_alert = time.time()
            run_due()
        except Exception as e:      # фоновый поток не должен падать
            print("integrations worker:", e)
        time.sleep(20)


# ================================================================ API
def public(a):
    row = cfg_of(a.key)
    cfg = dict(row["cfg"])
    for name, _, sec, _ in a.fields:
        if sec is True and cfg.get(name):
            cfg[name] = MASK
    for k in ("daily_sent", "alert_sent", "feed_token"):
        cfg.pop(k, None)
    st = state(a, row)
    if st == "active" and row.get("last_error") and (not row.get("last_ok") or row["last_error"][:16] >= row["last_ok"][:16]):
        st = "error"       # последняя проверка или отправка не прошла
    ok, why = a.ready(row["cfg"])
    out = {"key": a.key, "title": a.title, "desc": a.desc, "need": a.need, "state": st, "enabled": bool(row["enabled"]), "why": why,
           "fields": [{"name": n, "label": l, "secret": s is True, "optional": s == "opt", "ph": p} for n, l, s, p in a.fields],
           "settings": cfg, "last_ok": row.get("last_ok"), "last_error": row.get("last_error"), "implemented": a.implemented,
           "can_test": type(a).test is not Adapter.test and a.kind != "builtin"}
    if a.key == "telegram":
        out["events"] = [{"key": k, "title": t, "on": k in (row["cfg"].get("events") or [x for x, _ in Telegram.events])} for k, t in Telegram.events]
    if a.key == "sheets" and row["enabled"]:
        tok = row["cfg"].get("feed_token")
        out["feeds"] = [{"name": n, "title": t, "url": f"{request.host_url.rstrip('/')}/feed/{tok}/{n}.csv"} for n, t in FEEDS.items()] if tok else []
    st_info = None
    if a.key in ("ofd", "payments"):
        st_info = []
        fi, fia = H.rbac.sf("id")
        for s in H.q(f"SELECT name, info FROM stores WHERE active=1{fi} ORDER BY id", fia):
            i = json.loads(s["info"] or "{}")
            st_info.append({"name": s["name"], "unsent": i.get("unsent_fiscal"), "seen": bool(i)})
        out["stores"] = st_info
    if a.key == "tasnif":
        bad = tasnif_check()
        out["check"] = {"missing": len(bad["missing"]), "invalid": len(bad["invalid"])}
    return out


@bp.get("/api/integrations")
def int_list():
    stats = {r["key"]: r for r in H.q("""SELECT key, COUNT(*) n, COUNT(CASE WHEN status='done' THEN 1 END) done,
                                         COUNT(CASE WHEN status IN ('queued','retry') THEN 1 END) waiting, COUNT(CASE WHEN status='dead' THEN 1 END) dead
                                         FROM integration_jobs GROUP BY key""")}
    items = []
    for a in ADAPTERS.values():
        x = public(a)
        x["jobs"] = stats.get(a.key, {"n": 0, "done": 0, "waiting": 0, "dead": 0})
        items.append(x)
    return jsonify(items=items)


@bp.post("/api/integrations/<key>")
def int_save(key):
    a = ADAPTERS.get(key)
    if not a or a.kind == "builtin":
        return H.err("Неизвестная интеграция", 404)
    d = request.json or {}
    row = cfg_of(key)
    cfg = dict(row["cfg"])
    for name, _, sec, _ in a.fields:
        if name in (d.get("settings") or {}):
            v = str(d["settings"][name] or "").strip()
            if sec is True and v == MASK:
                continue
            cfg[name] = v
    if key == "telegram" and "events" in d:
        cfg["events"] = [e for e in d["events"] if e in dict(Telegram.events)]
    enabled = 1 if d.get("enabled") else 0
    if key == "sheets" and enabled and not cfg.get("feed_token"):
        cfg["feed_token"] = secrets.token_urlsafe(18)
    if key == "sheets" and d.get("new_token"):
        cfg["feed_token"] = secrets.token_urlsafe(18)
    ok, why = a.ready(cfg)
    if enabled and not a.implemented:
        enabled = 0
    t = H.now()
    c = H.con()
    if c.execute("SELECT 1 FROM integrations WHERE key=?", (key,)).fetchone():
        c.execute("UPDATE integrations SET enabled=?, settings=?, updated_at=? WHERE key=?", (enabled, json.dumps(cfg, ensure_ascii=False), t, key))
    else:
        c.execute("INSERT INTO integrations(key,enabled,settings,updated_at) VALUES(?,?,?,?)", (key, enabled, json.dumps(cfg, ensure_ascii=False), t))
    H.rbac.audit("Интеграция: настройки", f"{a.title}: {'включена' if enabled else 'выключена'}" + (f" ({why})" if enabled and not ok else "")
                 + (" · новый ключ ссылок" if d.get("new_token") else ""), None, c=c)
    c.commit()
    c.close()
    return jsonify(public(a))


@bp.post("/api/integrations/<key>/test")
def int_test(key):
    a = ADAPTERS.get(key)
    if not a:
        return H.err("Неизвестная интеграция", 404)
    row = cfg_of(key)
    ok, why = a.ready(row["cfg"])
    if not ok:
        return H.err(why)
    c = H.con()
    try:
        msg = a.test(row["cfg"])
        c.execute("UPDATE integrations SET last_ok=?, last_error=NULL WHERE key=?", (H.now(), key))
        res = jsonify(ok=True, message=msg)
    except NotReady as e:
        res = jsonify(ok=False, message=str(e))
    except Exception as e:
        msg = str(e) if isinstance(e, RuntimeError) else f"Нет связи: {e.__class__.__name__}"
        c.execute("UPDATE integrations SET last_error=? WHERE key=?", (f"{H.now()[:16]} {msg}"[:300], key))
        res = jsonify(ok=False, message=msg)
    c.commit()
    c.close()
    return res


@bp.post("/api/integrations/telegram/find_chat")
def tg_find():
    row = cfg_of("telegram")
    if not row["cfg"].get("bot_token"):
        return H.err("Сначала сохраните токен бота")
    try:
        return jsonify(chats=Telegram().find_chat(row["cfg"]))
    except Exception as e:
        return H.err(str(e) if isinstance(e, RuntimeError) else "Нет связи с Telegram")


@bp.post("/api/integrations/telegram/report_now")
def tg_report_now():
    if not active("telegram"):
        return H.err("Telegram не включён")
    enqueue("telegram", "text", "Отчёт по запросу", {"text": daily_report()})
    run_due()
    j = H.q("SELECT status, error FROM integration_jobs WHERE key='telegram' ORDER BY id DESC LIMIT 1", one=True)
    return jsonify(ok=j["status"] == "done", status=j["status"], error=j["error"])


@bp.get("/api/integrations/jobs")
def int_jobs():
    key, st = request.args.get("key"), request.args.get("status")
    w, a = "1=1", []
    if key:
        w += " AND key=?"
        a.append(key)
    if st:
        w += " AND status=?"
        a.append(st)
    rows = H.q(f"SELECT id, key, kind, title, status, attempts, next_at, created_at, done_at, response, error FROM integration_jobs WHERE {w} ORDER BY id DESC LIMIT 300", a)
    return jsonify(items=rows)


@bp.post("/api/integrations/jobs/<int:jid>/retry")
def int_retry(jid):
    c = H.con()
    c.execute("UPDATE integration_jobs SET status='queued', next_at=?, attempts=0 WHERE id=? AND status IN ('dead','retry')", (H.now(), jid))
    c.commit()
    c.close()
    run_due()
    return jsonify(H.q("SELECT status, error FROM integration_jobs WHERE id=?", (jid,), one=True))


# ================================================================ Google Sheets: CSV-ленты по секретной ссылке
FEEDS = {"sales_daily": "Продажи по дням и магазинам (90 дней)", "stock": "Остатки по магазинам", "products": "Каталог и цены",
         "plans": "План и факт магазинов (текущий месяц)"}


def _csv(rows, hdr):
    import csv
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(hdr)
    w.writerows(rows)
    return Response(buf.getvalue(), mimetype="text/csv; charset=utf-8")


@bp.get("/feed/<token>/<name>.csv")
def feed(token, name):
    row = cfg_of("sheets")
    if not row["enabled"] or not token or token != row["cfg"].get("feed_token") or name not in FEEDS:
        return Response("not found", status=404)
    now = H.tnow()
    if name == "sales_daily":
        a0 = (now - timedelta(days=90)).strftime("%Y-%m-%d") + " 00:00:00"
        rows = H.q("""SELECT substr(s.created_at,1,10) d, st.name, COALESCE(SUM(CASE WHEN s.operation='sale' THEN s.total ELSE -s.total END),0) net,
                      COUNT(CASE WHEN s.operation='sale' THEN 1 END) checks FROM sales s JOIN stores st ON st.id=s.store_id
                      WHERE s.created_at>=? GROUP BY substr(s.created_at,1,10), st.name ORDER BY d, st.name""", (a0,))
        return _csv([[r["d"], r["name"], round(r["net"]), r["checks"]] for r in rows], ["Дата", "Магазин", "Выручка", "Чеков"])
    if name == "stock":
        rows = H.q("""SELECT st.name store, p.name, p.barcode, p.category, s.qty FROM stock s JOIN products p ON p.uid=s.product_uid
                      JOIN stores st ON st.id=s.store_id WHERE p.active=1 ORDER BY st.name, p.name""")
        return _csv([[r["store"], r["name"], r["barcode"], r["category"], r["qty"]] for r in rows], ["Магазин", "Товар", "Штрихкод", "Категория", "Остаток"])
    if name == "products":
        rows = H.q("SELECT name, barcode, sku, category, brand, price, spic FROM products WHERE active=1 ORDER BY name")
        return _csv([[r["name"], r["barcode"], r["sku"], r["category"], r["brand"], r["price"], r["spic"]] for r in rows],
                    ["Товар", "Штрихкод", "Артикул", "Категория", "Бренд", "Цена", "ИКПУ"])
    ym = now.strftime("%Y-%m")
    rows = H.q("""SELECT st.name, COALESCE(p.amount,0) plan, (SELECT COALESCE(SUM(CASE WHEN s.operation='sale' THEN s.total ELSE -s.total END),0)
                  FROM sales s WHERE s.store_id=st.id AND s.created_at>=?) fact FROM stores st LEFT JOIN store_plans p ON p.store_id=st.id AND p.month=?
                  WHERE st.active=1 ORDER BY st.name""", (ym + "-01 00:00:00", ym))
    return _csv([[ym, r["name"], round(r["plan"]), round(r["fact"]), round(r["fact"] / r["plan"] * 100, 1) if r["plan"] else ""] for r in rows],
                ["Месяц", "Магазин", "План", "Факт", "Выполнено, %"])


# ================================================================ 1С: CommerceML 2
@bp.get("/api/integrations/1c/commerceml.zip")
def commerceml():
    t = H.tnow().strftime("%Y-%m-%dT%H:%M:%S")
    prods = H.q("SELECT uid, name, barcode, sku, category, brand, unit_name, price, vat_percent, spic FROM products WHERE active=1 ORDER BY name")
    cats = sorted({p["category"] for p in prods if p["category"]})
    cat_id = {c: f"cat-{i}" for i, c in enumerate(cats, 1)}
    fi, fia = H.rbac.sf("id")
    stores = H.q(f"SELECT id, uid, name FROM stores WHERE active=1{fi} ORDER BY id", fia)
    stock = {}
    if stores:
        for r in H.q(f"SELECT store_id, product_uid, qty FROM stock WHERE store_id IN ({','.join('?' * len(stores))})", [s["id"] for s in stores]):
            stock.setdefault(r["product_uid"], {})[r["store_id"]] = r["qty"]
    imp = [f'<?xml version="1.0" encoding="UTF-8"?>\n<КоммерческаяИнформация ВерсияСхемы="2.08" ДатаФормирования="{t}">',
           '<Классификатор><Ид>kassa-hq</Ид><Наименование>Каталог сети</Наименование><Группы>']
    imp += [f"<Группа><Ид>{cat_id[c]}</Ид><Наименование>{xe(c)}</Наименование></Группа>" for c in cats]
    imp += ["</Группы></Классификатор>", '<Каталог СодержитТолькоИзменения="false"><Ид>kassa-hq</Ид><ИдКлассификатора>kassa-hq</ИдКлассификатора><Наименование>Каталог сети</Наименование><Товары>']
    for p in prods:
        imp.append(f"<Товар><Ид>{p['uid']}</Ид>" + (f"<Штрихкод>{xe(p['barcode'])}</Штрихкод>" if p["barcode"] else "") +
                   (f"<Артикул>{xe(p['sku'])}</Артикул>" if p["sku"] else "") + f"<Наименование>{xe(p['name'])}</Наименование>"
                   f"<БазоваяЕдиница>{xe(p['unit_name'] or 'шт')}</БазоваяЕдиница>" +
                   (f"<Группы><Ид>{cat_id[p['category']]}</Ид></Группы>" if p["category"] else "") +
                   (f"<Изготовитель><Наименование>{xe(p['brand'])}</Наименование></Изготовитель>" if p["brand"] else "") +
                   f"<СтавкиНалогов><СтавкаНалога><Наименование>НДС</Наименование><Ставка>{p['vat_percent'] or 0}</Ставка></СтавкаНалога></СтавкиНалогов>" +
                   (f"<ЗначенияРеквизитов><ЗначениеРеквизита><Наименование>ИКПУ</Наименование><Значение>{xe(p['spic'])}</Значение></ЗначениеРеквизита></ЗначенияРеквизитов>" if p["spic"] else "") +
                   "</Товар>")
    imp.append("</Товары></Каталог></КоммерческаяИнформация>")
    off = [f'<?xml version="1.0" encoding="UTF-8"?>\n<КоммерческаяИнформация ВерсияСхемы="2.08" ДатаФормирования="{t}">',
           '<ПакетПредложений СодержитТолькоИзменения="false"><Ид>kassa-hq#offers</Ид><Наименование>Цены и остатки</Наименование>'
           '<ИдКаталога>kassa-hq</ИдКаталога><ИдКлассификатора>kassa-hq</ИдКлассификатора>'
           '<ТипыЦен><ТипЦены><Ид>retail</Ид><Наименование>Розничная</Наименование><Валюта>UZS</Валюта></ТипЦены></ТипыЦен><Склады>']
    off += [f"<Склад><Ид>{s['uid']}</Ид><Наименование>{xe(s['name'])}</Наименование></Склад>" for s in stores]
    off.append("</Склады><Предложения>")
    for p in prods:
        st = stock.get(p["uid"], {})
        off.append(f"<Предложение><Ид>{p['uid']}</Ид><Наименование>{xe(p['name'])}</Наименование>"
                   f"<Цены><Цена><ИдТипаЦены>retail</ИдТипаЦены><ЦенаЗаЕдиницу>{p['price'] or 0}</ЦенаЗаЕдиницу><Валюта>UZS</Валюта></Цена></Цены>"
                   f"<Количество>{sum(max(0, v) for v in st.values()):g}</Количество>" +
                   "".join(f'<Склад ИдСклада="{s["uid"]}" КоличествоНаСкладе="{max(0, st.get(s["id"], 0)):g}"/>' for s in stores) + "</Предложение>")
    off.append("</Предложения></ПакетПредложений></КоммерческаяИнформация>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("import.xml", "".join(imp))
        z.writestr("offers.xml", "".join(off))
    H.rbac.audit("Выгрузка в 1С (CommerceML)", f"{len(prods)} товаров, {len(stores)} складов")
    return Response(buf.getvalue(), mimetype="application/zip", headers={"Content-Disposition": "attachment; filename=commerceml.zip"})


# ================================================================ Tasnif: контроль ИКПУ, Uzum: предпросмотр выгрузки
def tasnif_check():
    import re
    rows = H.q("SELECT uid, name, barcode, category, spic FROM products WHERE active=1 ORDER BY name")
    return {"missing": [r for r in rows if not (r["spic"] or "").strip()],
            "invalid": [r for r in rows if (r["spic"] or "").strip() and not re.fullmatch(r"\d{17}", r["spic"].strip())]}


@bp.get("/api/integrations/tasnif/check")
def tasnif_api():
    return jsonify(tasnif_check())


@bp.get("/api/integrations/uzum/preview")
def uzum_preview():
    row = cfg_of("uzum")
    sid = row["cfg"].get("store_id") or request.args.get("store")
    if not sid:
        return H.err("Выберите магазин, откуда будут отгружаться заказы Uzum")
    if not H.rbac.store_allowed(sid):
        return H.err("Нет доступа к магазину", 403)
    rows = H.q("""SELECT p.name, p.barcode, p.sku, p.price, COALESCE(s.qty,0) qty FROM products p
                  LEFT JOIN stock s ON s.product_uid=p.uid AND s.store_id=? WHERE p.active=1 AND COALESCE(s.qty,0)>0 ORDER BY p.name LIMIT 500""", (int(sid),))
    return jsonify(items=rows, note="Предпросмотр: что ушло бы на витрину Uzum. Отправка включится после подключения Seller API.")


def register(hq_module):
    global H
    H = hq_module
    c = H.con()
    c.executescript(SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY") if H.PG else SCHEMA)
    c.commit()
    c.close()
    H.app.register_blueprint(bp)
    # события перемещений → Telegram (в той же транзакции, что и смена статуса)
    tr = H.transfers
    orig = tr.event

    def event(c, uid, status, comment="", who=None):
        orig(c, uid, status, comment, who)
        try:
            on_transfer_event(c, uid, status)
        except Exception as e:
            print("telegram event:", e)
    tr.event = event
    import os
    if os.environ.get("INTEGRATIONS_WORKER", "1") == "1":
        threading.Thread(target=worker, daemon=True).start()
