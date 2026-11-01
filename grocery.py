"""
Продуктовый магазин:
  * сроки годности: партии с датой «годен до», отчёт по истекающим, уценка и списание просрочки
  * штрихкоды упаковок (блок, ящик) с коэффициентом
  * маркировка Asl Belgisi: распознавание кода DataMatrix, контроль скана, передача кодов в ФМ
  * ограничения 18+ и часы продажи алкоголя
  * быстрые кнопки для товаров без штрихкода (хлеб, овощи, фрукты на вес)
  * поставщики и долги поставщикам
"""
import re
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request, session

import db

bp = Blueprint("grocery", __name__)
M = None


# ================================================================ маркировка
def parse_marking(code):
    """Код маркировки -> (штрихкод товара, полный код) или None.
    GS1 DataMatrix: 01 + GTIN(14) + 21 + серийный ... ; пачка сигарет: GTIN(14) + серия(7) + МРЦ(4) + крипто(4)."""
    c = (code or "").replace("\x1d", "").replace("\u001d", "").strip()
    gtin = None
    if len(c) >= 25 and c.startswith("01") and c[2:16].isdigit() and c[16:18] == "21":
        gtin = c[2:16]
    elif len(c) in (25, 29) and c[:14].isdigit() and not c.isdigit():
        gtin = c[:14]
    if not gtin:
        return None
    ean = gtin[1:] if gtin.startswith("0") else gtin
    return ean, gtin, code.strip()


def find_by_scan(con, code):
    """Поиск товара по скану: марка, штрихкод упаковки. Возвращает (product_row, extra) или (None, None)."""
    m = parse_marking(code)
    if m:
        ean, gtin, full = m
        p = con.execute("SELECT * FROM products WHERE active=1 AND barcode IN (?,?,?)", (ean, gtin, gtin.lstrip("0"))).fetchone()
        if not p:
            pb = con.execute("SELECT product_id FROM product_barcodes WHERE barcode IN (?,?)", (ean, gtin)).fetchone()
            if pb:
                p = con.execute("SELECT * FROM products WHERE id=? AND active=1", (pb["product_id"],)).fetchone()
        if p:
            return p, {"marking": full, "scan_qty": 1}
        return None, {"marking_unknown": ean}
    pb = con.execute("SELECT * FROM product_barcodes WHERE barcode=?", (code.strip(),)).fetchone()
    if pb:
        p = con.execute("SELECT * FROM products WHERE id=? AND active=1", (pb["product_id"],)).fetchone()
        if p:
            return p, {"scan_qty": pb["qty"], "pack_name": pb["name"] or f"упаковка ×{pb['qty']:g}"}
    return None, None


def sold_codes(con, codes):
    """Какие из кодов уже проданы (и не возвращены)."""
    bad = []
    for c in codes:
        n = 0
        for r in con.execute("""SELECT s.operation FROM sale_items si JOIN sales s ON s.id=si.sale_id
                                WHERE si.marking LIKE ?""", (f"%{c}%",)):
            n += 1 if r["operation"] == "sale" else -1
        if n > 0:
            bad.append(c)
    return bad


def check_sale_rules(con, lines, settings, user):
    """Проверки перед продажей. lines: [{p: product_row, qty, marking: [..]}]. Возвращает текст ошибки или None."""
    now = datetime.now().strftime("%H:%M")
    af, at = (settings.get("alcohol_from") or "").strip(), (settings.get("alcohol_to") or "").strip()
    all_codes = []
    for l in lines:
        p = l["p"]
        if p.get("restrict_kind") == 2 and af and at:
            ok = af <= now < at if af < at else (now >= af or now < at)
            if not ok:
                return f"Продажа алкоголя разрешена с {af} до {at}: «{p['name']}»"
        codes = [c for c in (l.get("marking") or []) if c]
        if len(set(codes)) != len(codes):
            return f"Один и тот же код маркировки отсканирован дважды: «{p['name']}»"
        if p.get("requires_marking") and settings.get("marking_strict") == "1":
            if abs(l["qty"] - round(l["qty"])) > 1e-9 or len(codes) != int(round(l["qty"])):
                return (f"«{p['name']}» — маркируемый товар: отсканируйте код DataMatrix на каждой единице "
                        f"({len(codes)} из {l['qty']:g})")
        all_codes += codes
    if all_codes:
        bad = sold_codes(con, all_codes)
        if bad:
            return "Этот код маркировки уже продан ранее: " + bad[0][:40] + "…"
    return None


# ================================================================ партии / сроки
def add_batch(con, product_id, qty, expiry=None, source=""):
    if qty <= 0:
        return
    p = con.execute("SELECT shelf_days FROM products WHERE id=?", (product_id,)).fetchone()
    if not expiry and p and p["shelf_days"]:
        expiry = (datetime.now() + timedelta(days=int(p["shelf_days"]))).strftime("%Y-%m-%d")
    if not expiry:
        return
    con.execute("INSERT INTO batches(product_id,qty,expiry,created_at,source) VALUES(?,?,?,?,?)",
                (product_id, qty, expiry, db.now(), source))


def allocate(con, product_ids=None):
    """Раскладывает текущий остаток по партиям: продаётся старое (FEFO), значит остаток — в самых новых партиях.
    Не требует учёта списания партий при каждой продаже, корректно при любых движениях остатка."""
    where = ""
    args = []
    if product_ids:
        where = f"AND p.id IN ({','.join('?' * len(product_ids))})"
        args = list(product_ids)
    prods = {r["id"]: dict(r) for r in con.execute(
        f"""SELECT p.id, p.name, p.barcode, p.stock, p.price, p.cost_price, p.unit_name FROM products p
            WHERE p.active=1 AND p.stock>0 AND EXISTS (SELECT 1 FROM batches b WHERE b.product_id=p.id) {where}""", args)}
    out = []
    for pid, p in prods.items():
        left = p["stock"]
        for b in con.execute("SELECT * FROM batches WHERE product_id=? ORDER BY created_at DESC, id DESC", (pid,)):
            if left <= 1e-9:
                break
            q = min(left, b["qty"])
            left -= q
            out.append({**p, "batch_id": b["id"], "qty": round(q, 3), "expiry": b["expiry"], "received": b["created_at"][:10]})
    return out


@bp.get("/api/expiry")
def expiry_report():
    if not M.current_user():
        return M.err("Требуется вход", 401)
    days = int(M.num(request.args.get("days"), 7))
    today = datetime.now().date()
    con = db.connect()
    rows = allocate(con)
    con.close()
    res = []
    for r in rows:
        if not r["expiry"]:
            continue
        d = (datetime.strptime(r["expiry"], "%Y-%m-%d").date() - today).days
        if d > days:
            continue
        r["days_left"] = d
        r["status"] = "expired" if d < 0 else "today" if d == 0 else "soon"
        r["sum_cost"] = round(r["qty"] * (r["cost_price"] or 0), 2)
        res.append(r)
    res.sort(key=lambda x: x["days_left"])
    return jsonify(items=res, expired=sum(1 for r in res if r["days_left"] < 0),
                   loss=round(sum(r["sum_cost"] for r in res if r["days_left"] < 0), 2))


def expiry_summary():
    """Для Telegram-отчёта и шапки: сколько позиций просрочено / истекает."""
    s = db.get_settings()
    warn = int(M.num(s.get("expiry_warn_days"), 3))
    con = db.connect()
    rows = allocate(con)
    con.close()
    today = datetime.now().date()
    exp = soon = 0
    names = []
    for r in rows:
        if not r["expiry"]:
            continue
        d = (datetime.strptime(r["expiry"], "%Y-%m-%d").date() - today).days
        if d < 0:
            exp += 1
            names.append(r["name"])
        elif d <= warn:
            soon += 1
    return {"expired": exp, "soon": soon, "names": names[:8]}


@bp.post("/api/expiry/writeoff")
def expiry_writeoff():
    u = M.current_user()
    if not u or u["role"] != "admin":
        return M.err("Списание делает администратор", 403)
    d = request.json or {}
    con = db.connect()
    t = db.now()
    n = 0
    for it in d.get("items") or []:
        qty = M.num(it.get("qty"))
        p = con.execute("SELECT id, name, stock, cost_price FROM products WHERE id=?", (it.get("product_id"),)).fetchone()
        if not p or qty <= 0:
            continue
        qty = min(qty, p["stock"])
        con.execute("UPDATE products SET stock=stock-? WHERE id=?", (qty, p["id"]))
        con.execute("INSERT INTO stock_moves(product_id,created_at,type,qty,user_id,note) VALUES(?,?,?,?,?,?)",
                    (p["id"], t, "writeoff", -qty, session["uid"], "Просрочка" + (f" (годен до {it.get('expiry')})" if it.get("expiry") else "")))
        if it.get("batch_id"):   # партия ушла — уменьшаем её объём, чтобы раскладка остатка была точной
            con.execute("UPDATE batches SET qty=MAX(0,qty-?) WHERE id=?", (qty, it["batch_id"]))
        n += 1
        M.extras.audit("Списание просрочки", f"{p['name']}: {qty:g} на {qty * (p['cost_price'] or 0):,.0f}".replace(",", " "), 1, con=con)
    con.commit()
    con.close()
    M.network.kick()
    return jsonify(lines=n)


@bp.post("/api/expiry/markdown")
def expiry_markdown():
    """Уценка товара с истекающим сроком (новая цена + ценник)."""
    u = M.current_user()
    if not u or u["role"] != "admin":
        return M.err("Уценку делает администратор", 403)
    d = request.json or {}
    con = db.connect()
    p = con.execute("SELECT id, name, price FROM products WHERE id=?", (d.get("product_id"),)).fetchone()
    if not p:
        con.close()
        return M.err("Товар не найден")
    if d.get("price"):
        new = round(M.num(d["price"]))
    else:
        new = round(p["price"] * (1 - M.num(d.get("pct"), 30) / 100) / 100) * 100   # округление до 100 сум
    if new <= 0 or new >= p["price"]:
        con.close()
        return M.err("Новая цена должна быть меньше текущей")
    con.execute("UPDATE products SET price=?, updated_at=? WHERE id=?", (new, db.now(), p["id"]))
    M.extras.audit("Уценка (срок годности)", f"{p['name']}: {p['price']:g} → {new:g}", 1, con=con)
    con.commit()
    con.close()
    return jsonify(price=new)


@bp.post("/api/docs/<int:did>/expiry")
def doc_line_expiry(did):
    if not M.current_user():
        return M.err("Требуется вход", 401)
    d = request.json or {}
    con = db.connect()
    con.execute("UPDATE doc_lines SET expiry=? WHERE id=? AND doc_id=?", (d.get("expiry") or None, d.get("line_id"), did))
    con.commit()
    con.close()
    return jsonify(ok=True)


# ================================================================ упаковки и флаги товара
@bp.get("/api/products/<int:pid>/barcodes")
def pack_list(pid):
    if not M.current_user():
        return M.err("Требуется вход", 401)
    return jsonify(M.q("SELECT * FROM product_barcodes WHERE product_id=? ORDER BY qty", (pid,)))


@bp.post("/api/products/<int:pid>/barcodes")
def pack_add(pid):
    u = M.current_user()
    if not u or u["role"] != "admin":
        return M.err("Доступ только для администратора", 403)
    d = request.json or {}
    bc = str(d.get("barcode") or "").strip()
    qty = M.num(d.get("qty"), 1)
    if not bc or qty <= 0:
        return M.err("Укажите штрихкод и количество в упаковке")
    con = db.connect()
    if con.execute("SELECT 1 FROM products WHERE barcode=? AND active=1", (bc,)).fetchone():
        con.close()
        return M.err("Этот штрихкод уже у другого товара")
    try:
        con.execute("INSERT INTO product_barcodes(product_id,barcode,qty,name) VALUES(?,?,?,?)",
                    (pid, bc, qty, (d.get("name") or "").strip()))
        con.commit()
    except Exception:
        con.close()
        return M.err("Такой штрихкод упаковки уже есть")
    con.close()
    return jsonify(ok=True)


@bp.delete("/api/products/barcodes/<int:bid>")
def pack_del(bid):
    u = M.current_user()
    if not u or u["role"] != "admin":
        return M.err("Доступ только для администратора", 403)
    con = db.connect()
    con.execute("DELETE FROM product_barcodes WHERE id=?", (bid,))
    con.commit()
    con.close()
    return jsonify(ok=True)


@bp.get("/api/quick")
def quick_list():
    if not M.current_user():
        return M.err("Требуется вход", 401)
    rows = M.q("""SELECT p.id, p.name, p.price, p.unit_name, p.barcode, p.stock, p.restrict_kind, p.requires_marking,
                  c.name AS category FROM products p LEFT JOIN categories c ON c.id=p.category_id
                  WHERE p.active=1 AND p.quick>0 ORDER BY c.name, p.name LIMIT 60""")
    return jsonify(rows)


# ================================================================ поставщики
@bp.get("/api/suppliers")
def suppliers():
    if not M.current_user():
        return M.err("Требуется вход", 401)
    return jsonify(M.q("""SELECT s.*, (SELECT MAX(created_at) FROM purchases WHERE supplier_id=s.id) last_purchase,
                          (SELECT COUNT(*) FROM purchases WHERE supplier_id=s.id) purchases,
                          (SELECT COALESCE(SUM(total),0) FROM purchases WHERE supplier_id=s.id) turnover
                          FROM suppliers s ORDER BY s.balance DESC, s.name"""))


@bp.post("/api/suppliers")
def supplier_save():
    u = M.current_user()
    if not u or u["role"] != "admin":
        return M.err("Доступ только для администратора", 403)
    d = request.json or {}
    name = (d.get("name") or "").strip()
    if not name:
        return M.err("Укажите название поставщика")
    con = db.connect()
    try:
        if d.get("id"):
            con.execute("UPDATE suppliers SET name=?, phone=?, inn=?, note=? WHERE id=?",
                        (name, d.get("phone", ""), d.get("inn", ""), d.get("note", ""), d["id"]))
            sid = d["id"]
        else:
            sid = con.execute("INSERT INTO suppliers(name,phone,inn,note,created_at) VALUES(?,?,?,?,?)",
                              (name, d.get("phone", ""), d.get("inn", ""), d.get("note", ""), db.now())).lastrowid
        con.commit()
    except Exception:
        con.close()
        return M.err("Поставщик с таким названием уже есть")
    con.close()
    return jsonify(M.q("SELECT * FROM suppliers WHERE id=?", (sid,), one=True))


@bp.get("/api/suppliers/<int:sid>")
def supplier_get(sid):
    if not M.current_user():
        return M.err("Требуется вход", 401)
    s = M.q("SELECT * FROM suppliers WHERE id=?", (sid,), one=True)
    if not s:
        return M.err("Не найден", 404)
    s["ops"] = M.q("""SELECT o.*, u.name AS user FROM supplier_ops o LEFT JOIN users u ON u.id=o.user_id
                      WHERE supplier_id=? ORDER BY o.id DESC LIMIT 200""", (sid,))
    return jsonify(s)


def supplier_purchase(con, supplier_id, total, paid, purchase_id, method="cash"):
    debt = round(total - paid, 2)
    t = db.now()
    con.execute("INSERT INTO supplier_ops(supplier_id,created_at,type,amount,ref_id,user_id,note) VALUES(?,?,?,?,?,?,?)",
                (supplier_id, t, "purchase", total, purchase_id, session.get("uid"), f"Приход №{purchase_id}"))
    if paid:
        con.execute("INSERT INTO supplier_ops(supplier_id,created_at,type,amount,method,ref_id,user_id,note) VALUES(?,?,?,?,?,?,?,?)",
                    (supplier_id, t, "payment", paid, method, purchase_id, session.get("uid"), "Оплата при приходе"))
        if method == "cash":
            sh = M.open_shift(con)
            if sh:
                name = con.execute("SELECT name FROM suppliers WHERE id=?", (supplier_id,)).fetchone()["name"]
                con.execute("""INSERT INTO cash_ops(shift_id,created_at,type,method,amount,category,note,user_id)
                               VALUES(?,?,?,?,?,?,?,?)""", (sh["id"], t, "expense", "cash", paid, "Поставщику", name, session.get("uid")))
    con.execute("UPDATE suppliers SET balance=balance+? WHERE id=?", (debt, supplier_id))


@bp.post("/api/suppliers/<int:sid>/pay")
def supplier_pay(sid):
    u = M.current_user()
    if not u or u["role"] != "admin":
        return M.err("Доступ только для администратора", 403)
    d = request.json or {}
    amount = M.money(d.get("amount"))
    method = d.get("method") if d.get("method") in ("cash", "bank", "card") else "cash"
    if amount <= 0:
        return M.err("Сумма должна быть больше нуля")
    con = db.connect()
    s = con.execute("SELECT * FROM suppliers WHERE id=?", (sid,)).fetchone()
    if not s:
        con.close()
        return M.err("Поставщик не найден", 404)
    t = db.now()
    if method == "cash":
        sh = M.open_shift(con)
        if not sh:
            con.close()
            return M.err("Оплата наличными из кассы — откройте смену", 409)
        con.execute("""INSERT INTO cash_ops(shift_id,created_at,type,method,amount,category,note,user_id)
                       VALUES(?,?,?,?,?,?,?,?)""", (sh["id"], t, "expense", "cash", amount, "Поставщику", s["name"], session["uid"]))
    con.execute("UPDATE suppliers SET balance=balance-? WHERE id=?", (amount, sid))
    con.execute("INSERT INTO supplier_ops(supplier_id,created_at,type,amount,method,user_id,note) VALUES(?,?,?,?,?,?,?)",
                (sid, t, "payment", amount, method, session["uid"], d.get("note") or ""))
    con.commit()
    con.close()
    M.extras.audit("Оплата поставщику", f"{s['name']}: {amount:,.0f} ({method})".replace(",", " "), 1)
    return jsonify(M.q("SELECT * FROM suppliers WHERE id=?", (sid,), one=True))


def register(app_module):
    global M
    M = app_module
    M.app.register_blueprint(bp)
