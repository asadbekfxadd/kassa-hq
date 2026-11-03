"""
Перемещения между магазинами (этап 4).

Статусы перемещения в Центре:
  draft      Черновик        — создан, можно править строки
  pending    На согласовании — ждёт управляющего/директора
  to_ship    Одобрено        — касса магазина-отправителя получила задание
  preparing  Сборка          — отправитель начал сканировать товар
  sent       Отгружено       — касса отправителя провела отгрузку (остаток отправителя уменьшен)
  received   Получено        — касса получателя провела приёмку (остаток получателя увеличен)
  completed  Завершено       — закрыто (сразу, если расхождений нет; иначе — после проверки)
  cancelled  Отменено

Остатки меняют только кассы, и каждый документ проводится ими один раз (uid). Центр принимает от касс
только переходы «вперёд», поэтому повторная отправка данных не откатывает статус и не дублирует остаток.
"""
import math
import uuid
from datetime import timedelta

from flask import Blueprint, jsonify, request

bp = Blueprint("transfers", __name__)
H = None
F = "%Y-%m-%d %H:%M:%S"
FLOW = ["draft", "pending", "to_ship", "preparing", "sent", "received", "completed"]
TITLES = {"draft": "Черновик", "pending": "На согласовании", "to_ship": "Одобрено", "preparing": "Сборка", "sent": "Отгружено",
          "received": "Получено", "completed": "Завершено", "cancelled": "Отменено"}
EDITABLE = ("draft", "pending")
CANCELABLE = ("draft", "pending", "to_ship", "preparing")

SCHEMA = """
CREATE TABLE IF NOT EXISTS transfer_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, doc_uid TEXT NOT NULL, created_at TEXT, status TEXT, user_name TEXT, comment TEXT);
CREATE INDEX IF NOT EXISTS ix_te_doc ON transfer_events(doc_uid);
"""


def event(c, uid, status, comment="", who=None):
    who = who or (H.rbac.user() or {}).get("name") or "Центр"
    c.execute("INSERT INTO transfer_events(doc_uid,created_at,status,user_name,comment) VALUES(?,?,?,?,?)",
              (uid, H.now(), status, who, comment or ""))


def can_approve(doc):
    return H.rbac.can("docs.approve") and H.rbac.store_allowed(doc["from_store_id"])


def has_discrepancy(c, uid):
    return bool(c.execute("""SELECT 1 FROM doc_lines WHERE doc_uid=? AND ABS(COALESCE(qty_fact,0)-qty)>0.0001""", (uid,)).fetchone())


# ---------------------------------------------------------------- действия над документом
@bp.post("/api/docs/<uid>/action")
def doc_action(uid):
    d = request.json or {}
    act, comment = d.get("action"), (d.get("comment") or "").strip()
    c = H.con()
    doc = c.execute("SELECT * FROM docs WHERE uid=?", (uid,)).fetchone()
    if not doc:
        c.close()
        return H.err("Документ не найден", 404)
    doc = dict(doc)
    if not (H.rbac.store_allowed(doc["to_store_id"]) or H.rbac.store_allowed(doc["from_store_id"])):
        c.close()
        return H.err("Нет доступа к документу", 403)
    st, t = doc["status"], H.now()

    def go(new, msg, **cols):
        sets = ", ".join(f"{k}=?" for k in cols)
        c.execute(f"UPDATE docs SET status=?, updated_at=?{', ' + sets if sets else ''} WHERE uid=?", (new, t, *cols.values(), uid))
        event(c, uid, new, comment)
        H.rbac.audit(msg, f"{doc['number']}" + (f": {comment}" if comment else ""), doc["to_store_id"], c=c)

    if doc["type"] != "transfer" and act not in ("cancel", "complete"):
        c.close()
        return H.err("Действие доступно только для перемещений")
    if act == "submit":
        if st != "draft":
            c.close()
            return H.err("Отправить на согласование можно только черновик")
        go("pending", "Перемещение на согласовании")
    elif act == "approve":
        if st not in ("draft", "pending"):
            c.close()
            return H.err("Документ уже одобрен или закрыт")
        if not can_approve(doc):
            c.close()
            return H.err("Одобрять может управляющий магазина-отправителя, региональный менеджер, директор или владелец", 403)
        if not c.execute("SELECT 1 FROM doc_lines WHERE doc_uid=? AND qty>0", (uid,)).fetchone():
            c.close()
            return H.err("В документе нет товаров")
        go("to_ship", "Перемещение одобрено", approved_at=t, approved_by=(H.rbac.user() or {}).get("name", ""), sent_at=None)
    elif act == "reject":
        if st != "pending":
            c.close()
            return H.err("Вернуть можно только документ на согласовании")
        if not can_approve(doc):
            c.close()
            return H.err("Недостаточно прав", 403)
        if not comment:
            c.close()
            return H.err("Напишите причину — её увидит автор")
        go("draft", "Перемещение возвращено на доработку")
    elif act == "cancel":
        if st not in CANCELABLE and not (doc["type"] == "supply" and st == "sent"):
            c.close()
            return H.err("Товар уже отгружен — отменить нельзя. Оформите обратное перемещение.")
        if st in ("to_ship", "preparing") and not can_approve(doc):
            c.close()
            return H.err("Отменить одобренное перемещение может только тот, кто вправе одобрять", 403)
        go("cancelled", "Документ отменён")
    elif act == "complete":
        if st != "received":
            c.close()
            return H.err("Завершить можно только полученный документ")
        if not can_approve(doc):
            c.close()
            return H.err("Недостаточно прав", 403)
        if has_discrepancy(c, uid) and not comment:
            c.close()
            return H.err("Есть расхождения — опишите, как они решены")
        go("completed", "Перемещение завершено", completed_at=t)
    else:
        c.close()
        return H.err("Неизвестное действие")
    c.commit()
    c.close()
    return jsonify(H.load_doc(uid))


@bp.post("/api/docs/<uid>/lines")
def doc_lines_save(uid):
    """Правка строк черновика / документа на согласовании."""
    d = request.json or {}
    c = H.con()
    doc = c.execute("SELECT * FROM docs WHERE uid=?", (uid,)).fetchone()
    if not doc or dict(doc)["status"] not in EDITABLE:
        c.close()
        return H.err("Изменять можно только черновик или документ на согласовании")
    doc = dict(doc)
    if not H.rbac.store_allowed(doc["from_store_id"]) and not H.rbac.store_allowed(doc["to_store_id"]):
        c.close()
        return H.err("Нет доступа", 403)
    lines = [l for l in d.get("lines") or [] if H.num(l.get("qty")) > 0 and l.get("product_uid")]
    if not lines:
        c.close()
        return H.err("Добавьте товары")
    c.execute("DELETE FROM doc_lines WHERE doc_uid=?", (uid,))
    for l in lines:
        cost = H.num(l.get("cost"))
        if not cost:       # себестоимость не передали — берём из каталога
            cost = (dict(c.execute("SELECT cost_price FROM products WHERE uid=?", (l["product_uid"],)).fetchone() or {}) or {}).get("cost_price") or 0
        c.execute("INSERT INTO doc_lines(doc_uid,product_uid,qty,cost,price) VALUES(?,?,?,?,?)",
                  (uid, l["product_uid"], H.num(l["qty"]), cost, H.num(l.get("price"))))
    if d.get("note") is not None:
        c.execute("UPDATE docs SET note=? WHERE uid=?", (d["note"], uid))
    c.execute("UPDATE docs SET updated_at=? WHERE uid=?", (H.now(), uid))
    event(c, uid, doc["status"], f"Изменены строки: {len(lines)}")
    c.commit()
    c.close()
    return jsonify(H.load_doc(uid))


@bp.get("/api/docs/<uid>/events")
def doc_events(uid):
    return jsonify(H.q("SELECT * FROM transfer_events WHERE doc_uid=? ORDER BY id", (uid,)))


# ---------------------------------------------------------------- авто-рекомендации
def in_transit(c, ids):
    """Товар, который уже едет или будет отправлен: {(store, product): +приход/−расход}."""
    out = {}
    for r in c.execute(f"""SELECT d.from_store_id, d.to_store_id, d.status, l.product_uid, l.qty FROM docs d JOIN doc_lines l ON l.doc_uid=d.uid
                           WHERE d.type='transfer' AND d.status IN ('draft','pending','to_ship','preparing','sent')""").fetchall():
        r = dict(r)
        if r["status"] != "sent" and r["from_store_id"]:        # ещё не отгружено — у отправителя «зарезервировано»
            out[(r["from_store_id"], r["product_uid"])] = out.get((r["from_store_id"], r["product_uid"]), 0) - r["qty"]
        out[(r["to_store_id"], r["product_uid"])] = out.get((r["to_store_id"], r["product_uid"]), 0) + r["qty"]
    return out


_REC = {}


def recommend(window=30, target=14, keep=7, min_qty=1, category="", only_from=None, only_to=None):
    """С кэшем на минуту: пересчёт, если изменились документы (перемещения в пути) или прошла минута."""
    import time as _t
    ver = H.q("SELECT COUNT(*) n, MAX(updated_at) m FROM docs", one=True)
    key = (window, target, keep, min_qty, category, only_from, only_to, tuple(H.rbac.store_ids() or ()) if H.rbac.store_ids() is not None else None,
           ver["n"], ver["m"])
    hit = _REC.get(key)
    H.salescache.sold(), H.salescache.stock()          # прогреть до сверки версии
    if hit and _t.time() - hit[0] < 60 and hit[1] == H.salescache.VER[0]:
        return hit[2]
    res = _recommend(window, target, keep, min_qty, category, only_from, only_to)
    if len(_REC) > 50:
        _REC.clear()
    _REC[key] = (_t.time(), H.salescache.VER[0], res)
    return res


def _recommend(window=30, target=14, keep=7, min_qty=1, category="", only_from=None, only_to=None):
    now = H.tnow()
    fi, fia = H.rbac.sf("id")
    stores = {s["id"]: s["name"] for s in H.q(f"SELECT id, name FROM stores WHERE active=1{fi}", fia)}
    if len(stores) < 2:
        return [], stores
    ids = tuple(stores)
    ph = ",".join("?" * len(ids))
    since = {k: (now - timedelta(days=k)).strftime(F) for k in (7, 14, 30, 60)}
    sold = {(sid, pu): {"d7": max(0, v[0]), "d14": max(0, v[1]), "d30": max(0, v[2]), "d60": max(0, v[3])}
            for (sid, pu), v in H.salescache.sold().items() if sid in stores}
    stock = {k: v for k, v in H.salescache.stock().items() if k[0] in stores}
    c = H.con()
    transit = in_transit(c, ids)
    c.close()
    prods = {p["uid"]: p for p in H.q("SELECT uid, name, barcode, category, unit_name, min_stock, cost_price, shelf_days FROM products WHERE active=1")}
    wkey = {7: "d7", 14: "d14", 30: "d30", 60: "d60"}[window]
    by_prod = {}
    for (sid, pu) in set(stock) | set(sold):
        if pu in prods and sid in stores:
            by_prod.setdefault(pu, []).append(sid)
    out = []
    for pu, sids in by_prod.items():
        p = prods[pu]
        if category and (p["category"] or "") != category:
            continue
        info = {}
        for sid in sids:
            q = stock.get((sid, pu), 0) + transit.get((sid, pu), 0)
            s = sold.get((sid, pu), {"d7": 0, "d14": 0, "d30": 0, "d60": 0})
            per_day = s[wkey] / window
            info[sid] = {"stock": stock.get((sid, pu), 0), "eff": q, "per_day": per_day, **s}
        recv, donors = [], []
        for sid, x in info.items():
            if x["per_day"] > 0:
                need = math.ceil(x["per_day"] * target) - x["eff"]
                if need >= min_qty and (only_to is None or sid == only_to):
                    recv.append([sid, need])
            keep_q = max(math.ceil(x["per_day"] * keep), p["min_stock"] or 0, 1 if x["per_day"] > 0 else 0)
            surplus = math.floor(x["eff"] - keep_q)
            if surplus >= min_qty and (only_from is None or sid == only_from):
                donors.append([sid, surplus])
        if not recv or not donors:
            continue
        recv.sort(key=lambda r: (-info[r[0]]["per_day"], -r[1]))
        donors.sort(key=lambda d: (info[d[0]]["per_day"], -d[1]))
        for r in recv:
            for dn in donors:
                if dn[0] == r[0] or dn[1] < min_qty or r[1] < min_qty:
                    continue
                qty = min(r[1], dn[1])
                a, b = info[dn[0]], info[r[0]]
                cover_from = round(a["eff"] / a["per_day"]) if a["per_day"] else None
                cover_to = round(b["eff"] / b["per_day"], 1) if b["per_day"] else None
                out.append({"product_uid": pu, "name": p["name"], "barcode": p["barcode"], "category": p["category"], "unit": p["unit_name"],
                            "from_id": dn[0], "from": stores[dn[0]], "to_id": r[0], "to": stores[r[0]], "qty": qty,
                            "from_stock": a["stock"], "from_sold": {k: a[k] for k in ("d7", "d14", "d30", "d60")}, "from_cover": cover_from,
                            "to_stock": b["stock"], "to_sold": {k: b[k] for k in ("d7", "d14", "d30", "d60")}, "to_cover": cover_to,
                            "value": round(qty * (p["cost_price"] or 0)),
                            "reason": (f"«{stores[r[0]]}»: продано {b[wkey]:g} за {window} дн., остаток {b['stock']:g} — "
                                       f"{'нет в наличии' if b['stock'] <= 0 else f'хватит на {cover_to:g} дн.'}. "
                                       f"«{stores[dn[0]]}»: остаток {a['stock']:g}, продано {a[wkey]:g} — "
                                       f"{'без продаж' if not a['per_day'] else f'запас на {cover_from} дн.'}"),
                            "priority": "high" if b["stock"] <= 0 else ("mid" if (cover_to or 0) < 3 else "low")})
                dn[1] -= qty
                r[1] -= qty
    pr = {"high": 0, "mid": 1, "low": 2}
    out.sort(key=lambda x: (pr[x["priority"]], -x["to_sold"][wkey]))
    return out, stores


@bp.get("/api/transfers/recommend")
def recommend_api():
    a = request.args
    win = int(a.get("window", 30)) if a.get("window") in ("7", "14", "30", "60") else 30
    items, stores = recommend(win, max(1, int(H.num(a.get("target"), 14))), max(0, int(H.num(a.get("keep"), 7))),
                              max(1, H.num(a.get("min"), 1)), a.get("category", ""),
                              int(a["from"]) if a.get("from") else None, int(a["to"]) if a.get("to") else None)
    fin = H.rbac.can("finance")
    if not fin:
        for i in items:
            i["value"] = None
    return jsonify(items=items[:1000], total=len(items), stores=[{"id": k, "name": v} for k, v in stores.items()],
                   categories=sorted({i["category"] for i in items if i["category"]}), finance=fin)


@bp.post("/api/transfers/create")
def create_from_recommend():
    """Создаёт перемещения (по одному на пару «откуда → куда») из выбранных рекомендаций."""
    d = request.json or {}
    mode = d.get("mode") if d.get("mode") in ("draft", "submit", "approve") else "draft"
    groups = {}
    for it in d.get("items") or []:
        if H.num(it.get("qty")) > 0:
            groups.setdefault((int(it["from_id"]), int(it["to_id"])), []).append(it)
    if not groups:
        return H.err("Выберите строки")
    c = H.con()
    made = []
    for (fr, to), lines in groups.items():
        if not (H.rbac.store_allowed(fr) or H.rbac.store_allowed(to)):
            continue
        uid = uuid.uuid4().hex
        t = H.now()
        approve = mode == "approve" and H.rbac.can("docs.approve") and H.rbac.store_allowed(fr)
        status = "to_ship" if approve else ("pending" if mode in ("submit", "approve") else "draft")
        c.execute("""INSERT INTO docs(uid,number,type,status,from_store_id,to_store_id,supplier,note,created_at,created_by,updated_at,approved_at,approved_by)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (uid, H.doc_number(c, "transfer"), "transfer", status, fr, to, "", d.get("note") or "Авто-рекомендация", t,
                   (H.rbac.user() or {}).get("name", "Центр"), t, t if approve else None, (H.rbac.user() or {}).get("name") if approve else None))
        for l in lines:
            cost = (c.execute("SELECT cost_price FROM products WHERE uid=?", (l["product_uid"],)).fetchone() or {"cost_price": 0})
            c.execute("INSERT INTO doc_lines(doc_uid,product_uid,qty,cost) VALUES(?,?,?,?)",
                      (uid, l["product_uid"], H.num(l["qty"]), dict(cost)["cost_price"] or 0))
        event(c, uid, "draft", "Создано из рекомендаций")
        if status != "draft":
            event(c, uid, status, "")
        H.rbac.audit("Перемещение из рекомендаций", f"{len(lines)} строк, статус {TITLES[status]}", to, c=c)
        made.append(uid)
    c.commit()
    c.close()
    return jsonify(created=len(made), uids=made)


# ---------------------------------------------------------------- переходы от касс (вызывается из /sync)
def from_till(c, store, dc, t):
    """Возвращает True, если документ обработан этим модулем."""
    uid = dc.get("uid")
    ex = c.execute("SELECT * FROM docs WHERE uid=?", (uid,)).fetchone()
    ex = dict(ex) if ex else None
    s = dc.get("status")
    if s == "preparing":
        if ex and ex["from_store_id"] == store["id"] and ex["status"] == "to_ship":
            c.execute("UPDATE docs SET status='preparing', prepared_at=?, updated_at=? WHERE uid=?", (t, t, uid))
            event(c, uid, "preparing", "Касса начала сборку", store["name"])
        return True
    if s == "shipped" and ex:
        if ex["from_store_id"] != store["id"] or ex["status"] not in ("to_ship", "preparing"):
            return True            # повтор или отменённый документ — ничего не меняем
        c.execute("UPDATE docs SET status='sent', sent_at=?, updated_at=? WHERE uid=?", (t, t, uid))
        fact = {l["product_uid"]: H.num(l.get("qty")) for l in dc.get("lines") or []}
        plan = {r["product_uid"]: r["qty"] for r in (dict(x) for x in c.execute("SELECT product_uid, qty FROM doc_lines WHERE doc_uid=?", (uid,)))}
        c.execute("DELETE FROM doc_lines WHERE doc_uid=?", (uid,))
        for pu, qv in fact.items():
            c.execute("INSERT INTO doc_lines(doc_uid,product_uid,qty,cost) VALUES(?,?,?,?)", (uid, pu, qv,
                      next((H.num(l.get("cost")) for l in dc["lines"] if l["product_uid"] == pu), 0)))
        diff = sum(abs(fact.get(k, 0) - plan.get(k, 0)) for k in set(fact) | set(plan))
        event(c, uid, "sent", f"Отгружено {sum(fact.values()):g} шт" + (f", отличие от задания {diff:g}" if diff else ""), store["name"])
        return True
    if s == "received" and ex:
        if ex["to_store_id"] != store["id"] or ex["status"] != "sent":
            return True
        return False               # строки факта пишет основной обработчик /sync, статус дальше — after_received()
    return False


def after_received(c, uid, store):
    ex = dict(c.execute("SELECT type, status FROM docs WHERE uid=?", (uid,)).fetchone() or {})
    if ex.get("type") != "transfer" or ex.get("status") != "received":
        return
    disc = has_discrepancy(c, uid)
    event(c, uid, "received", "Принято с расхождениями" if disc else "Принято полностью", store["name"])
    if not disc:
        t = H.now()
        c.execute("UPDATE docs SET status='completed', completed_at=? WHERE uid=?", (t, uid))
        event(c, uid, "completed", "Закрыто автоматически: расхождений нет", "Центр")


def register(hq_module):
    global H
    H = hq_module
    c = H.con()
    c.executescript(SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY") if H.PG else SCHEMA)
    c.commit()
    for col in ("approved_at", "approved_by", "prepared_at", "completed_at"):
        try:
            c.execute(f"ALTER TABLE docs ADD COLUMN {col} TEXT")
            c.commit()
        except Exception:
            c.rollback()
    c.close()
    H.app.register_blueprint(bp)
