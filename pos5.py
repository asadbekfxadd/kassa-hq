"""
Касса (этап 5): промокоды, QR-провайдеры и платёжные интеграции (Payme / Click / Uzum),
отчёт по кассиру.

Интеграции с платёжными системами работают через их официальные API и включаются только после того,
как банк/провайдер выдал доступы. До этого касса принимает QR-оплату в режиме «ручное подтверждение»:
кассир видит оплату в приложении провайдера и нажимает «Провести», а провайдер записывается в чек для отчётов.
"""
from datetime import datetime

from flask import Blueprint, jsonify, request, session, render_template_string

import db

bp = Blueprint("pos5", __name__)
M = None
SECRETS = ("payme_key", "click_secret", "uzum_secret")
PROVIDERS = {
    "payme": {"title": "Payme", "keys": ["payme_merchant_id", "payme_key"],
              "doc": "Payme Business: кассовый ID и ключ выдаёт Payme после заключения договора"},
    "click": {"title": "Click", "keys": ["click_service_id", "click_merchant_id", "click_secret"],
              "doc": "Click: service_id, merchant_id и secret_key из кабинета merchant.click.uz после подключения"},
    "uzum": {"title": "Uzum Bank", "keys": ["uzum_merchant_id", "uzum_secret"],
             "doc": "Uzum: идентификатор мерчанта и ключ выдаёт Uzum Bank после договора"},
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS promocodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE, kind TEXT NOT NULL DEFAULT 'percent',  -- percent|fixed
    value REAL NOT NULL DEFAULT 0, min_sum REAL NOT NULL DEFAULT 0, max_discount REAL NOT NULL DEFAULT 0,
    date_from TEXT, date_to TEXT, max_uses INTEGER NOT NULL DEFAULT 0, uses INTEGER NOT NULL DEFAULT 0,
    once_per_customer INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, note TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS promocode_uses (
    id INTEGER PRIMARY KEY AUTOINCREMENT, promocode_id INTEGER NOT NULL, sale_id INTEGER, customer_id INTEGER,
    discount REAL, created_at TEXT);
CREATE TABLE IF NOT EXISTS integration_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, provider TEXT, action TEXT, status TEXT,
    request TEXT, response TEXT, error TEXT, sale_id INTEGER, attempts INTEGER DEFAULT 1);
"""


def _admin():
    u = M.current_user()
    return u and u["role"] == "admin"


# ---------------------------------------------------------------- промокоды
def promo_eval(con, code, base, cust=None):
    """(строка промокода, скидка, ошибка). Скидку всегда считает сервер."""
    code = (code or "").strip().upper()
    p = con.execute("SELECT * FROM promocodes WHERE code=?", (code,)).fetchone()
    if not p or not p["active"]:
        return None, 0, "Промокод не найден или выключен"
    today = datetime.now().strftime("%Y-%m-%d")
    if p["date_from"] and today < p["date_from"]:
        return None, 0, f"Промокод действует с {p['date_from']}"
    if p["date_to"] and today > p["date_to"]:
        return None, 0, "Срок действия промокода истёк"
    if p["max_uses"] and p["uses"] >= p["max_uses"]:
        return None, 0, "Лимит использований промокода исчерпан"
    if base < (p["min_sum"] or 0) - 0.001:
        return None, 0, f"Промокод действует от суммы {p['min_sum']:,.0f}".replace(",", " ")
    if p["once_per_customer"]:
        if not cust:
            return None, 0, "Этот промокод — один раз на клиента: выберите клиента"
        if con.execute("SELECT 1 FROM promocode_uses WHERE promocode_id=? AND customer_id=?", (p["id"], cust["id"])).fetchone():
            return None, 0, "Клиент уже использовал этот промокод"
    disc = base * p["value"] / 100 if p["kind"] == "percent" else p["value"]
    if p["max_discount"]:
        disc = min(disc, p["max_discount"])
    return dict(p), round(min(disc, base)), None


def promo_used(con, pc, sale_id, cust, disc):
    con.execute("UPDATE promocodes SET uses=uses+1 WHERE id=?", (pc["id"],))
    con.execute("INSERT INTO promocode_uses(promocode_id,sale_id,customer_id,discount,created_at) VALUES(?,?,?,?,?)",
                (pc["id"], sale_id, cust["id"] if cust else None, disc, db.now()))


@bp.get("/api/promocodes")
def promocodes():
    if not M.current_user():
        return M.err("Требуется вход", 401)
    rows = M.q("""SELECT p.*, (SELECT COALESCE(SUM(discount),0) FROM promocode_uses u WHERE u.promocode_id=p.id) given
                  FROM promocodes p ORDER BY p.active DESC, p.id DESC""")
    return jsonify(rows)


@bp.post("/api/promocodes")
def promocode_save():
    if not _admin():
        return M.err("Доступ только для администратора", 403)
    d = request.json or {}
    code = (d.get("code") or "").strip().upper()
    if not code or len(code) < 3 or not all(ch.isalnum() or ch in "-_" for ch in code):
        return M.err("Код — от 3 символов: буквы, цифры, «-» и «_»")
    kind = "fixed" if d.get("kind") == "fixed" else "percent"
    value = M.num(d.get("value"))
    if value <= 0 or (kind == "percent" and value > 90):
        return M.err("Скидка: 1–90% или сумма больше нуля")
    vals = (code, kind, value, M.num(d.get("min_sum")), M.num(d.get("max_discount")), d.get("date_from") or None,
            d.get("date_to") or None, int(M.num(d.get("max_uses"))), int(bool(d.get("once_per_customer"))),
            int(d.get("active", 1)), d.get("note") or "")
    con = db.connect()
    try:
        if d.get("id"):
            con.execute("""UPDATE promocodes SET code=?, kind=?, value=?, min_sum=?, max_discount=?, date_from=?, date_to=?,
                           max_uses=?, once_per_customer=?, active=?, note=? WHERE id=?""", vals + (int(d["id"]),))
        else:
            con.execute("""INSERT INTO promocodes(code,kind,value,min_sum,max_discount,date_from,date_to,max_uses,once_per_customer,
                           active,note,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", vals + (db.now(),))
        con.commit()
    except Exception as e:
        con.close()
        return M.err("Такой промокод уже есть" if "UNIQUE" in str(e) else str(e))
    con.close()
    M.extras.audit("Промокод сохранён", f"{code}: {value:g}{'%' if kind == 'percent' else ' сум'}", 1)
    return jsonify(ok=True)


@bp.post("/api/promocodes/check")
def promocode_check():
    if not M.current_user():
        return M.err("Требуется вход", 401)
    d = request.json or {}
    con = db.connect()
    cust = con.execute("SELECT * FROM customers WHERE id=?", (d.get("customer_id"),)).fetchone() if d.get("customer_id") else None
    pc, disc, e = promo_eval(con, d.get("code"), M.num(d.get("base")), cust)
    con.close()
    if e:
        return M.err(e)
    return jsonify(code=pc["code"], kind=pc["kind"], value=pc["value"], min_sum=pc["min_sum"], max_discount=pc["max_discount"],
                   once_per_customer=pc["once_per_customer"], discount=disc)


# ---------------------------------------------------------------- интеграции
@bp.get("/api/integrations")
def integrations():
    if not _admin():
        return M.err("Доступ только для администратора", 403)
    s = db.get_settings()
    out = []
    for k, p in PROVIDERS.items():
        have = all((s.get(x) or "").strip() for x in p["keys"])
        out.append({"key": k, "title": p["title"], "doc": p["doc"], "keys_set": have, "active": False,
                    "mode": "Ручное подтверждение" if not have else "Ключи сохранены — ожидает проверки подключения",
                    "note": ("Автоматическая проверка оплаты не подключена: нет ключей API. QR-оплаты учитываются, "
                             "кассир подтверждает их вручную.") if not have else
                            ("Ключи сохранены. Автоматическая проверка включится после тестового подключения к API провайдера "
                             "по его документации — до этого оплата подтверждается вручную.")})
    log = M.q("SELECT * FROM integration_log ORDER BY id DESC LIMIT 100")
    stats = M.q("""SELECT COALESCE(NULLIF(qr_provider,''),'other') prov, COUNT(*) n, COALESCE(SUM(qr),0) sum
                   FROM sales WHERE operation='sale' AND qr>0 AND created_at>=date('now','-30 day') GROUP BY prov""")
    return jsonify(items=out, log=log, stats=stats,
                   enabled=[x for x in (s.get("qr_providers") or "payme,click,uzum").split(",") if x])


# ---------------------------------------------------------------- отчёт по кассиру
def cashier_report(f, t, uid=None):
    a = (f + " 00:00:00", t + " 23:59:59")
    users = M.q("SELECT id, name, role FROM users" + (" WHERE id=?" if uid else "") + " ORDER BY name", (uid,) if uid else ())
    out = []
    for u in users:
        sh = M.q("""SELECT * FROM shifts WHERE user_id=? AND opened_at BETWEEN ? AND ? ORDER BY id""", (u["id"],) + a)
        shifts = []
        for s in sh:
            rep = M.shift_report(s["id"])
            dur = ((datetime.strptime(s["closed_at"], "%Y-%m-%d %H:%M:%S") if s["closed_at"] else datetime.now())
                   - datetime.strptime(s["opened_at"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600
            shifts.append({"id": s["id"], "opened_at": s["opened_at"], "closed_at": s["closed_at"], "hours": round(dur, 1),
                           "opening_cash": s["opening_cash"] or 0, "expected": rep["expected_cash"], "closing_cash": s["closing_cash"],
                           "diff": round((s["closing_cash"] or 0) - rep["expected_cash"], 2) if s["closing_cash"] is not None else None,
                           "net": rep["net"], "checks": rep["sales"]["n"]})
        r = M.q("""SELECT COUNT(CASE WHEN operation='sale' THEN 1 END) checks,
                   COALESCE(SUM(CASE WHEN operation='sale' THEN total END),0) gross,
                   COALESCE(SUM(CASE WHEN operation='refund' THEN total END),0) refunds, COUNT(CASE WHEN operation='refund' THEN 1 END) refund_n,
                   COALESCE(SUM(CASE WHEN operation='sale' THEN cash ELSE -cash END),0) cash,
                   COALESCE(SUM(CASE WHEN operation='sale' THEN card ELSE -card END),0) card,
                   COALESCE(SUM(CASE WHEN operation='sale' THEN qr ELSE -qr END),0) qr,
                   COALESCE(SUM(CASE WHEN operation='sale' THEN debt ELSE -debt END),0) debt,
                   COALESCE(SUM(CASE WHEN operation='sale' THEN bonus_used END),0) bonus,
                   COALESCE(SUM(CASE WHEN operation='sale' THEN discount END),0) discount,
                   COALESCE(SUM(CASE WHEN operation='sale' THEN promo_disc END),0) promo_disc,
                   COUNT(CASE WHEN operation='sale' AND COALESCE(promo_code,'')<>'' THEN 1 END) promo_n,
                   COUNT(CASE WHEN fiscal_status IN ('pending','error') THEN 1 END) unfiscal
                   FROM sales WHERE user_id=? AND created_at BETWEEN ? AND ?""", (u["id"],) + a, one=True)
        qr = M.q("""SELECT COALESCE(NULLIF(qr_provider,''),'other') prov, COALESCE(SUM(CASE WHEN operation='sale' THEN qr ELSE -qr END),0) v
                    FROM sales WHERE user_id=? AND qr>0 AND created_at BETWEEN ? AND ? GROUP BY prov""", (u["id"],) + a)
        au = M.q("""SELECT COUNT(CASE WHEN action='Удалена строка из чека' THEN 1 END) deleted,
                    COUNT(CASE WHEN action='Чек очищен без оплаты' THEN 1 END) cleared,
                    COUNT(CASE WHEN action LIKE '%подтвержден%' THEN 1 END) approvals
                    FROM audit WHERE user_id=? AND created_at BETWEEN ? AND ?""", (u["id"],) + a, one=True)
        promo_lines = M.q("""SELECT COALESCE(SUM(si.discount),0) v FROM sale_items si JOIN sales s ON s.id=si.sale_id
                             WHERE s.user_id=? AND s.operation='sale' AND si.promo IS NOT NULL AND s.created_at BETWEEN ? AND ?""",
                          (u["id"],) + a, one=True)["v"]
        if not (r["checks"] or r["refund_n"] or shifts):
            continue
        net = r["gross"] - r["refunds"]
        out.append({**u, **r, **au, "net": round(net, 2), "avg": round(r["gross"] / r["checks"]) if r["checks"] else 0,
                    "qr_by": {x["prov"]: x["v"] for x in qr}, "promo_lines": promo_lines,
                    "manual_disc": round(r["discount"] - promo_lines - r["promo_disc"] - r["bonus"], 2),
                    "shifts": shifts, "hours": round(sum(s["hours"] for s in shifts), 1),
                    "cash_diff": round(sum(s["diff"] or 0 for s in shifts), 2)})
    return out


@bp.get("/api/rep/cashier")
def rep_cashier():
    u = M.current_user()
    if not u:
        return M.err("Требуется вход", 401)
    f = request.args.get("from") or datetime.now().strftime("%Y-%m-%d")
    t = request.args.get("to") or f
    uid = request.args.get("user") or None
    if u["role"] != "admin":
        uid = u["id"]        # кассир видит только свой отчёт
    return jsonify(items=cashier_report(f, t, int(uid) if uid else None), period=[f, t],
                   users=M.q("SELECT id, name FROM users ORDER BY name") if u["role"] == "admin" else [])


REPORT = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Отчёт по кассиру</title>
<style>body{font:13px/1.45 Arial,sans-serif;margin:22px;color:#000}h2{margin:0}h3{margin:18px 0 4px}table{width:100%;border-collapse:collapse;margin-top:6px}
th,td{border:1px solid #999;padding:4px 6px;text-align:left}td.n,th.n{text-align:right}.k{display:inline-block;margin:4px 18px 4px 0}
.k b{font-size:16px}.red{color:#b42318}@media print{button{display:none}}</style></head><body>
<button onclick="print()">Печать</button>
<h2>Отчёт по кассиру</h2><div>{{s.store_name or 'Магазин'}} · период {{p[0]}} — {{p[1]}} · сформирован {{now}}</div>
{% for c in items %}<h3>{{c.name}} <span style="font-weight:normal">({{'Администратор' if c.role=='admin' else 'Кассир'}})</span></h3>
<div><span class="k">Чеков: <b>{{c.checks}}</b></span><span class="k">Выручка: <b>{{m(c.net)}}</b></span><span class="k">Средний чек: <b>{{m(c.avg)}}</b></span>
<span class="k">Часов: <b>{{c.hours}}</b></span><span class="k">Возвраты: <b>{{c.refund_n}} на {{m(c.refunds)}}</b></span></div>
<table><tr><th>Наличные</th><th>Карта</th>{% for k,v in c.qr_by.items() %}<th>QR {{prov.get(k,'другой')}}</th>{% endfor %}<th>Насия</th><th>Бонусы</th>
<th>Скидки вручную</th><th>Акции</th><th>Промокоды</th><th>Удалено строк</th><th>Очищено чеков</th><th>Не в ФМ</th></tr>
<tr><td class="n">{{m(c.cash)}}</td><td class="n">{{m(c.card)}}</td>{% for k,v in c.qr_by.items() %}<td class="n">{{m(v)}}</td>{% endfor %}
<td class="n">{{m(c.debt)}}</td><td class="n">{{m(c.bonus)}}</td><td class="n">{{m(c.manual_disc)}}</td><td class="n">{{m(c.promo_lines)}}</td>
<td class="n">{{m(c.promo_disc)}} ({{c.promo_n}})</td><td class="n">{{c.deleted}}</td><td class="n">{{c.cleared}}</td><td class="n {{'red' if c.unfiscal}}">{{c.unfiscal}}</td></tr></table>
<table><tr><th>Смена</th><th>Открыта</th><th>Закрыта</th><th class="n">Часов</th><th class="n">Чеков</th><th class="n">Выручка</th><th class="n">Нал. на начало</th>
<th class="n">Должно быть</th><th class="n">Сдано</th><th class="n">Расхождение</th></tr>
{% for x in c.shifts %}<tr><td>№{{x.id}}</td><td>{{x.opened_at[:16]}}</td><td>{{(x.closed_at or 'открыта')[:16]}}</td><td class="n">{{x.hours}}</td><td class="n">{{x.checks}}</td>
<td class="n">{{m(x.net)}}</td><td class="n">{{m(x.opening_cash)}}</td><td class="n">{{m(x.expected)}}</td><td class="n">{{m(x.closing_cash) if x.closing_cash is not none else '—'}}</td>
<td class="n {{'red' if x.diff}}">{{'%+.0f'|format(x.diff) if x.diff is not none else '—'}}</td></tr>{% endfor %}</table>
{% else %}<p>За период нет продаж и смен.</p>{% endfor %}
<p style="margin-top:30px">Подпись кассира: ____________________ &nbsp;&nbsp; Подпись администратора: ____________________</p>
{% if auto %}<script>setTimeout(()=>print(),300)</script>{% endif %}</body></html>"""


@bp.get("/cashier-report")
def cashier_report_page():
    u = M.current_user()
    if not u:
        return "Требуется вход", 401
    f = request.args.get("from") or datetime.now().strftime("%Y-%m-%d")
    t = request.args.get("to") or f
    uid = request.args.get("user") or None
    if u["role"] != "admin":
        uid = u["id"]
    return render_template_string(REPORT, items=cashier_report(f, t, int(uid) if uid else None), p=[f, t], s=db.get_settings(),
                                  now=datetime.now().strftime("%Y-%m-%d %H:%M"), auto=request.args.get("print") == "1",
                                  m=lambda v: f"{(v or 0):,.0f}".replace(",", " "),
                                  prov={"payme": "Payme", "click": "Click", "uzum": "Uzum", "other": "другой"})


def register(app_module):
    global M
    M = app_module
    con = db.connect()
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    M.app.register_blueprint(bp)
