"""
Цены: акции (скидка %, «N+M в подарок») по товарам/категориям, датам, часам и дням недели;
оптовые цены; переоценка с печатью ценников.
Акции считаются на сервере при каждой продаже — касса показывает тот же расчёт заранее.
"""
import json
from datetime import datetime

from flask import Blueprint, jsonify, request, session

import db

bp = Blueprint("pricing", __name__)
M = None


def _active(con, now=None):
    now = now or datetime.now()
    d, t, wd = now.strftime("%Y-%m-%d"), now.strftime("%H:%M"), str(now.isoweekday())
    out = []
    for r in con.execute("SELECT * FROM promos WHERE active=1 ORDER BY id"):
        r = dict(r)
        if r["date_from"] and d < r["date_from"]:
            continue
        if r["date_to"] and d > r["date_to"]:
            continue
        if r["time_from"] and r["time_to"] and not (r["time_from"] <= t < r["time_to"]):
            continue
        if r["weekdays"] and wd not in r["weekdays"].split(","):
            continue
        r["products"] = json.loads(r["products"] or "[]")
        r["categories"] = json.loads(r["categories"] or "[]")
        out.append(r)
    return out


def _matches(promo, p):
    if not promo["products"] and not promo["categories"]:
        return True
    return p["id"] in promo["products"] or (p.get("category_id") in promo["categories"])


def promo_discount(promo, qty, price):
    if promo["kind"] == "percent":
        return round(qty * price * promo["value"] / 100)
    if promo["kind"] == "nplus" and promo["n"] > 0 and promo["m"] > 0:
        free = int(qty // (promo["n"] + promo["m"])) * promo["m"]
        return round(free * price)
    return 0


def apply_promos(con, lines):
    """Добавляет к строкам лучшую из действующих акций (одна акция на строку). Возвращает сумму скидок по акциям."""
    promos = _active(con)
    total = 0
    for l in lines:
        best, best_d = None, 0
        for pr in promos:
            if _matches(pr, l["p"]):
                dsc = promo_discount(pr, l["qty"], l["price"])
                if dsc > best_d:
                    best, best_d = pr, dsc
        if best:
            room = round(l["qty"] * l["price"] - l["discount"], 2)
            best_d = min(best_d, room)
            if best_d > 0:
                l["discount"] = round(l["discount"] + best_d, 2)
                l["promo"] = best["name"]
                total += best_d
    return total


def price_allowed(p, price):
    """Цена из чека допустима без подтверждения: розничная или оптовая."""
    return abs(price - (p["price"] or 0)) < 0.01 or ((p.get("wholesale_price") or 0) > 0 and abs(price - p["wholesale_price"]) < 0.01)


# ================================================================ API: акции
@bp.get("/api/promos")
def promos_list():
    if not M.current_user():
        return M.err("Требуется вход", 401)
    con = db.connect()
    rows = [dict(r) for r in con.execute("SELECT * FROM promos ORDER BY active DESC, id DESC")]
    active_ids = {p["id"] for p in _active(con)}
    con.close()
    for r in rows:
        r["products"] = json.loads(r["products"] or "[]")
        r["categories"] = json.loads(r["categories"] or "[]")
        r["running"] = r["id"] in active_ids
    return jsonify(rows)


@bp.get("/api/promos/active")
def promos_active():
    if not M.current_user():
        return M.err("Требуется вход", 401)
    con = db.connect()
    rows = _active(con)
    con.close()
    return jsonify(rows)


@bp.post("/api/promos")
def promo_save():
    u = M.current_user()
    if not u or u["role"] != "admin":
        return M.err("Доступ только для администратора", 403)
    d = request.json or {}
    name = (d.get("name") or "").strip()
    kind = d.get("kind") if d.get("kind") in ("percent", "nplus") else "percent"
    if not name:
        return M.err("Укажите название акции")
    if kind == "percent" and not (0 < M.num(d.get("value")) <= 90):
        return M.err("Скидка от 1 до 90%")
    if kind == "nplus" and (int(M.num(d.get("n"))) < 1 or int(M.num(d.get("m"))) < 1):
        return M.err("Укажите N и M, например 2+1")
    vals = {"name": name, "kind": kind, "value": M.num(d.get("value")), "n": int(M.num(d.get("n"))), "m": int(M.num(d.get("m"))),
            "products": json.dumps([int(x) for x in d.get("products") or []]),
            "categories": json.dumps([int(x) for x in d.get("categories") or []]),
            "date_from": d.get("date_from") or None, "date_to": d.get("date_to") or None,
            "time_from": d.get("time_from") or None, "time_to": d.get("time_to") or None,
            "weekdays": ",".join(str(int(x)) for x in d.get("weekdays") or []), "active": int(d.get("active", 1))}
    con = db.connect()
    if d.get("id"):
        con.execute(f"UPDATE promos SET {','.join(k + '=?' for k in vals)} WHERE id=?", list(vals.values()) + [d["id"]])
    else:
        vals["created_at"] = db.now()
        con.execute(f"INSERT INTO promos({','.join(vals)}) VALUES({','.join('?' * len(vals))})", list(vals.values()))
    con.commit()
    con.close()
    M.extras.audit("Акция сохранена", name, 1)
    return jsonify(ok=True)


@bp.delete("/api/promos/<int:pid>")
def promo_delete(pid):
    u = M.current_user()
    if not u or u["role"] != "admin":
        return M.err("Доступ только для администратора", 403)
    con = db.connect()
    con.execute("DELETE FROM promos WHERE id=?", (pid,))
    con.commit()
    con.close()
    return jsonify(ok=True)


# ================================================================ API: переоценка
def _round(v, step):
    step = max(1, int(step or 1))
    return round(v / step) * step


@bp.post("/api/revalue/preview")
def revalue_preview():
    u = M.current_user()
    if not u or u["role"] != "admin":
        return M.err("Доступ только для администратора", 403)
    d = request.json or {}
    return jsonify(items=_revalue_items(d))


def _revalue_items(d):
    where, args = ["active=1"], []
    if d.get("ids"):
        ids = [int(x) for x in d["ids"]]
        where.append(f"id IN ({','.join('?' * len(ids))})")
        args += ids
    elif d.get("category_id"):
        where.append("category_id=?")
        args.append(int(d["category_id"]))
    rows = M.q(f"SELECT id, name, barcode, price, cost_price, stock, hq FROM products WHERE {' AND '.join(where)} ORDER BY name", args)
    mode, val, step = d.get("mode", "pct"), M.num(d.get("value")), d.get("round", 100)
    out = []
    for r in rows:
        if mode == "pct":
            new = _round(r["price"] * (1 + val / 100), step)
        elif mode == "markup":                       # наценка от себестоимости
            if not r["cost_price"]:
                continue
            new = _round(r["cost_price"] * (1 + val / 100), step)
        else:
            new = _round(val, step)
        if new <= 0 or abs(new - r["price"]) < 0.01:
            continue
        out.append({**r, "new_price": new, "diff": new - r["price"], "diff_sum": round((new - r["price"]) * max(r["stock"], 0), 2)})
    return out


@bp.post("/api/revalue/apply")
def revalue_apply():
    u = M.current_user()
    if not u or u["role"] != "admin":
        return M.err("Доступ только для администратора", 403)
    d = request.json or {}
    items = d.get("items") or []
    if not items:
        return M.err("Нет товаров для переоценки")
    con = db.connect()
    t = db.now()
    hq_locked = M.network.connected()
    rid = con.execute("INSERT INTO revaluations(created_at,user_id,note,lines,diff_sum) VALUES(?,?,?,?,?)",
                      (t, session["uid"], d.get("note") or "", 0, 0)).lastrowid
    n, total, skipped = 0, 0.0, 0
    ids = []
    for it in items:
        p = con.execute("SELECT id, price, stock, hq FROM products WHERE id=?", (it["id"],)).fetchone()
        new = M.num(it.get("new_price"))
        if not p or new <= 0:
            continue
        if hq_locked and p["hq"]:
            skipped += 1
            continue
        con.execute("INSERT INTO revaluation_lines(revaluation_id,product_id,old_price,new_price,stock) VALUES(?,?,?,?,?)",
                    (rid, p["id"], p["price"], new, p["stock"]))
        con.execute("UPDATE products SET price=?, updated_at=? WHERE id=?", (new, t, p["id"]))
        n += 1
        total += (new - p["price"]) * max(p["stock"], 0)
        ids.append(p["id"])
    con.execute("UPDATE revaluations SET lines=?, diff_sum=? WHERE id=?", (n, round(total, 2), rid))
    con.commit()
    con.close()
    M.extras.audit("Переоценка", f"№{rid}: {n} товаров, разница по остатку {total:+,.0f}".replace(",", " "), 1)
    return jsonify(id=rid, lines=n, diff_sum=round(total, 2), ids=ids, skipped=skipped)


@bp.get("/api/revaluations")
def revaluations():
    u = M.current_user()
    if not u or u["role"] != "admin":
        return M.err("Доступ только для администратора", 403)
    return jsonify(M.q("""SELECT r.*, u.name AS user FROM revaluations r LEFT JOIN users u ON u.id=r.user_id
                          WHERE r.lines>0 ORDER BY r.id DESC LIMIT 50"""))


def register(app_module):
    global M
    M = app_module
    M.app.register_blueprint(bp)
