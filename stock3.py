"""
Склад магазина (этап 3): история цен товара, возврат поставщику, статусы остатков
(В наличии / Мало / Нет / Избыток / Без продаж / Зона риска).
"""
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request, session, render_template_string

import db

bp = Blueprint("stock3", __name__)
M = None

STATUS = {"out": "Нет в наличии", "low": "Мало", "risk": "Зона риска", "nosale": "Без продаж", "over": "Избыток", "in": "В наличии"}


def stock_status(stock, min_stock, sold30, overstock_days=60, risk_days=3):
    """Один статус на товар. Приоритет: нет → мало → зона риска → без продаж → избыток → в наличии."""
    per_day = (sold30 or 0) / 30
    cover = stock / per_day if per_day > 0 and stock > 0 else None
    if stock <= 0:
        return "out", cover
    if min_stock and stock <= min_stock:
        return "low", cover
    if cover is not None and cover < risk_days:
        return "risk", cover
    if not sold30:
        return "nosale", cover
    if cover is not None and cover > overstock_days:
        return "over", cover
    return "in", cover


def _admin():
    u = M.current_user()
    return u and u["role"] == "admin"


# ---------------------------------------------------------------- статусы остатков
@bp.get("/api/stock/status")
def stock_statuses():
    if not M.current_user():
        return M.err("Требуется вход", 401)
    s = db.get_settings()
    over_days = int(M.num(s.get("overstock_days"), 60))
    since = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    sold = {r["product_id"]: r["q"] for r in M.q("""SELECT product_id, -SUM(qty) q FROM stock_moves
                                                    WHERE type='sale' AND created_at>=? GROUP BY product_id""", (since,))}
    rows = M.q("""SELECT p.id, p.name, p.barcode, p.sku, p.brand, p.stock, p.min_stock, p.cost_price, p.price, p.unit_name,
                  COALESCE(c.name,'') AS category FROM products p LEFT JOIN categories c ON c.id=p.category_id WHERE p.active=1""")
    counts = {k: 0 for k in STATUS}
    for r in rows:
        r["sold30"] = round(sold.get(r["id"], 0), 3)
        st, cover = stock_status(r["stock"], r["min_stock"], r["sold30"], over_days)
        r["status"], r["status_title"], r["cover_days"] = st, STATUS[st], round(cover, 1) if cover else None
        r["value"] = round(max(r["stock"], 0) * (r["cost_price"] or 0))
        counts[st] += 1
    want = request.args.get("status")
    if want:
        rows = [r for r in rows if r["status"] == want]
    order = list(STATUS)
    rows.sort(key=lambda r: (order.index(r["status"]), r["name"]))
    return jsonify(items=rows, counts=counts, titles=STATUS)


# ---------------------------------------------------------------- история цен
@bp.get("/api/products/<int:pid>/prices")
def price_history(pid):
    if not M.current_user():
        return M.err("Требуется вход", 401)
    return jsonify(M.q("""SELECT h.*, u.name AS user FROM price_history h LEFT JOIN users u ON u.id=h.user_id
                          WHERE h.product_id=? ORDER BY h.id DESC LIMIT 200""", (pid,)))


# ---------------------------------------------------------------- возврат поставщику
REASONS = ["Брак", "Истёк срок годности", "Пересорт / не тот товар", "Излишек поставки", "Недовложение в упаковке", "Другое"]


@bp.get("/api/supplier_returns")
def returns_list():
    if not _admin():
        return M.err("Доступ только для администратора", 403)
    return jsonify(items=M.q("""SELECT r.*, s.name AS supplier, u.name AS user FROM supplier_returns r
                                LEFT JOIN suppliers s ON s.id=r.supplier_id LEFT JOIN users u ON u.id=r.user_id
                                ORDER BY r.id DESC LIMIT 200"""), reasons=REASONS)


@bp.post("/api/supplier_returns")
def return_create():
    if not _admin():
        return M.err("Возврат поставщику оформляет администратор", 403)
    d = request.json or {}
    sid = int(M.num(d.get("supplier_id")))
    reason = (d.get("reason") or "").strip()
    if not sid:
        return M.err("Выберите поставщика")
    if not reason:
        return M.err("Укажите причину возврата")
    items = [i for i in d.get("items") or [] if M.num(i.get("qty")) > 0]
    if not items:
        return M.err("Добавьте товары")
    con = db.connect()
    sup = con.execute("SELECT * FROM suppliers WHERE id=?", (sid,)).fetchone()
    if not sup:
        con.close()
        return M.err("Поставщик не найден", 404)
    allow_neg = db.get_settings().get("allow_negative_stock") != "0"
    lines = []
    for i in items:
        p = con.execute("SELECT id, name, stock, cost_price FROM products WHERE id=?", (i.get("product_id"),)).fetchone()
        if not p:
            con.close()
            return M.err("Товар не найден")
        qty = M.num(i["qty"])
        if not allow_neg and qty > p["stock"] + 1e-9:
            con.close()
            return M.err(f"Нельзя вернуть больше остатка: {p['name']} (есть {p['stock']:g})")
        cost = M.money(i.get("cost")) if i.get("cost") not in (None, "") else (p["cost_price"] or 0)
        lines.append((p, qty, cost))
    t = db.now()
    total = round(sum(q * c for _, q, c in lines), 2)
    rid = con.execute("""INSERT INTO supplier_returns(created_at,supplier_id,user_id,reason,note,lines,qty,total)
                         VALUES(?,?,?,?,?,?,?,?)""", (t, sid, session["uid"], reason, d.get("note", ""), len(lines),
                                                      sum(q for _, q, _ in lines), total)).lastrowid
    for p, qty, cost in lines:
        con.execute("INSERT INTO supplier_return_lines(return_id,product_id,name,qty,cost) VALUES(?,?,?,?,?)", (rid, p["id"], p["name"], qty, cost))
        con.execute("UPDATE products SET stock=stock-? WHERE id=?", (qty, p["id"]))
        con.execute("INSERT INTO stock_moves(product_id,created_at,type,qty,ref_id,user_id,note) VALUES(?,?,?,?,?,?,?)",
                    (p["id"], t, "supplier_return", -qty, rid, session["uid"], f"Возврат поставщику №{rid}: {reason}"))
    # долг перед поставщиком уменьшается на сумму возврата
    con.execute("UPDATE suppliers SET balance=balance-? WHERE id=?", (total, sid))
    con.execute("INSERT INTO supplier_ops(supplier_id,created_at,type,amount,ref_id,user_id,note) VALUES(?,?,?,?,?,?,?)",
                (sid, t, "return", total, rid, session["uid"], f"Возврат №{rid}: {reason}"))
    con.commit()
    con.close()
    M.extras.audit("Возврат поставщику", f"№{rid} {sup['name']}: {len(lines)} поз. на {total:,.0f} ({reason})".replace(",", " "), 1)
    try:
        M.network.kick()
    except Exception:
        pass
    return jsonify(id=rid, total=total, balance=M.q("SELECT balance FROM suppliers WHERE id=?", (sid,), one=True)["balance"])


ACT = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Акт возврата №{{r.id}}</title>
<style>body{font:13px/1.45 Arial,sans-serif;margin:24px;color:#000}h2{margin:0 0 4px}table{width:100%;border-collapse:collapse;margin-top:12px}
th,td{border:1px solid #000;padding:5px 7px;text-align:left}td.n,th.n{text-align:right}.sig{display:flex;justify-content:space-between;margin-top:40px}
.sig div{width:45%;border-top:1px solid #000;padding-top:4px;font-size:12px}@media print{button{display:none}}</style></head><body>
<button onclick="print()">Печать</button>
<h2>Акт возврата товара поставщику № {{r.id}}</h2>
<div>от {{r.created_at[:16]}} · {{s.store_name or 'Магазин'}}{% if s.inn %}, ИНН {{s.inn}}{% endif %}</div>
<div>Поставщик: <b>{{r.supplier}}</b>{% if r.inn %}, ИНН {{r.inn}}{% endif %} · Причина: <b>{{r.reason}}</b>{% if r.note %} · {{r.note}}{% endif %}</div>
<table><tr><th>№</th><th>Товар</th><th>Штрихкод</th><th class="n">Кол-во</th><th class="n">Цена закупки</th><th class="n">Сумма</th></tr>
{% for l in lines %}<tr><td>{{loop.index}}</td><td>{{l.name}}</td><td>{{l.barcode or ''}}</td><td class="n">{{'%g'|format(l.qty)}}</td>
<td class="n">{{'{:,.0f}'.format(l.cost).replace(',',' ')}}</td><td class="n">{{'{:,.0f}'.format(l.qty*l.cost).replace(',',' ')}}</td></tr>{% endfor %}
<tr><td colspan="5"><b>Итого</b></td><td class="n"><b>{{'{:,.0f}'.format(r.total).replace(',',' ')}}</b></td></tr></table>
<div class="sig"><div>Сдал: {{r.user or ''}}</div><div>Принял (представитель поставщика)</div></div>
{% if auto %}<script>setTimeout(()=>print(),300)</script>{% endif %}</body></html>"""


@bp.get("/supplier-return/<int:rid>")
def return_act(rid):
    if not M.current_user():
        return "Требуется вход", 401
    r = M.q("""SELECT r.*, s.name AS supplier, s.inn, u.name AS user FROM supplier_returns r LEFT JOIN suppliers s ON s.id=r.supplier_id
               LEFT JOIN users u ON u.id=r.user_id WHERE r.id=?""", (rid,), one=True)
    if not r:
        return "Не найдено", 404
    lines = M.q("""SELECT l.*, p.barcode FROM supplier_return_lines l LEFT JOIN products p ON p.id=l.product_id WHERE l.return_id=?""", (rid,))
    return render_template_string(ACT, r=r, lines=lines, s=db.get_settings(), auto=request.args.get("print") == "1")


def register(app_module):
    global M
    M = app_module
    M.app.register_blueprint(bp)
