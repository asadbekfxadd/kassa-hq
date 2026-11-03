"""
Сеть магазинов (сторона кассы):
  * синхронизация с Центром (hq/hq.py): каталог вниз, продажи/остатки/накладные вверх
  * поступления по накладным с приёмкой сканером, акт расхождений
  * импорт накладной поставщика из Excel для сверки сканером
  * отгрузка (перемещение) в другой магазин сети
Касса продолжает работать без интернета — всё досылается при появлении связи.
"""
import json
import threading
import time
import uuid
from datetime import datetime

import requests
from flask import Blueprint, jsonify, request, session

import db

bp = Blueprint("network", __name__)
M = None
SYNC_LOCK = threading.Lock()
VERSION = "3.0"


def connected(s=None):
    s = s or db.get_settings()
    return bool((s.get("hq_url") or "").strip() and (s.get("hq_token") or "").strip())


def set_settings(con, **kw):
    for k, v in kw.items():
        con.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (k, str(v)))


# ================================================================ SYNC
def sync_once():
    """Один цикл обмена с Центром. Возвращает dict со статистикой или кидает исключение."""
    if not SYNC_LOCK.acquire(timeout=60):
        raise RuntimeError("Синхронизация уже идёт")
    try:
        return _sync()
    finally:
        SYNC_LOCK.release()


def _sync():
    s = db.get_settings()
    if not connected(s):
        return {"skipped": True}
    url = s["hq_url"].strip().rstrip("/") + "/sync"
    con = db.connect()
    push_started = db.now()

    sales = [dict(r) for r in con.execute("""SELECT s.id, s.uid, s.created_at, s.operation, s.total, s.cash, s.card, s.debt,
              s.discount, s.fiscal_status, s.receipt_seq, s.qr, s.qr_provider, s.promo_disc, u.name AS cashier
              FROM sales s LEFT JOIN users u ON u.id=s.user_id
              WHERE s.synced=0 ORDER BY s.id LIMIT 300""")]
    for x in sales:
        x["items"] = [dict(r) for r in con.execute("""SELECT p.uid AS product_uid, si.name, si.qty, si.price, si.total, si.cost_price
                       FROM sale_items si LEFT JOIN products p ON p.id=si.product_id WHERE si.sale_id=?""", (x["id"],))]
    new_products = [dict(r) for r in con.execute("""SELECT p.uid, p.name, p.barcode, p.sku, c.name AS category, p.unit_name,
              p.units_code, p.spic, p.package_code, p.owner_type, p.vat_percent, p.cost_price, p.price, p.min_stock
              FROM products p LEFT JOIN categories c ON c.id=p.category_id WHERE p.hq=0 AND p.active=1""")]
    since_stock = s.get("hq_stock_push") or "1970-01-01 00:00:00"
    stock = [dict(r) for r in con.execute("SELECT uid, stock AS qty FROM products WHERE stock_at>=? OR ?=''",
                                           (since_stock, s.get("hq_stock_push") or ""))]
    docs = []
    for d in con.execute("""SELECT * FROM docs WHERE synced=0 AND (status IN ('received','shipped')
                            OR (status='to_ship' AND type='out_transfer' AND picking=1))""").fetchall():
        d = dict(d)
        lines = [dict(r) for r in con.execute("""SELECT COALESCE(l.product_uid,p.uid) AS product_uid, l.qty_expected AS qty,
                     l.qty_fact, l.cost FROM doc_lines l LEFT JOIN products p ON p.id=l.product_id WHERE doc_id=?""", (d["id"],))]
        if d["status"] == "shipped":
            for l in lines:
                l["qty"] = l["qty_fact"]
        docs.append({"uid": d["uid"], "number": d["number"], "status": "preparing" if d["status"] == "to_ship" else d["status"], "to_store_uid": d["partner_uid"],
                     "supplier": d["partner"] if d["type"] == "local_supply" else "", "comment": d["note"] or "",
                     "created_at": d["created_at"], "received_at": d["done_at"], "lines": lines})
    # деньги: кассовые операции (расходы, внесения, изъятия, погашения долгов) и оплаты поставщикам
    money = [dict(r, kind="cash_op") for r in con.execute("""SELECT c.uid, c.created_at, c.type, c.method, c.amount, c.category, c.note,
                 u.name AS user FROM cash_ops c LEFT JOIN users u ON u.id=c.user_id WHERE c.synced=0 ORDER BY c.id LIMIT 500""")]
    money += [dict(r, kind="supplier_op") for r in con.execute("""SELECT o.uid, o.created_at, o.type, o.method, o.amount, s.name AS category,
                 o.note, u.name AS user FROM supplier_ops o LEFT JOIN suppliers s ON s.id=o.supplier_id LEFT JOIN users u ON u.id=o.user_id
                 WHERE o.synced=0 AND o.type IN ('payment','return') ORDER BY o.id LIMIT 500""")]
    sh = M.open_shift(con)
    info = {"version": VERSION, "app_version": M.updater.local_version(), "shift": bool(sh),
            "cashier": (con.execute("SELECT name FROM users WHERE id=?", (sh["user_id"],)).fetchone() or {"name": ""})["name"] if sh else "",
            "unsent_fiscal": con.execute("SELECT COUNT(*) n FROM sales WHERE fiscal_status IN ('pending','error')").fetchone()["n"],
            "customers_debt": con.execute("SELECT COALESCE(SUM(debt),0) v FROM customers WHERE debt>0").fetchone()["v"],
            "suppliers_debt": con.execute("SELECT COALESCE(SUM(balance),0) v FROM suppliers WHERE balance>0").fetchone()["v"],
            "debtors": con.execute("SELECT COUNT(*) n FROM customers WHERE debt>0").fetchone()["n"],
            "cash_in_drawer": M.shift_report(sh["id"])["expected_cash"] if sh else None,
            "stock_value": con.execute("SELECT COALESCE(SUM(stock*cost_price),0) v FROM products WHERE active=1 AND stock>0").fetchone()["v"]}
    con.close()

    body = {"since": s.get("hq_since") or "", "sales": sales, "new_products": new_products, "stock": stock, "docs": docs, "money": money,
            "info": info}
    try:
        r = requests.post(url, json=body, headers={"X-Store-Token": s["hq_token"].strip()}, timeout=40)
    except requests.RequestException as e:
        raise RuntimeError("Нет связи с Центром: " + e.__class__.__name__)
    if r.status_code == 401:
        raise RuntimeError("Центр не принял токен магазина")
    if not r.ok:
        raise RuntimeError(f"Центр ответил ошибкой {r.status_code}")
    res = r.json()

    con = db.connect()
    acks = res.get("acks", {})
    for uid in acks.get("sales", []):
        con.execute("UPDATE sales SET synced=1 WHERE uid=? AND synced=0", (uid,))
    for a in acks.get("products", []):
        if a["hq_uid"] != a["uid"]:
            # такой штрихкод уже был в Центре — берём его uid
            if not con.execute("SELECT 1 FROM products WHERE uid=?", (a["hq_uid"],)).fetchone():
                con.execute("UPDATE products SET uid=? WHERE uid=?", (a["hq_uid"], a["uid"]))
        con.execute("UPDATE products SET hq=1 WHERE uid IN (?,?)", (a["uid"], a["hq_uid"]))
    for uid in acks.get("money", []):
        con.execute("UPDATE cash_ops SET synced=1 WHERE uid=?", (uid,))
        con.execute("UPDATE supplier_ops SET synced=1 WHERE uid=?", (uid,))
    sent_status = {x["uid"]: ("to_ship" if x["status"] == "preparing" else x["status"]) for x in docs}
    for uid in acks.get("docs", []):
        # только если документ не изменился, пока шёл обмен (например, отгрузку провели во время синхронизации)
        con.execute("UPDATE docs SET synced=1 WHERE uid=? AND status=?", (uid, sent_status.get(uid, "")))

    # ---- каталог из Центра
    cats = {}
    applied_p = 0
    for p in res.get("products", []):
        cat_id = None
        if p.get("category"):
            if p["category"] not in cats:
                con.execute("INSERT OR IGNORE INTO categories(name) VALUES(?)", (p["category"],))
                cats[p["category"]] = con.execute("SELECT id FROM categories WHERE name=?", (p["category"],)).fetchone()["id"]
            cat_id = cats[p["category"]]
        vals = {"name": p["name"], "barcode": p.get("barcode") or None, "sku": p.get("sku") or "", "category_id": cat_id,
                "unit_name": p.get("unit_name") or "шт", "units_code": p.get("units_code") or 0, "spic": p.get("spic") or "",
                "package_code": p.get("package_code") or "", "owner_type": p.get("owner_type") or 0,
                "vat_percent": p.get("vat_percent") or 0, "cost_price": p.get("cost_price") or 0,
                "price": p.get("price") or 0, "active": p.get("active", 1), "hq": 1,
                "requires_marking": p.get("requires_marking") or 0, "restrict_kind": p.get("restrict_kind") or 0,
                "shelf_days": p.get("shelf_days") or 0, "wholesale_price": p.get("wholesale_price") or 0,
                "brand": p.get("brand") or ""}
        ex = con.execute("SELECT id FROM products WHERE uid=?", (p["uid"],)).fetchone()
        if not ex and vals["barcode"]:
            ex = con.execute("SELECT id FROM products WHERE barcode=?", (vals["barcode"],)).fetchone()
            if ex:
                con.execute("UPDATE products SET uid=? WHERE id=?", (p["uid"], ex["id"]))
        if not vals["active"]:
            vals["barcode"] = None
        if ex:
            # штрихкод может быть занят другим локальным товаром — освобождаем
            if vals["barcode"]:
                con.execute("UPDATE products SET barcode=NULL WHERE barcode=? AND id<>?", (vals["barcode"], ex["id"]))
            con.execute(f"UPDATE products SET {','.join(k + '=?' for k in vals)}, updated_at=? WHERE id=?",
                        list(vals.values()) + [db.now(), ex["id"]])
        elif vals["active"]:
            if vals["barcode"]:
                con.execute("UPDATE products SET barcode=NULL WHERE barcode=?", (vals["barcode"],))
            vals["uid"] = p["uid"]
            con.execute(f"INSERT INTO products({','.join(vals)}) VALUES({','.join('?' * len(vals))})", list(vals.values()))
        applied_p += 1

    # ---- накладные для приёмки / отгрузки
    my_uid = (res.get("store") or {}).get("uid")
    new_docs = 0
    for d in res.get("docs", []):
        if con.execute("SELECT 1 FROM docs WHERE uid=?", (d["uid"],)).fetchone():
            continue
        outgoing = d["status"] == "to_ship"
        if outgoing:
            typ, status, partner = "out_transfer", "to_ship", d.get("to_name")
        else:
            typ = "in_transfer" if d["type"] == "transfer" else "in_supply"
            status, partner = "expected", (d.get("from_name") if d["type"] == "transfer" else (d.get("supplier") or "Поставщик"))
        partner_uid = None
        if outgoing:
            for st in res.get("stores", []):
                if st["name"] == d.get("to_name"):
                    partner_uid = st["uid"]
        did = con.execute("""INSERT INTO docs(uid,number,type,status,partner,partner_uid,note,created_at,synced)
                             VALUES(?,?,?,?,?,?,?,?,1)""",
                          (d["uid"], d.get("number"), typ, status, partner, partner_uid, d.get("note") or "",
                           d.get("created_at") or db.now())).lastrowid
        for l in d.get("lines", []):
            p = con.execute("SELECT id,name,barcode FROM products WHERE uid=?", (l["product_uid"],)).fetchone()
            con.execute("""INSERT INTO doc_lines(doc_id,product_id,product_uid,name,barcode,qty_expected,qty_fact,cost,price)
                           VALUES(?,?,?,?,?,?,0,?,?)""",
                        (did, p["id"] if p else None, l["product_uid"], (p["name"] if p else l.get("name")) or "?",
                         (p["barcode"] if p else l.get("barcode")), l.get("qty") or 0, l.get("cost") or 0, l.get("price") or 0))
        new_docs += 1
        M_extras().audit("Новая накладная из Центра", f"{d.get('number')} ({'отгрузка' if outgoing else 'поступление'})", 0, 0, con=con)

    for uid in res.get("cancelled") or []:      # отменено в Центре до отгрузки/приёмки
        dd = con.execute("SELECT id, number, status FROM docs WHERE uid=?", (uid,)).fetchone()
        if dd and dd["status"] in ("expected", "receiving", "to_ship"):
            con.execute("UPDATE docs SET status='cancelled', synced=1 WHERE id=?", (dd["id"],))
            M_extras().audit("Накладная отменена Центром", dd["number"] or "", 1, 0, con=con)

    applied = apply_pending(con, res.get("pending") or {})

    set_settings(con, hq_since=res.get("server_time") or "", hq_stock_push=push_started, hq_last_ok=db.now(), hq_error="",
                 hq_store_name=(res.get("store") or {}).get("name", ""),
                 hq_stores=json.dumps(res.get("stores", []), ensure_ascii=False))
    con.commit()
    con.close()
    if any(applied.values()):
        try:
            requests.post(s["hq_url"].strip().rstrip("/") + "/sync/ack", json=applied,
                          headers={"X-Store-Token": s["hq_token"].strip()}, timeout=40)
        except requests.RequestException:
            pass   # Центр пришлёт ещё раз, применение идемпотентно
    return {"sales": len(acks.get("sales", [])), "products": applied_p, "new_docs": new_docs, "stock": len(stock),
            "docs_sent": len(acks.get("docs", [])), "migrated": {k: len(v) for k, v in applied.items()}}


def apply_pending(con, pend):
    """Данные переезда из Центра: остатки, клиенты с бонусами/долгами, история продаж."""
    t = db.now()
    done = {"stock": [], "customers": [], "history": []}
    imported_key = "migrated_ids"
    seen = set(json.loads(db.get_settings(con).get(imported_key) or "[]"))
    for r in pend.get("stock", []):
        key = f"s{r['id']}"
        p = con.execute("SELECT id, stock FROM products WHERE uid=?", (r["uid"],)).fetchone()
        if key not in seen and p:
            new = r["qty"] if r["mode"] == "set" else p["stock"] + r["qty"]
            diff = round(new - p["stock"], 3)
            if diff:
                con.execute("UPDATE products SET stock=? WHERE id=?", (new, p["id"]))
                con.execute("INSERT INTO stock_moves(product_id,created_at,type,qty,user_id,note) VALUES(?,?,?,?,?,?)",
                            (p["id"], t, "inventory", diff, 0, "Перенос остатков из прошлой программы"))
            else:
                con.execute("UPDATE products SET stock_at=? WHERE id=?", (t, p["id"]))   # всё равно отчитаться Центру
            seen.add(key)
        if p:
            done["stock"].append(r["id"])
    for r in pend.get("customers", []):
        key = f"c{r['id']}"
        d = r["data"]
        if key not in seen:
            ex = con.execute("SELECT * FROM customers WHERE phone=?", (d.get("phone"),)).fetchone() if d.get("phone") else None
            if not ex and d.get("card"):
                ex = con.execute("SELECT * FROM customers WHERE card=?", (d["card"],)).fetchone()
            if not ex and not d.get("phone"):
                ex = con.execute("SELECT * FROM customers WHERE name=? AND phone IS NULL", (d["name"],)).fetchone()
            if ex:
                cid = ex["id"]
                con.execute("""UPDATE customers SET name=COALESCE(NULLIF(?,''),name), card=COALESCE(?,card), discount_pct=?,
                               birthday=COALESCE(?,birthday) WHERE id=?""",
                            (d["name"], d.get("card"), d.get("discount_pct") or ex["discount_pct"] or 0, d.get("birthday"), cid))
                old_b, old_d = ex["bonus"], ex["debt"]
            else:
                if d.get("card") and con.execute("SELECT 1 FROM customers WHERE card=?", (d["card"],)).fetchone():
                    d["card"] = None
                cid = con.execute("""INSERT INTO customers(name,phone,card,discount_pct,birthday,note,created_at)
                                     VALUES(?,?,?,?,?,?,?)""", (d["name"], d.get("phone"), d.get("card"), d.get("discount_pct") or 0,
                                                                 d.get("birthday"), "Перенесён из прошлой программы", t)).lastrowid
                old_b = old_d = 0
            for typ, field, old in (("bonus_add", "bonus", old_b), ("debt_add", "debt", old_d)):
                delta = round((d.get(field) or 0) - old, 2)
                if delta:
                    con.execute(f"UPDATE customers SET {field}=? WHERE id=?", (d.get(field) or 0, cid))
                    con.execute("INSERT INTO customer_ops(customer_id,created_at,type,amount,user_id,note) VALUES(?,?,?,?,?,?)",
                                (cid, t, typ if delta > 0 else typ.replace("_add", "_back"), abs(delta), 0,
                                 "Перенос баланса из прошлой программы"))
            seen.add(key)
        done["customers"].append(r["id"])
    for r in pend.get("history", []):
        key = f"h{r['id']}"
        if key not in seen:
            p = con.execute("SELECT id FROM products WHERE uid=?", (r["uid"],)).fetchone()
            if not p:
                continue
            # только журнал движения (для автозаказа и неликвида) — остаток не меняется
            con.execute("INSERT INTO stock_moves(product_id,created_at,type,qty,user_id,note) VALUES(?,?,?,?,?,?)",
                        (p["id"], r["day"] + " 12:00:00", "sale", -abs(r["qty"]), 0, "История продаж из прошлой программы"))
            seen.add(key)
        done["history"].append(r["id"])
    if any(done.values()):
        set_settings(con, **{imported_key: json.dumps(sorted(seen)[-200000:])})
        n = sum(len(v) for v in done.values())
        M.extras.audit("Получены данные переезда из Центра",
                       f"остатки {len(done['stock'])}, клиенты {len(done['customers'])}, история {len(done['history'])}", 1, 0, con=con)
    return done


def M_extras():
    return M.extras


def sync_worker():
    while True:
        try:
            if connected():
                sync_once()
        except Exception as e:
            try:
                con = db.connect()
                set_settings(con, hq_error=str(e)[:300])
                con.commit()
                con.close()
            except Exception:
                pass
        time.sleep(45)


def kick():
    """Синхронизация сразу (в фоне) — после продажи, приёмки и т.п."""
    if connected():
        threading.Thread(target=lambda: _safe(sync_once), daemon=True).start()


def _safe(fn):
    try:
        fn()
    except Exception:
        pass


@bp.get("/api/network/status")
def net_status():
    if not M.current_user():
        return M.err("Требуется вход", 401)
    s = db.get_settings()
    unsent = M.q("SELECT COUNT(*) n FROM sales WHERE synced=0", one=True)["n"] if connected(s) else 0
    expected = M.q("SELECT COUNT(*) n FROM docs WHERE status IN ('expected','receiving','to_ship')", one=True)["n"]
    return jsonify(connected=connected(s), store=s.get("hq_store_name"), last_ok=s.get("hq_last_ok"),
                   error=s.get("hq_error"), unsent=unsent, expected_docs=expected,
                   stores=json.loads(s.get("hq_stores") or "[]"))


@bp.post("/api/network/connect")
def net_connect():
    u = M.current_user()
    if not u or u["role"] != "admin":
        return M.err("Доступ только для администратора", 403)
    d = request.json or {}
    con = db.connect()
    url = (d.get("url") or "").strip().rstrip("/")
    if url and not url.startswith("http"):
        url = "https://" + url
    set_settings(con, hq_url=url, hq_token=(d.get("token") or "").strip(), hq_since="", hq_stock_push="", hq_error="")
    con.commit()
    con.close()
    if not url:
        M.extras.audit("Касса отключена от сети", "", 1)
        return jsonify(ok=True, disconnected=True)
    try:
        r = sync_once()
    except Exception as e:
        con = db.connect()
        set_settings(con, hq_error=str(e)[:300])
        con.commit()
        con.close()
        return M.err(str(e))
    M.extras.audit("Касса подключена к Центру", url, 1)
    return jsonify(ok=True, result=r, store=db.get_settings().get("hq_store_name"))


@bp.post("/api/network/sync")
def net_sync():
    if not M.current_user():
        return M.err("Требуется вход", 401)
    try:
        return jsonify(result=sync_once())
    except Exception as e:
        con = db.connect()
        set_settings(con, hq_error=str(e)[:300])
        con.commit()
        con.close()
        return M.err(str(e))


# ================================================================ DOCS (поступления / отгрузки)
DOC_TYPES = {"in_supply": "Поставка", "in_transfer": "Перемещение к нам", "local_supply": "Накладная поставщика",
             "out_transfer": "Отгрузка в магазин"}


def load_doc(did, con=None):
    own = con is None
    con = con or db.connect()
    d = con.execute("SELECT d.*, u.name AS user FROM docs d LEFT JOIN users u ON u.id=d.user_id WHERE d.id=?", (did,)).fetchone()
    if not d:
        if own:
            con.close()
        return None
    d = dict(d)
    d["type_name"] = DOC_TYPES.get(d["type"], d["type"])
    d["lines"] = [dict(r) for r in con.execute("""SELECT l.*, p.stock, p.unit_name FROM doc_lines l
                  LEFT JOIN products p ON p.id=l.product_id WHERE doc_id=? ORDER BY l.id""", (did,))]
    d["exp"] = sum(l["qty_expected"] for l in d["lines"])
    d["fact"] = sum(l["qty_fact"] for l in d["lines"])
    if own:
        con.close()
    return d


@bp.get("/api/docs")
def docs_list():
    if not M.current_user():
        return M.err("Требуется вход", 401)
    rows = M.q("""SELECT d.*, u.name AS user, (SELECT COUNT(*) FROM doc_lines WHERE doc_id=d.id) lines,
                  (SELECT COALESCE(SUM(qty_expected),0) FROM doc_lines WHERE doc_id=d.id) exp,
                  (SELECT COALESCE(SUM(qty_fact),0) FROM doc_lines WHERE doc_id=d.id) fact,
                  (SELECT COALESCE(SUM(qty_expected*cost),0) FROM doc_lines WHERE doc_id=d.id) sum
                  FROM docs d LEFT JOIN users u ON u.id=d.user_id ORDER BY
                  CASE WHEN d.status IN ('expected','receiving','to_ship') THEN 0 ELSE 1 END, d.id DESC LIMIT 200""")
    for r in rows:
        r["type_name"] = DOC_TYPES.get(r["type"], r["type"])
    return jsonify(rows)


@bp.get("/api/docs/<int:did>")
def doc_get(did):
    if not M.current_user():
        return M.err("Требуется вход", 401)
    d = load_doc(did)
    return jsonify(d) if d else M.err("Не найдено", 404)


def _editable(d):
    return d and d["status"] in ("expected", "receiving", "to_ship")


@bp.post("/api/docs/<int:did>/scan")
def doc_scan(did):
    """Скан штрихкода в накладной: +1 (или +qty) к факту. Работает и для весовых штрихкодов."""
    if not M.current_user():
        return M.err("Требуется вход", 401)
    d = request.json or {}
    code = str(d.get("code") or "").strip()
    qty = M.num(d.get("qty"), 1)
    con = db.connect()
    doc = con.execute("SELECT * FROM docs WHERE id=?", (did,)).fetchone()
    if not _editable(doc):
        con.close()
        return M.err("Накладная уже закрыта")
    line = None
    if d.get("line_id"):
        line = con.execute("SELECT * FROM doc_lines WHERE id=? AND doc_id=?", (d["line_id"], did)).fetchone()
    p = None
    if not line:
        p = con.execute("SELECT * FROM products WHERE active=1 AND (barcode=? OR sku=?)", (code, code)).fetchone()
        if not p and len(code) == 13 and code.isdigit():
            pref = [x.strip() for x in (db.get_settings(con).get("weight_prefixes") or "").split(",") if x.strip()]
            if code[:2] in pref:
                plu = code[2:7]
                p = con.execute("SELECT * FROM products WHERE active=1 AND (barcode IN (?,?) OR sku IN (?,?))",
                                (plu, plu.lstrip("0"), plu, plu.lstrip("0"))).fetchone()
                if p and not d.get("qty"):
                    qty = round(int(code[7:12]) / 1000, 3)
        if not p:
            # строка накладной могла прийти с штрихкодом, которого ещё нет в локальном каталоге
            line = con.execute("SELECT * FROM doc_lines WHERE doc_id=? AND barcode=?", (did, code)).fetchone()
            if not line:
                con.close()
                return jsonify(error="Штрихкод не найден: " + code, unknown=True), 404
        else:
            line = con.execute("SELECT * FROM doc_lines WHERE doc_id=? AND (product_id=? OR product_uid=?)",
                               (did, p["id"], p["uid"])).fetchone()
    extra = False
    if not line:
        extra = True
        lid = con.execute("""INSERT INTO doc_lines(doc_id,product_id,product_uid,name,barcode,qty_expected,qty_fact,cost)
                             VALUES(?,?,?,?,?,0,0,?)""", (did, p["id"], p["uid"], p["name"], p["barcode"], p["cost_price"])).lastrowid
        line = con.execute("SELECT * FROM doc_lines WHERE id=?", (lid,)).fetchone()
    new_fact = round(max(0, line["qty_fact"] + qty), 3) if d.get("set") is None else max(0, M.num(d["set"]))
    con.execute("UPDATE doc_lines SET qty_fact=?, scanned_at=? WHERE id=?", (new_fact, db.now(), line["id"]))
    if doc["status"] == "expected":
        con.execute("UPDATE docs SET status='receiving', user_id=? WHERE id=?", (session["uid"], did))
    if doc["type"] == "out_transfer" and doc["status"] == "to_ship" and not doc["picking"]:
        con.execute("UPDATE docs SET picking=1, synced=0 WHERE id=?", (did,))      # Центр увидит «Сборка»
    con.commit()
    res = load_doc(did, con)
    con.close()
    ln = next(l for l in res["lines"] if l["id"] == line["id"])
    state = "extra" if ln["qty_expected"] == 0 else ("over" if ln["qty_fact"] > ln["qty_expected"] else
                                                     "done" if ln["qty_fact"] == ln["qty_expected"] else "partial")
    return jsonify(doc=res, line=ln, state=state, extra=extra)


@bp.post("/api/docs/<int:did>/fill")
def doc_fill(did):
    u = M.current_user()
    if not u or u["role"] != "admin":
        return M.err("Только администратор может принять без сканирования", 403)
    con = db.connect()
    con.execute("UPDATE doc_lines SET qty_fact=qty_expected WHERE doc_id=?", (did,))
    con.execute("UPDATE docs SET status=CASE WHEN status='expected' THEN 'receiving' ELSE status END WHERE id=?", (did,))
    con.commit()
    con.close()
    M.extras.audit("Накладная заполнена без сканирования", f"документ #{did}", 1)
    return jsonify(load_doc(did))


@bp.post("/api/docs/<int:did>/reset")
def doc_reset(did):
    if not M.current_user():
        return M.err("Требуется вход", 401)
    con = db.connect()
    con.execute("UPDATE doc_lines SET qty_fact=0 WHERE doc_id=?", (did,))
    con.execute("DELETE FROM doc_lines WHERE doc_id=? AND qty_expected=0", (did,))
    con.commit()
    con.close()
    return jsonify(load_doc(did))


@bp.post("/api/docs/<int:did>/complete")
def doc_complete(did):
    """Проводит приёмку (остаток +факт) или отгрузку (остаток −факт)."""
    if not M.current_user():
        return M.err("Требуется вход", 401)
    d = request.json or {}
    con = db.connect()
    doc = load_doc(did, con)
    if not _editable(doc):
        con.close()
        return M.err("Накладная уже проведена")
    t = db.now()
    out = doc["type"] == "out_transfer"
    if out and not doc["partner_uid"]:
        con.close()
        return M.err("Не выбран магазин-получатель")
    if not any(l["qty_fact"] for l in doc["lines"]):
        con.close()
        return M.err("Ничего не отсканировано")
    for l in doc["lines"]:
        if not l["product_id"]:
            p = con.execute("SELECT id FROM products WHERE uid=?", (l["product_uid"],)).fetchone()
            if not p:
                continue
            l["product_id"] = p["id"]
            con.execute("UPDATE doc_lines SET product_id=? WHERE id=?", (p["id"], l["id"]))
        q_ = l["qty_fact"]
        if not q_:
            continue
        if out:
            con.execute("UPDATE products SET stock=stock-? WHERE id=?", (q_, l["product_id"]))
            con.execute("INSERT INTO stock_moves(product_id,created_at,type,qty,ref_id,user_id,note) VALUES(?,?,?,?,?,?,?)",
                        (l["product_id"], t, "transfer_out", -q_, did, session["uid"], f"{doc['number'] or ''} → {doc['partner']}"))
        else:
            p = con.execute("SELECT stock, cost_price FROM products WHERE id=?", (l["product_id"],)).fetchone()
            cost = l["cost"] or 0
            if cost and p:
                old_q = max(p["stock"], 0)
                new_cost = round((old_q * (p["cost_price"] or cost) + q_ * cost) / (old_q + q_), 2) if old_q else cost
                con.execute("UPDATE products SET cost_price=? WHERE id=? AND hq=0", (new_cost, l["product_id"]))
            if (l.get("price") or 0) > 0:
                con.execute("UPDATE products SET price=? WHERE id=? AND hq=0", (l["price"], l["product_id"]))
            con.execute("UPDATE products SET stock=stock+? WHERE id=?", (q_, l["product_id"]))
            con.execute("INSERT INTO stock_moves(product_id,created_at,type,qty,ref_id,user_id,note) VALUES(?,?,?,?,?,?,?)",
                        (l["product_id"], t, "receive", q_, did, session["uid"], f"{doc['number'] or ''} {doc['partner'] or ''}"))
            M.grocery.add_batch(con, l["product_id"], q_, l.get("expiry"), f"{doc['number'] or ''} {doc['partner'] or ''}")
    con.execute("UPDATE docs SET status=?, done_at=?, user_id=?, note=?, synced=0 WHERE id=?",
                ("shipped" if out else "received", t, session["uid"],
                 ((doc["note"] or "") + (" | " + d["comment"] if d.get("comment") else "")).strip(" |"), did))
    con.commit()
    con.close()
    res = load_doc(did)
    diff = res["fact"] - res["exp"]
    short = sum(max(0, l["qty_expected"] - l["qty_fact"]) for l in res["lines"])
    extra = sum(max(0, l["qty_fact"] - l["qty_expected"]) for l in res["lines"])
    if not out and (short or extra):
        M.extras.audit("Расхождение при приёмке", f"{res['number'] or ''} {res['partner'] or ''}: недостача {short:g}, излишек {extra:g}",
                       2 if short else 1)
    else:
        M.extras.audit("Отгрузка проведена" if out else "Приёмка проведена", f"{res['number'] or ''} {res['partner'] or ''}: {res['fact']:g} шт")
    kick()
    return jsonify(doc=res, short=short, extra=extra, diff=diff)


@bp.post("/api/docs/out")
def doc_out_new():
    """Новая отгрузка в другой магазин сети — дальше наполняется сканированием."""
    if not M.current_user():
        return M.err("Требуется вход", 401)
    d = request.json or {}
    stores = json.loads(db.get_settings().get("hq_stores") or "[]")
    st = next((x for x in stores if x["uid"] == d.get("to_store_uid")), None)
    if not st:
        return M.err("Выберите магазин-получатель")
    con = db.connect()
    n = con.execute("SELECT COUNT(*) n FROM docs WHERE type='out_transfer'").fetchone()["n"] + 1
    did = con.execute("""INSERT INTO docs(uid,number,type,status,partner,partner_uid,note,created_at,user_id,synced)
                         VALUES(?,?,?,?,?,?,?,?,?,1)""",
                      (uuid.uuid4().hex, f"ОТГ-{n:04d}", "out_transfer", "to_ship", st["name"], st["uid"],
                       d.get("note") or "", db.now(), session["uid"])).lastrowid
    con.commit()
    con.close()
    return jsonify(load_doc(did))


@bp.post("/api/docs/import")
def doc_import():
    """Excel/CSV накладной поставщика -> ожидаемое поступление для сверки сканером."""
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
    n = con.execute("SELECT COUNT(*) n FROM docs WHERE type='local_supply'").fetchone()["n"] + 1
    did = con.execute("""INSERT INTO docs(uid,number,type,status,partner,note,created_at,user_id,synced)
                         VALUES(?,?,?,?,?,?,?,?,1)""",
                      (uuid.uuid4().hex, f"НП-{n:04d}", "local_supply", "expected", request.form.get("supplier") or "Поставщик",
                       f.filename, db.now(), session["uid"])).lastrowid
    added, missing = 0, []
    for r in rows:
        rec = {}
        for field, aliases in M.IMPORT_MAP.items():
            for a in aliases:
                if a in r and r[a] not in (None, ""):
                    rec[field] = r[a]
                    break
        bc = str(rec.get("barcode") or "").strip().split(".")[0]
        qty = M.num(rec.get("stock"), 0)          # колонки «Количество»/«Кол-во» попадают в stock
        if not bc and not rec.get("name"):
            continue
        p = con.execute("SELECT * FROM products WHERE active=1 AND barcode=?", (bc,)).fetchone() if bc else None
        if not p and rec.get("name"):
            p = con.execute("SELECT * FROM products WHERE active=1 AND name=?", (str(rec["name"]).strip(),)).fetchone()
        cost = M.num(rec.get("cost_price"), M.num(rec.get("price")) if not rec.get("cost_price") else 0)
        if not p:
            missing.append({"name": rec.get("name") or "", "barcode": bc, "qty": qty, "cost": cost})
            continue
        con.execute("""INSERT INTO doc_lines(doc_id,product_id,product_uid,name,barcode,qty_expected,qty_fact,cost)
                       VALUES(?,?,?,?,?,?,0,?)""", (did, p["id"], p["uid"], p["name"], p["barcode"], qty or 1, cost or p["cost_price"]))
        added += 1
    if not added:
        con.execute("DELETE FROM docs WHERE id=?", (did,))
        con.commit()
        con.close()
        return M.err("В файле не найдено ни одного известного товара" + (f" (неизвестных: {len(missing)})" if missing else ""))
    con.commit()
    con.close()
    return jsonify(doc=load_doc(did), missing=missing)


@bp.delete("/api/docs/<int:did>")
def doc_delete(did):
    if not M.current_user():
        return M.err("Требуется вход", 401)
    con = db.connect()
    doc = con.execute("SELECT * FROM docs WHERE id=?", (did,)).fetchone()
    if not doc or doc["type"] not in ("out_transfer", "local_supply") or doc["status"] not in ("expected", "receiving", "to_ship"):
        con.close()
        return M.err("Удалить можно только свою непроведённую накладную")
    con.execute("DELETE FROM doc_lines WHERE doc_id=?", (did,))
    con.execute("DELETE FROM docs WHERE id=?", (did,))
    con.commit()
    con.close()
    return jsonify(ok=True)


def register(app_module):
    global M
    M = app_module
    M.app.register_blueprint(bp)
