"""
Расширения: клиенты и лояльность, насия (долги), кассовые операции, журнал действий,
отложенные чеки, подтверждение администратором, ценники/этикетки, автозаказ и ABC,
Telegram-уведомления владельцу, экран покупателя.
"""
import io
import json
import math
import threading
import time
from datetime import datetime, timedelta

import requests
from flask import Blueprint, jsonify, request, session, render_template, abort

import db

bp = Blueprint("extras", __name__)
M = None  # модуль app (helpers: q, err, num, money, current_user, login_required, admin_required, open_shift)


# ================================================================ helpers
def audit(action, details="", severity=0, user_id=None, con=None):
    own = con is None
    con = con or db.connect()
    if user_id is None:
        try:
            user_id = session.get("uid")
        except RuntimeError:
            user_id = None
    con.execute("INSERT INTO audit(created_at,user_id,action,details,severity) VALUES(?,?,?,?,?)",
                (db.now(), user_id, action,
                 details if isinstance(details, str) else json.dumps(details, ensure_ascii=False), severity))
    if own:
        con.commit()
        con.close()
    if severity >= 2:
        s = db.get_settings()
        if s.get("tg_notify_alerts") == "1":
            try:
                uid = user_id or session.get("uid")
            except RuntimeError:
                uid = user_id
            u = M.q("SELECT name FROM users WHERE id=?", (uid,), one=True) if uid else None
            tg_send(f"⚠️ <b>{_h(s.get('store_name'))}</b>\n{_h(action)}\n{_h(details if isinstance(details, str) else '')}"
                    + (f"\n👤 {_h(u['name'])}" if u else ""))


def is_admin_pin(pin):
    if not pin:
        return None
    return M.q("SELECT id,name FROM users WHERE pin=? AND role='admin' AND active=1", (str(pin).strip(),), one=True)


def _h(t):
    return str(t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fmt(v):
    return f"{(v or 0):,.0f}".replace(",", " ")


# ================================================================ Telegram
def tg_send(text, settings=None, sync=False):
    s = settings or db.get_settings()
    token, chat = (s.get("tg_token") or "").strip(), (s.get("tg_chat_id") or "").strip()
    if not token or not chat:
        return False, "Telegram не настроен"

    def go():
        err = None
        for cid in [c.strip() for c in chat.split(",") if c.strip()]:
            try:
                r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                  json={"chat_id": cid, "text": text[:4000], "parse_mode": "HTML",
                                        "disable_web_page_preview": True}, timeout=15)
                if not r.ok:
                    err = r.text[:300]
            except Exception as e:
                err = str(e)
        return err

    if sync:
        e = go()
        return (e is None), (e or "OK")
    threading.Thread(target=go, daemon=True).start()
    return True, "queued"


def day_report_text(day=None):
    day = day or datetime.now().strftime("%Y-%m-%d")
    a = (day + " 00:00:00", day + " 23:59:59")
    s = db.get_settings()
    t = M.q("""SELECT COALESCE(SUM(CASE WHEN operation='sale' THEN total END),0) sales,
               COALESCE(SUM(CASE WHEN operation='refund' THEN total END),0) refunds,
               COALESCE(SUM(CASE WHEN operation='sale' THEN cash ELSE -cash END),0) cash,
               COALESCE(SUM(CASE WHEN operation='sale' THEN card ELSE -card END),0) card,
               COALESCE(SUM(CASE WHEN operation='sale' THEN debt ELSE -debt END),0) debt,
               COUNT(CASE WHEN operation='sale' THEN 1 END) n FROM sales WHERE created_at BETWEEN ? AND ?""", a, one=True)
    cost = M.q("""SELECT COALESCE(SUM(CASE WHEN s.operation='sale' THEN si.qty*si.cost_price ELSE -si.qty*si.cost_price END),0) c
                  FROM sale_items si JOIN sales s ON s.id=si.sale_id WHERE s.created_at BETWEEN ? AND ?""", a, one=True)["c"]
    net = t["sales"] - t["refunds"]
    exp = M.q("SELECT COALESCE(SUM(amount),0) v FROM cash_ops WHERE type='expense' AND created_at BETWEEN ? AND ?", a, one=True)["v"]
    debt_in = M.q("SELECT COALESCE(SUM(amount),0) v FROM customer_ops WHERE type='debt_pay' AND created_at BETWEEN ? AND ?", a, one=True)["v"]
    top = M.q("""SELECT si.name, SUM(si.qty) q, SUM(si.total) t FROM sale_items si JOIN sales s ON s.id=si.sale_id
                 WHERE s.operation='sale' AND s.created_at BETWEEN ? AND ? GROUP BY si.product_id ORDER BY t DESC LIMIT 5""", a)
    low = M.q("SELECT name, stock FROM products WHERE active=1 AND min_stock>0 AND stock<=min_stock ORDER BY stock LIMIT 8")
    # сравнение с тем же днём неделю назад
    prev_day = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
    prev = M.q("""SELECT COALESCE(SUM(CASE WHEN operation='sale' THEN total ELSE -total END),0) v FROM sales
                  WHERE created_at BETWEEN ? AND ?""", (prev_day + " 00:00:00", prev_day + " 23:59:59"), one=True)["v"]
    delta = f" ({'+' if net >= prev else ''}{(net - prev) / prev * 100:.0f}% к пр. неделе)" if prev else ""
    lines = [f"📊 <b>{_h(s.get('store_name'))}</b> — отчёт за {day}",
             f"💰 Выручка: <b>{fmt(net)}</b>{delta}",
             f"🧾 Чеков: {t['n']} · средний {fmt(t['sales'] / t['n'] if t['n'] else 0)}",
             f"📈 Прибыль: {fmt(net - cost)}",
             f"💵 Нал: {fmt(t['cash'])} · 💳 Карта: {fmt(t['card'])}"]
    if t["debt"]:
        lines.append(f"📝 В насию: {fmt(t['debt'])}")
    if debt_in:
        lines.append(f"✅ Погашено долгов: {fmt(debt_in)}")
    if t["refunds"]:
        lines.append(f"↩️ Возвраты: {fmt(t['refunds'])}")
    if exp:
        lines.append(f"💸 Расходы: {fmt(exp)}")
    if top:
        lines.append("\n🏆 <b>Топ-5:</b>")
        lines += [f"• {_h(r['name'])} — {r['q']:g} шт · {fmt(r['t'])}" for r in top]
    if low:
        lines.append("\n⚠️ <b>Заканчивается:</b>")
        lines += [f"• {_h(r['name'])} — {r['stock']:g}" for r in low]
    try:
        ex = M.grocery.expiry_summary()
        if ex["expired"] or ex["soon"]:
            lines.append(f"\n⏳ <b>Сроки:</b> просрочено {ex['expired']} поз., истекает скоро {ex['soon']}")
            if ex["names"]:
                lines.append("• " + ", ".join(_h(n) for n in ex["names"][:5]))
    except Exception:
        pass
    sup_debt = M.q("SELECT COALESCE(SUM(balance),0) v FROM suppliers WHERE balance>0", one=True)["v"]
    if sup_debt:
        lines.append(f"🚚 Долг поставщикам: {fmt(sup_debt)}")
    total_debt = M.q("SELECT COALESCE(SUM(debt),0) v FROM customers", one=True)["v"]
    if total_debt:
        lines.append(f"\n📒 Общий долг клиентов: {fmt(total_debt)}")
    return "\n".join(lines)


def tg_worker():
    sent_for = None
    while True:
        try:
            s = db.get_settings()
            if s.get("tg_token") and s.get("tg_chat_id"):
                h = int(M.num(s.get("tg_daily_hour"), 22))
                now = datetime.now()
                today = now.strftime("%Y-%m-%d")
                if now.hour == h and sent_for != today:
                    tg_send(day_report_text(today), s)
                    sent_for = today
        except Exception:
            pass
        time.sleep(60)


# ================================================================ customer display
DISPLAY = {"state": {"items": [], "total": 0, "status": "idle"}, "v": 0}


@bp.post("/api/display")
def display_set():
    if not M.current_user():
        return M.err("Требуется вход", 401)
    DISPLAY["state"] = request.json or {}
    DISPLAY["v"] += 1
    return jsonify(ok=True)


@bp.get("/api/display")
def display_get():
    if request.remote_addr not in ("127.0.0.1", "::1") and not M.current_user():
        abort(403)
    s = db.get_settings()
    return jsonify(v=DISPLAY["v"], state=DISPLAY["state"], store=s.get("store_name"), color=s.get("theme_color"),
                   currency=s.get("currency"), footer=s.get("receipt_footer"))


@bp.get("/display")
def display_page():
    return render_template("display.html")


# ================================================================ admin approval
@bp.post("/api/auth/check")
def auth_check():
    if not M.current_user():
        return M.err("Требуется вход", 401)
    d = request.json or {}
    a = is_admin_pin(d.get("pin"))
    if not a:
        audit("Неверный PIN администратора", d.get("reason", ""), 1)
        return M.err("Неверный PIN администратора", 403)
    audit("Подтверждено администратором " + a["name"], d.get("reason", ""), 1)
    return jsonify(ok=True, name=a["name"])


# ================================================================ audit
@bp.post("/api/audit")
def audit_post():
    if not M.current_user():
        return M.err("Требуется вход", 401)
    d = request.json or {}
    audit(str(d.get("action", ""))[:100], str(d.get("details", ""))[:500], int(d.get("severity", 0)))
    return jsonify(ok=True)


@bp.get("/api/audit")
def audit_list():
    u = M.current_user()
    if not u or u["role"] != "admin":
        return M.err("Доступ только для администратора", 403)
    dfrom = request.args.get("from") or (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    dto = request.args.get("to") or datetime.now().strftime("%Y-%m-%d")
    sev = int(M.num(request.args.get("min"), 0))
    return jsonify(M.q("""SELECT a.*, u.name AS user FROM audit a LEFT JOIN users u ON u.id=a.user_id
                          WHERE a.created_at BETWEEN ? AND ? AND a.severity>=? ORDER BY a.id DESC LIMIT 1000""",
                       (dfrom + " 00:00:00", dto + " 23:59:59", sev)))


# ================================================================ parked checks
@bp.get("/api/parked")
def parked_list():
    if not M.current_user():
        return M.err("Требуется вход", 401)
    rows = M.q("SELECT p.*, u.name AS user FROM parked p LEFT JOIN users u ON u.id=p.user_id ORDER BY p.id")
    for r in rows:
        r["data"] = json.loads(r["data"])
    return jsonify(rows)


@bp.post("/api/parked")
def parked_add():
    if not M.current_user():
        return M.err("Требуется вход", 401)
    d = request.json or {}
    if not d.get("cart"):
        return M.err("Пустой чек")
    con = db.connect()
    pid = con.execute("INSERT INTO parked(created_at,user_id,title,data) VALUES(?,?,?,?)",
                      (db.now(), session["uid"], d.get("title") or "", json.dumps(d, ensure_ascii=False))).lastrowid
    con.commit()
    con.close()
    return jsonify(id=pid)


@bp.delete("/api/parked/<int:pid>")
def parked_del(pid):
    if not M.current_user():
        return M.err("Требуется вход", 401)
    con = db.connect()
    con.execute("DELETE FROM parked WHERE id=?", (pid,))
    con.commit()
    con.close()
    return jsonify(ok=True)


# ================================================================ customers
@bp.get("/api/customers")
def customers_list():
    if not M.current_user():
        return M.err("Требуется вход", 401)
    t = (request.args.get("q") or "").strip()
    where, args = ["1=1"], []
    if t:
        digits = "".join(c for c in t if c.isdigit())
        conds = ["name LIKE ?", "card=?"]
        args += [f"%{t}%", t]
        if digits:
            conds.append("phone LIKE ?")
            args.append(f"%{digits}%")
        where.append("(" + " OR ".join(conds) + ")")
    if request.args.get("debtors"):
        where.append("debt>0")
    order = "debt DESC" if request.args.get("debtors") else "name"
    rows = M.q(f"""SELECT c.*,
        (SELECT COUNT(*) FROM sales s WHERE s.customer_id=c.id AND s.operation='sale') AS purchases,
        (SELECT COALESCE(SUM(CASE WHEN operation='sale' THEN total ELSE -total END),0) FROM sales s WHERE s.customer_id=c.id) AS spent,
        (SELECT MAX(created_at) FROM sales s WHERE s.customer_id=c.id) AS last_visit
        FROM customers c WHERE {' AND '.join(where)} ORDER BY {order} LIMIT 500""", args)
    return jsonify(rows)


def norm_phone(p):
    d = "".join(c for c in str(p or "") if c.isdigit())
    if len(d) == 9:
        d = "998" + d
    return d or None


@bp.post("/api/customers")
def customer_save():
    if not M.current_user():
        return M.err("Требуется вход", 401)
    d = request.json or {}
    name = (d.get("name") or "").strip()
    phone = norm_phone(d.get("phone"))
    if not name and not phone:
        return M.err("Укажите имя или телефон")
    name = name or ("Клиент " + (phone or "")[-4:])
    u = M.current_user()
    fields = {"name": name, "phone": phone, "card": (d.get("card") or "").strip() or None,
              "birthday": d.get("birthday") or None, "note": d.get("note") or ""}
    if u["role"] == "admin":
        fields["discount_pct"] = M.num(d.get("discount_pct"))
        fields["debt_limit"] = M.num(d.get("debt_limit"))
    con = db.connect()
    try:
        if d.get("id"):
            sets = ",".join(f"{k}=?" for k in fields)
            con.execute(f"UPDATE customers SET {sets} WHERE id=?", list(fields.values()) + [d["id"]])
            cid = d["id"]
        else:
            cid = con.execute(f"INSERT INTO customers({','.join(fields)}) VALUES({','.join('?' * len(fields))})",
                              list(fields.values())).lastrowid
        con.commit()
    except Exception as e:
        con.close()
        return M.err("Клиент с таким телефоном уже есть" if "UNIQUE" in str(e) else str(e))
    con.close()
    return jsonify(M.q("SELECT * FROM customers WHERE id=?", (cid,), one=True))


@bp.get("/api/customers/<int:cid>")
def customer_get(cid):
    if not M.current_user():
        return M.err("Требуется вход", 401)
    c = M.q("SELECT * FROM customers WHERE id=?", (cid,), one=True)
    if not c:
        return M.err("Клиент не найден", 404)
    c["ops"] = M.q("""SELECT o.*, u.name AS user FROM customer_ops o LEFT JOIN users u ON u.id=o.user_id
                      WHERE customer_id=? ORDER BY o.id DESC LIMIT 200""", (cid,))
    c["sales"] = M.q("""SELECT id,created_at,operation,total,debt,bonus_used FROM sales WHERE customer_id=?
                        ORDER BY id DESC LIMIT 100""", (cid,))
    c["fav"] = M.q("""SELECT si.name, SUM(si.qty) q FROM sale_items si JOIN sales s ON s.id=si.sale_id
                      WHERE s.customer_id=? AND s.operation='sale' GROUP BY si.product_id ORDER BY q DESC LIMIT 5""", (cid,))
    return jsonify(c)


C_ALIASES = {
    "name": ["клиент", "фио", "имя", "покупатель", "контрагент", "name", "mijoz"],
    "phone": ["телефон", "тел", "номер телефона", "мобильный", "phone", "telefon"],
    "card": ["карта", "номер карты", "дисконтная карта", "card"],
    "bonus": ["бонусы", "бонус", "баллы", "бонусный баланс", "bonus", "кэшбэк", "кешбэк"],
    "debt": ["долг", "задолженность", "насия", "nasiya", "qarz", "debt"],
    "discount_pct": ["скидка", "скидка %", "процент скидки", "discount"],
}


@bp.post("/api/customers/import")
def customers_import():
    """Клиенты с бонусами и долгами из Excel/CSV другой программы (для магазина без Центра)."""
    u = M.current_user()
    if not u or u["role"] != "admin":
        return M.err("Доступ только для администратора", 403)
    f = request.files.get("file")
    if not f:
        return M.err("Файл не выбран")
    try:
        rows = M._rows_from_upload(f)
    except Exception as e:
        return M.err("Не удалось прочитать файл: " + str(e))
    con = db.connect()
    t = db.now()
    created = updated = skipped = 0
    for r in rows:
        rec = {}
        for k, al in C_ALIASES.items():
            for a in al:
                if a in r and r[a] not in (None, ""):
                    rec[k] = r[a]
                    break
        ph = norm_phone(str(rec.get("phone") or "").split(".")[0])
        name = str(rec.get("name") or "").strip()
        if not ph and not name:
            skipped += 1
            continue
        ex = con.execute("SELECT * FROM customers WHERE phone=?", (ph,)).fetchone() if ph else None
        if ex:
            cid = ex["id"]
            updated += 1
        else:
            cid = con.execute("INSERT INTO customers(name,phone,card,discount_pct,note,created_at) VALUES(?,?,?,?,?,?)",
                              (name or "Клиент " + (ph or "")[-4:], ph, str(rec.get("card") or "").split(".")[0] or None,
                               M.num(rec.get("discount_pct")), "Перенесён из прошлой программы", t)).lastrowid
            created += 1
        for field, typ in (("bonus", "bonus_add"), ("debt", "debt_add")):
            v = M.num(rec.get(field))
            if v > 0:
                con.execute(f"UPDATE customers SET {field}=? WHERE id=?", (v, cid))
                con.execute("INSERT INTO customer_ops(customer_id,created_at,type,amount,user_id,note) VALUES(?,?,?,?,?,?)",
                            (cid, t, typ, v, session["uid"], "Перенос баланса из прошлой программы"))
    con.commit()
    con.close()
    audit("Импорт клиентов", f"создано {created}, обновлено {updated}", 1)
    return jsonify(created=created, updated=updated, skipped=skipped)


@bp.post("/api/customers/<int:cid>/debt_pay")
def customer_debt_pay(cid):
    """Погашение насии. Наличные попадают в кассу смены."""
    if not M.current_user():
        return M.err("Требуется вход", 401)
    d = request.json or {}
    amount = M.money(d.get("amount"))
    method = "card" if d.get("method") == "card" else "cash"
    con = db.connect()
    c = con.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
    if not c:
        con.close()
        return M.err("Клиент не найден", 404)
    if amount <= 0:
        con.close()
        return M.err("Сумма должна быть больше нуля")
    if amount > c["debt"] + 0.001:
        con.close()
        return M.err(f"Долг клиента всего {fmt(c['debt'])}")
    sh = M.open_shift(con)
    if not sh:
        con.close()
        return M.err("Откройте смену", 409)
    t = db.now()
    con.execute("UPDATE customers SET debt=debt-? WHERE id=?", (amount, cid))
    con.execute("INSERT INTO customer_ops(customer_id,created_at,type,amount,user_id,method,note) VALUES(?,?,?,?,?,?,?)",
                (cid, t, "debt_pay", amount, session["uid"], method, d.get("note") or ""))
    con.execute("INSERT INTO cash_ops(shift_id,created_at,type,method,amount,category,note,user_id) VALUES(?,?,?,?,?,?,?,?)",
                (sh["id"], t, "debt_in", method, amount, "Погашение долга", c["name"], session["uid"]))
    con.commit()
    con.close()
    audit("Погашение долга", f"{c['name']}: {fmt(amount)} ({'карта' if method == 'card' else 'нал'})")
    return jsonify(M.q("SELECT * FROM customers WHERE id=?", (cid,), one=True))


@bp.post("/api/customers/<int:cid>/bonus")
def customer_bonus_adjust(cid):
    u = M.current_user()
    if not u or u["role"] != "admin":
        return M.err("Доступ только для администратора", 403)
    d = request.json or {}
    amount = M.money(d.get("amount"))
    con = db.connect()
    con.execute("UPDATE customers SET bonus=MAX(0,bonus+?) WHERE id=?", (amount, cid))
    con.execute("INSERT INTO customer_ops(customer_id,created_at,type,amount,user_id,note) VALUES(?,?,?,?,?,?)",
                (cid, db.now(), "bonus_add" if amount > 0 else "bonus_use", abs(amount), session["uid"],
                 d.get("note") or "Ручная корректировка"))
    con.commit()
    con.close()
    audit("Корректировка бонусов", f"клиент #{cid}: {amount:+g}", 1)
    return jsonify(M.q("SELECT * FROM customers WHERE id=?", (cid,), one=True))


# ================================================================ cash operations
@bp.post("/api/cash_ops")
def cash_op_add():
    if not M.current_user():
        return M.err("Требуется вход", 401)
    d = request.json or {}
    typ = d.get("type")
    if typ not in ("in", "out", "expense"):
        return M.err("Неверный тип")
    amount = M.money(d.get("amount"))
    if amount <= 0:
        return M.err("Сумма должна быть больше нуля")
    con = db.connect()
    sh = M.open_shift(con)
    if not sh:
        con.close()
        return M.err("Откройте смену", 409)
    con.execute("INSERT INTO cash_ops(shift_id,created_at,type,method,amount,category,note,user_id) VALUES(?,?,?,?,?,?,?,?)",
                (sh["id"], db.now(), typ, "cash", amount, d.get("category") or "", d.get("note") or "", session["uid"]))
    con.commit()
    con.close()
    names = {"in": "Внесение", "out": "Изъятие", "expense": "Расход"}
    audit(f"{names[typ]} наличных", f"{fmt(amount)} {d.get('category') or ''} {d.get('note') or ''}", 1 if typ != "in" else 0)
    return jsonify(ok=True)


@bp.get("/api/cash_ops")
def cash_op_list():
    if not M.current_user():
        return M.err("Требуется вход", 401)
    sid = request.args.get("shift_id")
    if sid:
        rows = M.q("""SELECT c.*, u.name AS user FROM cash_ops c LEFT JOIN users u ON u.id=c.user_id
                      WHERE shift_id=? ORDER BY c.id DESC""", (sid,))
    else:
        dfrom = request.args.get("from") or datetime.now().strftime("%Y-%m-01")
        dto = request.args.get("to") or datetime.now().strftime("%Y-%m-%d")
        rows = M.q("""SELECT c.*, u.name AS user FROM cash_ops c LEFT JOIN users u ON u.id=c.user_id
                      WHERE c.created_at BETWEEN ? AND ? ORDER BY c.id DESC""", (dfrom + " 00:00:00", dto + " 23:59:59"))
    return jsonify(rows)


def cash_ops_totals(shift_id):
    r = M.q("""SELECT
        COALESCE(SUM(CASE WHEN type='in' THEN amount END),0) cin,
        COALESCE(SUM(CASE WHEN type='out' THEN amount END),0) cout,
        COALESCE(SUM(CASE WHEN type='expense' THEN amount END),0) expense,
        COALESCE(SUM(CASE WHEN type='debt_in' AND method='cash' THEN amount END),0) debt_cash,
        COALESCE(SUM(CASE WHEN type='debt_in' AND method='card' THEN amount END),0) debt_card
        FROM cash_ops WHERE shift_id=?""", (shift_id,), one=True)
    return r


# ================================================================ labels / price tags
def barcode_svg(code):
    try:
        import barcode
        from barcode.writer import SVGWriter
        code = str(code or "").strip()
        if not code:
            return ""
        kind = "code128"
        if code.isdigit() and len(code) == 13:
            s = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(code[:12]))
            if (10 - s % 10) % 10 == int(code[12]):
                kind = "ean13"
        elif code.isdigit() and len(code) == 8:
            kind = "ean8"
        b = barcode.get(kind, code if kind == "code128" else code[:-1], writer=SVGWriter())
        buf = io.BytesIO()
        b.write(buf, options={"module_height": 9, "font_size": 5, "text_distance": 2.5, "quiet_zone": 2,
                              "module_width": 0.25 if kind == "code128" else 0.3})
        svg = buf.getvalue().decode("utf-8")
        return svg[svg.find("<svg"):]
    except Exception:
        return ""


@bp.get("/labels")
def labels():
    u = M.current_user()
    if not u:
        abort(401)
    ids = [int(x) for x in (request.args.get("ids") or "").split(",") if x.strip().isdigit()]
    copies = {}
    for part in (request.args.get("copies") or "").split(","):
        if ":" in part:
            k, v = part.split(":", 1)
            if k.isdigit():
                copies[int(k)] = max(1, min(500, int(M.num(v, 1))))
    kind = request.args.get("kind", "price")       # price | label
    size = request.args.get("size", "58x40")
    if not ids:
        abort(400)
    rows = M.q(f"SELECT * FROM products WHERE id IN ({','.join('?' * len(ids))})", ids)
    items = []
    for p in rows:
        p["svg"] = barcode_svg(p["barcode"]) if p.get("barcode") else ""
        for _ in range(copies.get(p["id"], 1)):
            items.append(p)
    w, h = (size.split("x") + ["40"])[:2]
    return render_template("labels.html", items=items, s=db.get_settings(), kind=kind, w=w, h=h,
                           today=datetime.now().strftime("%d.%m.%Y"), auto=request.args.get("print") == "1")


# ================================================================ reorder / ABC
@bp.get("/api/reorder")
def reorder():
    u = M.current_user()
    if not u or u["role"] != "admin":
        return M.err("Доступ только для администратора", 403)
    s = db.get_settings()
    days = max(7, int(M.num(request.args.get("days"), 30)))
    cover = max(1, int(M.num(request.args.get("cover"), M.num(s.get("reorder_cover_days"), 14))))
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d 00:00:00")
    rows = M.q("""SELECT p.id, p.name, p.barcode, p.stock, p.min_stock, p.cost_price, p.price, p.unit_name,
                  COALESCE(-SUM(CASE WHEN m.type IN ('sale','refund') THEN m.qty END),0) AS sold
                  FROM products p LEFT JOIN stock_moves m ON m.product_id=p.id AND m.created_at>=?
                  WHERE p.active=1 GROUP BY p.id""", (since,))
    out = []
    for r in rows:
        per_day = max(r["sold"], 0) / days
        days_left = (r["stock"] / per_day) if per_day > 0 else None
        need = per_day * cover - r["stock"]
        if r["min_stock"] and r["stock"] <= r["min_stock"]:
            need = max(need, r["min_stock"] * 2 - r["stock"])
        if need <= 0:
            continue
        qty = math.ceil(need)
        out.append({**r, "per_day": round(per_day, 2), "days_left": round(days_left, 1) if days_left is not None else None,
                    "qty": qty, "sum": round(qty * (r["cost_price"] or 0), 2),
                    "urgent": days_left is not None and days_left < 3})
    out.sort(key=lambda x: (x["days_left"] if x["days_left"] is not None else 9999))
    return jsonify(items=out, days=days, cover=cover, total=round(sum(x["sum"] for x in out), 2))


@bp.get("/api/abc")
def abc():
    u = M.current_user()
    if not u or u["role"] != "admin":
        return M.err("Доступ только для администратора", 403)
    dfrom = request.args.get("from") or (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    dto = request.args.get("to") or datetime.now().strftime("%Y-%m-%d")
    rows = M.q("""SELECT si.product_id, si.name,
                  SUM(CASE WHEN s.operation='sale' THEN si.total ELSE -si.total END) revenue,
                  SUM(CASE WHEN s.operation='sale' THEN si.total-si.qty*si.cost_price ELSE -(si.total-si.qty*si.cost_price) END) profit,
                  SUM(CASE WHEN s.operation='sale' THEN si.qty ELSE -si.qty END) qty
                  FROM sale_items si JOIN sales s ON s.id=si.sale_id
                  WHERE s.created_at BETWEEN ? AND ? GROUP BY si.product_id HAVING revenue>0 ORDER BY revenue DESC""",
               (dfrom + " 00:00:00", dto + " 23:59:59"))
    total = sum(r["revenue"] for r in rows) or 1
    acc = 0
    summary = {"A": [0, 0], "B": [0, 0], "C": [0, 0]}
    for r in rows:
        acc += r["revenue"]
        r["share"] = round(r["revenue"] / total * 100, 2)
        r["cum"] = round(acc / total * 100, 1)
        r["cls"] = "A" if r["cum"] <= 80 or r is rows[0] else ("B" if r["cum"] <= 95 else "C")
        summary[r["cls"]][0] += 1
        summary[r["cls"]][1] += r["revenue"]
    return jsonify(items=rows, summary=summary)


# ================================================================ telegram endpoints
@bp.post("/api/telegram/test")
def tg_test():
    u = M.current_user()
    if not u or u["role"] != "admin":
        return M.err("Доступ только для администратора", 403)
    ok, msg = tg_send(day_report_text(), sync=True)
    return jsonify(ok=True) if ok else M.err("Telegram: " + msg)


@bp.post("/api/telegram/chat_id")
def tg_find_chat():
    """Находит chat_id: владелец пишет боту /start, затем жмёт кнопку."""
    u = M.current_user()
    if not u or u["role"] != "admin":
        return M.err("Доступ только для администратора", 403)
    token = ((request.json or {}).get("token") or db.get_settings().get("tg_token") or "").strip()
    if not token:
        return M.err("Укажите токен бота")
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15).json()
    except Exception as e:
        return M.err("Нет связи с Telegram: " + str(e))
    if not r.get("ok"):
        return M.err("Неверный токен бота")
    chats = {}
    for upd in r.get("result", []):
        msg = upd.get("message") or upd.get("my_chat_member") or {}
        ch = msg.get("chat") or {}
        if ch.get("id"):
            chats[ch["id"]] = ch.get("title") or " ".join(filter(None, [ch.get("first_name"), ch.get("last_name")])) or ch.get("username")
    if not chats:
        return M.err("Напишите боту /start в Telegram и нажмите ещё раз")
    return jsonify(chats=[{"id": k, "name": v} for k, v in chats.items()])


def register(app_module):
    global M
    M = app_module
    M.app.register_blueprint(bp)
