"""
Центр (этап 6): аналитика сети и финансы.

Аналитика — фильтры: период, магазин, категория, бренд.
  • продажи: итоги и сравнение, динамика, магазины, категории, бренды, оплаты, день недели × час;
  • товары: ABC (70/25/5), XYZ, топ/худшие 10/20/30/50, без продаж, нулевой остаток, зона риска, избыток;
  • сезонность: 24 месяца (этот год к прошлому), дни недели, часы.
Финансы — по каждому филиалу и по сети:
  • отчёт о прибыли: выручка, себестоимость, валовая прибыль, расходы по статьям, чистая прибыль, маржа;
  • денежный поток: поступления (нал, карта, QR, погашения долгов) и выплаты (возвраты, расходы, поставщикам, инкассация);
  • долги и касса: дебиторка (насия), долг поставщикам, наличные в кассах;
  • расходы: из касс магазинов (синхронизируются) и введённые в Центре (аренда, зарплаты и т.д.).
"""
import io
import math
from datetime import datetime, timedelta

from flask import Blueprint, Response, jsonify, request, render_template_string

bp = Blueprint("analytics", __name__)
H = None
F = "%Y-%m-%d %H:%M:%S"
SGN = "(CASE WHEN s.operation='sale' THEN 1 ELSE -1 END)"
WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONTHS = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
EXP_CATS = ["Аренда", "Зарплаты", "Логистика", "Маркетинг", "Коммунальные услуги", "Прочие расходы"]
_KEYS = [("Аренда", ("аренд",)), ("Зарплаты", ("зарплат", "зп", "аванс", "оклад", "премия")),
         ("Логистика", ("логист", "доставк", "такси", "бензин", "топлив", "транспорт")),
         ("Маркетинг", ("маркет", "реклам", "smm", "таргет", "листовк", "акци")),
         ("Коммунальные услуги", ("коммун", "свет", "электр", "газ", "вода", "интернет", "связь", "мусор"))]


def exp_cat(raw):
    r = (raw or "").lower()
    for name, keys in _KEYS:
        if any(k in r for k in keys):
            return name
    return "Прочие расходы"


# ---------------------------------------------------------------- фильтры
def flt():
    a = request.args
    now = H.tnow()
    f = a.get("from") or now.strftime("%Y-%m-01")
    t = a.get("to") or now.strftime("%Y-%m-%d")
    sid = a.get("store") or ""
    if sid and not H.rbac.store_allowed(sid):
        raise PermissionError("Нет доступа к магазину")
    return {"from": f, "to": t, "a0": f + " 00:00:00", "a1": t + " 23:59:59", "store": int(sid) if sid else None,
            "category": a.get("category") or "", "brand": a.get("brand") or ""}


def where_sales(fl, alias="s"):
    """WHERE-часть для sales (+ права по магазинам)."""
    w, args = [f"{alias}.created_at BETWEEN ? AND ?"], [fl["a0"], fl["a1"]]
    if fl["store"]:
        w.append(f"{alias}.store_id=?")
        args.append(fl["store"])
    f, fa = H.rbac.sf(f"{alias}.store_id")
    return " AND ".join(w) + f, args + list(fa)


def where_items(fl):
    w, args = where_sales(fl)
    if fl["category"]:
        w += " AND COALESCE(p.category,'')=?"
        args.append(fl["category"])
    if fl["brand"]:
        w += " AND COALESCE(p.brand,'')=?"
        args.append(fl["brand"])
    return w, args


ITEMS = "FROM sale_items si JOIN sales s ON s.uid=si.sale_uid LEFT JOIN products p ON p.uid=si.product_uid"


def item_totals(fl):
    w, a = where_items(fl)
    r = H.q(f"""SELECT COALESCE(SUM({SGN}*si.total),0) net, COALESCE(SUM({SGN}*si.qty),0) qty,
                COALESCE(SUM({SGN}*(si.total-si.qty*COALESCE(si.cost_price,0))),0) profit, COUNT(DISTINCT CASE WHEN s.operation='sale' THEN s.uid END) checks
                {ITEMS} WHERE {w}""", a, one=True)
    r["avg"] = round(r["net"] / r["checks"]) if r["checks"] else 0
    r["margin"] = round(r["profit"] / r["net"] * 100, 1) if r["net"] else 0
    return r


def _prev(fl):
    d0, d1 = datetime.strptime(fl["from"], "%Y-%m-%d"), datetime.strptime(fl["to"], "%Y-%m-%d")
    n = (d1 - d0).days + 1
    pf, pt = (d0 - timedelta(days=n)).strftime("%Y-%m-%d"), (d0 - timedelta(days=1)).strftime("%Y-%m-%d")
    return {**fl, "from": pf, "to": pt, "a0": pf + " 00:00:00", "a1": pt + " 23:59:59"}


def _fin(d):
    if not H.rbac.can("finance"):
        for k in ("profit", "margin", "cost", "value"):
            if k in d:
                d[k] = None
    return d


# ---------------------------------------------------------------- продажи
def sales_data(fl):
    cur, prev = item_totals(fl), item_totals(_prev(fl))
    chg = {k: (round((cur[k] - prev[k]) / abs(prev[k]) * 100, 1) if prev[k] else None) for k in ("net", "qty", "profit", "checks", "avg")}
    w, a = where_items(fl)
    by_day = H.q(f"""SELECT substr(s.created_at,1,10) d, COALESCE(SUM({SGN}*si.total),0) net, COUNT(DISTINCT CASE WHEN s.operation='sale' THEN s.uid END) checks
                    {ITEMS} WHERE {w} GROUP BY substr(s.created_at,1,10) ORDER BY d""", a)
    by_store = H.q(f"""SELECT s.store_id, MAX(st.name) AS name, COALESCE(SUM({SGN}*si.total),0) net,
                      COALESCE(SUM({SGN}*(si.total-si.qty*COALESCE(si.cost_price,0))),0) profit, COUNT(DISTINCT CASE WHEN s.operation='sale' THEN s.uid END) checks
                      {ITEMS} JOIN stores st ON st.id=s.store_id WHERE {w} GROUP BY s.store_id ORDER BY net DESC""", a)
    by_cat = H.q(f"""SELECT COALESCE(NULLIF(p.category,''),'Без категории') AS name, COALESCE(SUM({SGN}*si.total),0) net,
                    COALESCE(SUM({SGN}*(si.total-si.qty*COALESCE(si.cost_price,0))),0) profit, COALESCE(SUM({SGN}*si.qty),0) qty
                    {ITEMS} WHERE {w} GROUP BY COALESCE(NULLIF(p.category,''),'Без категории') ORDER BY net DESC""", a)
    by_brand = H.q(f"""SELECT COALESCE(NULLIF(p.brand,''),'Без бренда') AS name, COALESCE(SUM({SGN}*si.total),0) net,
                      COALESCE(SUM({SGN}*(si.total-si.qty*COALESCE(si.cost_price,0))),0) profit, COALESCE(SUM({SGN}*si.qty),0) qty
                      {ITEMS} WHERE {w} GROUP BY COALESCE(NULLIF(p.brand,''),'Без бренда') ORDER BY net DESC""", a)
    ws, sa = where_sales(fl)
    pay = H.q(f"""SELECT COALESCE(SUM({SGN}*s.cash),0) cash, COALESCE(SUM({SGN}*s.card),0) card, COALESCE(SUM({SGN}*COALESCE(s.qr,0)),0) qr,
                  COALESCE(SUM({SGN}*s.debt),0) debt, COALESCE(SUM(CASE WHEN s.operation='refund' THEN s.total ELSE 0 END),0) refunds,
                  COALESCE(SUM(CASE WHEN s.operation='sale' THEN s.discount ELSE 0 END),0) discount FROM sales s WHERE {ws}""", sa, one=True)
    heat = [[0] * 24 for _ in range(7)]
    for r in H.q(f"""SELECT substr(s.created_at,1,10) d, CAST(substr(s.created_at,12,2) AS INTEGER) h, COALESCE(SUM({SGN}*si.total),0) net
                     {ITEMS} WHERE {w} GROUP BY substr(s.created_at,1,10), CAST(substr(s.created_at,12,2) AS INTEGER)""", a):
        heat[datetime.strptime(r["d"], "%Y-%m-%d").weekday()][r["h"]] += r["net"]
    for lst in (by_store, by_cat, by_brand):
        for x in lst:
            _fin(x)
            if x.get("profit") is not None:
                x["margin"] = round(x["profit"] / x["net"] * 100, 1) if x["net"] else 0
    return {"cur": _fin(cur), "prev": _fin(prev), "chg": chg, "by_day": by_day, "by_store": by_store, "by_category": by_cat,
            "by_brand": by_brand, "pay": pay, "heat": heat, "prev_period": [_prev(fl)["from"], _prev(fl)["to"]]}


# ---------------------------------------------------------------- товары
def products_data(fl, top=20):
    w, a = where_items(fl)
    d0, d1 = datetime.strptime(fl["from"], "%Y-%m-%d"), datetime.strptime(fl["to"], "%Y-%m-%d")
    d1 = min(d1, H.tnow().replace(hour=0, minute=0, second=0, microsecond=0))   # будущие дни не размывают скорость продаж и XYZ
    days = max(1, (d1 - d0).days + 1)
    rows = H.q(f"""SELECT si.product_uid uid, MAX(si.name) AS name, MAX(p.category) AS category, MAX(p.brand) AS brand,
                   COALESCE(SUM({SGN}*si.qty),0) qty, COALESCE(SUM({SGN}*si.total),0) net,
                   COALESCE(SUM({SGN}*(si.total-si.qty*COALESCE(si.cost_price,0))),0) profit
                   {ITEMS} WHERE {w} GROUP BY si.product_uid""", a)
    # XYZ: продажи по неделям (если период ≥ 4 недель) или по дням
    bucket = 7 if days >= 28 else 1
    nb = max(1, math.ceil(days / bucket))
    series = {}
    for r in H.q(f"""SELECT si.product_uid uid, substr(s.created_at,1,10) d, COALESCE(SUM({SGN}*si.qty),0) q {ITEMS} WHERE {w}
                     GROUP BY si.product_uid, substr(s.created_at,1,10)""", a):
        i = min(nb - 1, (datetime.strptime(r["d"], "%Y-%m-%d") - d0).days // bucket)
        series.setdefault(r["uid"], [0.0] * nb)[i] += r["q"]
    total = sum(max(0, r["net"]) for r in rows) or 1
    rows.sort(key=lambda r: -r["net"])
    acc = 0
    for r in rows:      # класс по накопленной доле ДО товара: A — пока не набрали 70%, B — до 95%, остальные C
        before = acc / total * 100
        acc += max(0, r["net"])
        r["share"] = round(max(0, r["net"]) / total * 100, 2)
        r["abc"] = "A" if before < 70 else ("B" if before < 95 else "C")
        sr = series.get(r["uid"])
        if sr and nb > 1 and sum(sr) > 0:
            mean = sum(sr) / nb
            cv = math.sqrt(sum((x - mean) ** 2 for x in sr) / nb) / mean * 100
            r["cv"], r["xyz"] = round(cv, 1), ("X" if cv <= 10 else "Y" if cv <= 25 else "Z")
        else:
            r["cv"], r["xyz"] = None, "—"
        r["margin"] = round(r["profit"] / r["net"] * 100, 1) if r["net"] else None
        _fin(r)
    matrix = {}
    for r in rows:
        if r["xyz"] in "XYZ":
            m = matrix.setdefault(r["abc"] + r["xyz"], {"n": 0, "net": 0})
            m["n"] += 1
            m["net"] += r["net"]
    # остатки по выбранным магазинам: без продаж / нулевой остаток / зона риска / избыток
    fi, fia = H.rbac.sf("st.store_id")
    sw = " AND st.store_id=?" if fl["store"] else ""
    sa = (fl["store"],) if fl["store"] else ()
    stock = {}
    for r in H.q(f"SELECT st.product_uid, SUM(st.qty) q FROM stock st WHERE 1=1{fi}{sw} GROUP BY st.product_uid", fia + sa):
        stock[r["product_uid"]] = r["q"] or 0
    prods = {p["uid"]: p for p in H.q("SELECT uid, name, category, brand, cost_price, min_stock FROM products WHERE active=1")}
    sold = {r["uid"]: r for r in rows}
    fin = H.rbac.can("finance")
    zero_sales, zero_stock, risk, over = [], [], [], []
    for uid, q in stock.items():
        p = prods.get(uid)
        if not p or (fl["category"] and (p["category"] or "") != fl["category"]) or (fl["brand"] and (p["brand"] or "") != fl["brand"]):
            continue
        s_ = sold.get(uid)
        per_day = (s_["qty"] / days) if s_ else 0
        item = {"uid": uid, "name": p["name"], "category": p["category"], "brand": p["brand"], "stock": q, "sold": s_["qty"] if s_ else 0,
                "per_day": round(per_day, 2), "cover": round(q / per_day, 1) if per_day > 0 and q > 0 else None,
                "value": round(max(q, 0) * (p["cost_price"] or 0)) if fin else None}
        if q > 0 and not s_:
            zero_sales.append(item)
        elif q <= 0 and s_ and s_["qty"] > 0:
            zero_stock.append(item)
        elif per_day > 0 and q / per_day < 3:
            risk.append(item)
        elif per_day > 0 and q / per_day > 60:
            over.append(item)
    zero_sales.sort(key=lambda x: -(x["value"] or x["stock"]))
    zero_stock.sort(key=lambda x: -x["sold"])
    risk.sort(key=lambda x: x["cover"] or 0)
    over.sort(key=lambda x: -(x["cover"] or 0))
    best = [r for r in rows if r["net"] > 0][:top]
    worst = sorted([r for r in rows if r["qty"] > 0], key=lambda r: r["net"])[:top]
    return {"items": rows[:2000], "total": len(rows), "matrix": matrix, "bucket": "неделя" if bucket == 7 else "день", "days": days,
            "best": best, "worst": worst, "zero_sales": zero_sales[:300], "zero_stock": zero_stock[:300], "risk": risk[:300], "over": over[:300],
            "counts": {"zero_sales": len(zero_sales), "zero_stock": len(zero_stock), "risk": len(risk), "over": len(over)},
            "abc_n": {k: sum(1 for r in rows if r["abc"] == k) for k in "ABC"}}


# ---------------------------------------------------------------- сезонность
def season_data(fl):
    now = H.tnow()
    start = datetime(now.year - 1, 1, 1)
    fl2 = {**fl, "a0": start.strftime(F), "a1": now.strftime("%Y-%m-%d") + " 23:59:59"}
    w, a = where_items(fl2)
    months = {}
    for r in H.q(f"""SELECT substr(s.created_at,1,7) m, COALESCE(SUM({SGN}*si.total),0) net, COALESCE(SUM({SGN}*si.qty),0) qty
                     {ITEMS} WHERE {w} GROUP BY substr(s.created_at,1,7)""", a):
        months[r["m"]] = r
    out = []
    for m in range(1, 13):
        c, p = months.get(f"{now.year}-{m:02d}", {}), months.get(f"{now.year - 1}-{m:02d}", {})
        out.append({"m": m, "label": MONTHS[m - 1], "cur": c.get("net", 0) if m <= now.month else None, "prev": p.get("net", 0),
                    "chg": round((c.get("net", 0) - p.get("net", 0)) / p["net"] * 100, 1) if p.get("net") and m <= now.month else None})
    w2, a2 = where_items(fl)
    wd, hr = [0.0] * 7, [0.0] * 24
    for r in H.q(f"""SELECT substr(s.created_at,1,10) d, CAST(substr(s.created_at,12,2) AS INTEGER) h, COALESCE(SUM({SGN}*si.total),0) net
                     {ITEMS} WHERE {w2} GROUP BY substr(s.created_at,1,10), CAST(substr(s.created_at,12,2) AS INTEGER)""", a2):
        wd[datetime.strptime(r["d"], "%Y-%m-%d").weekday()] += r["net"]
        hr[r["h"]] += r["net"]
    cats = {}
    for r in H.q(f"""SELECT substr(s.created_at,6,2) m, COALESCE(NULLIF(p.category,''),'Без категории') AS cat, COALESCE(SUM({SGN}*si.total),0) net
                     {ITEMS} WHERE {w} GROUP BY substr(s.created_at,6,2), COALESCE(NULLIF(p.category,''),'Без категории')""", a):
        cats.setdefault(r["cat"], [0.0] * 12)[int(r["m"]) - 1] += r["net"]
    peak = {c: MONTHS[max(range(12), key=lambda i: v[i])] for c, v in cats.items() if sum(v) > 0}
    return {"months": out, "weekday": [{"label": WD[i], "net": wd[i]} for i in range(7)], "hours": [{"label": str(h), "net": hr[h]} for h in range(24)],
            "years": [now.year - 1, now.year], "category_peak": peak,
            "category_months": {c: v for c, v in sorted(cats.items(), key=lambda kv: -sum(kv[1]))[:8]}}


# ---------------------------------------------------------------- финансы
def money_where(fl, alias="m"):
    w, args = [f"{alias}.created_at BETWEEN ? AND ?"], [fl["a0"], fl["a1"]]
    if fl["store"]:
        w.append(f"{alias}.store_id=?")
        args.append(fl["store"])
    ids = H.rbac.store_ids()
    if ids is not None:      # общие расходы сети (без магазина) видят только роли «вся сеть»
        w.append(f"{alias}.store_id IN ({','.join('?' * len(ids)) or 'NULL'})")
        args += ids
    return " AND ".join(w), args


def expenses(fl):
    """Расходы: из касс (тип expense, кроме оплат поставщикам) и введённые в Центре."""
    w, a = money_where(fl)
    rows = H.q(f"""SELECT m.*, st.name AS store FROM money_ops m LEFT JOIN stores st ON st.id=m.store_id
                   WHERE {w} AND ((m.kind='cash_op' AND m.type='expense' AND COALESCE(m.category,'')<>'Поставщику') OR m.kind='hq_expense')
                   ORDER BY m.created_at DESC""", a)
    for r in rows:
        r["cat"] = r["category"] if r["kind"] == "hq_expense" and r["category"] in EXP_CATS else exp_cat(r["category"])
    return rows


def pnl(fl):
    stores = {s["id"]: s["name"] for s in H.q(f"SELECT id, name FROM stores WHERE 1=1{H.rbac.sf('id')[0]} ORDER BY id", H.rbac.sf("id")[1])}
    if fl["store"]:
        stores = {k: v for k, v in stores.items() if k == fl["store"]}
    w, a = where_items(fl)
    sales = {r["store_id"]: r for r in H.q(f"""SELECT s.store_id, COALESCE(SUM({SGN}*si.total),0) net,
                   COALESCE(SUM({SGN}*si.qty*COALESCE(si.cost_price,0)),0) cost {ITEMS} WHERE {w} GROUP BY s.store_id""", a)}
    exp = expenses(fl)
    cols = list(stores) + ([None] if any(e["store_id"] is None for e in exp) else [])
    out = []
    for sid in cols:
        s_ = sales.get(sid, {})
        rev, cost = s_.get("net", 0) or 0, s_.get("cost", 0) or 0
        by = {c: 0.0 for c in EXP_CATS}
        for e in exp:
            if e["store_id"] == sid:
                by[e["cat"]] += e["amount"]
        ex = sum(by.values())
        gross = rev - cost
        out.append({"store_id": sid, "store": stores.get(sid, "Общие расходы сети") if sid else "Общие расходы сети", "revenue": round(rev, 2),
                    "cost": round(cost, 2), "gross": round(gross, 2), "gross_margin": round(gross / rev * 100, 1) if rev else 0,
                    "expenses": {k: round(v, 2) for k, v in by.items()}, "expenses_total": round(ex, 2), "net": round(gross - ex, 2),
                    "net_margin": round((gross - ex) / rev * 100, 1) if rev else 0})
    tot = {"store": "Вся сеть" if not fl["store"] else stores.get(fl["store"]), "revenue": sum(x["revenue"] for x in out),
           "cost": sum(x["cost"] for x in out), "gross": sum(x["gross"] for x in out), "expenses_total": sum(x["expenses_total"] for x in out),
           "expenses": {c: sum(x["expenses"][c] for x in out) for c in EXP_CATS}}
    tot["net"] = tot["gross"] - tot["expenses_total"]
    tot["gross_margin"] = round(tot["gross"] / tot["revenue"] * 100, 1) if tot["revenue"] else 0
    tot["net_margin"] = round(tot["net"] / tot["revenue"] * 100, 1) if tot["revenue"] else 0
    return {"stores": out, "total": tot, "categories": EXP_CATS}


def cashflow(fl):
    ws, sa = where_sales(fl)
    days = {}

    def day(d):
        return days.setdefault(d, {"d": d, "in_cash": 0, "in_card": 0, "in_qr": 0, "in_debt": 0, "out_refund": 0, "out_exp": 0, "out_sup": 0, "out_cash": 0})
    for r in H.q(f"""SELECT substr(s.created_at,1,10) d, COALESCE(SUM(CASE WHEN s.operation='sale' THEN s.cash ELSE 0 END),0) cash,
                     COALESCE(SUM(CASE WHEN s.operation='sale' THEN s.card ELSE 0 END),0) card, COALESCE(SUM(CASE WHEN s.operation='sale' THEN COALESCE(s.qr,0) ELSE 0 END),0) qr,
                     COALESCE(SUM(CASE WHEN s.operation='refund' THEN s.cash+s.card+COALESCE(s.qr,0) ELSE 0 END),0) refund
                     FROM sales s WHERE {ws} GROUP BY substr(s.created_at,1,10)""", sa):
        x = day(r["d"])
        x["in_cash"] += r["cash"]
        x["in_card"] += r["card"]
        x["in_qr"] += r["qr"]
        x["out_refund"] += r["refund"]
    w, a = money_where(fl)
    ops = H.q(f"SELECT substr(m.created_at,1,10) d, m.store_id, m.created_at, m.kind, m.type, m.method, m.category, m.amount FROM money_ops m WHERE {w}", a)
    # Оплата поставщику наличными из кассы пишется на кассе дважды: в журнал поставщика и расходом «Поставщику» в кассе.
    # Считаем её один раз (по журналу поставщика); расход «Поставщику» без пары — ручная выдача кассиром, считаем.
    paired = {}
    for r in ops:
        if r["kind"] == "supplier_op" and r["type"] == "payment" and r["method"] == "cash":
            k = (r["store_id"], r["created_at"], round(r["amount"], 2))
            paired[k] = paired.get(k, 0) + 1
    for r in ops:
        x = day(r["d"])
        if r["kind"] == "cash_op":
            if r["type"] == "debt_in":
                x["in_debt"] += r["amount"]
            elif r["type"] == "expense" and (r["category"] or "") == "Поставщику":
                k = (r["store_id"], r["created_at"], round(r["amount"], 2))
                if paired.get(k):
                    paired[k] -= 1
                else:
                    x["out_sup"] += r["amount"]
            elif r["type"] == "expense":
                x["out_exp"] += r["amount"]
            elif r["type"] == "out":
                x["out_cash"] += r["amount"]
        elif r["kind"] == "supplier_op" and r["type"] == "payment":
            x["out_sup"] += r["amount"]
        elif r["kind"] == "hq_expense":
            x["out_exp"] += r["amount"]
        # supplier_op return — возврат товара уменьшает долг поставщику, деньги не движутся
    rows = sorted(days.values(), key=lambda x: x["d"])
    for x in rows:
        x["inflow"] = round(x["in_cash"] + x["in_card"] + x["in_qr"] + x["in_debt"], 2)
        x["outflow"] = round(x["out_refund"] + x["out_exp"] + x["out_sup"], 2)
        x["net"] = round(x["inflow"] - x["outflow"], 2)
    tot = {k: round(sum(x[k] for x in rows), 2) for k in ("in_cash", "in_card", "in_qr", "in_debt", "out_refund", "out_exp", "out_sup", "out_cash", "inflow", "outflow", "net")}
    return {"days": rows, "total": tot}


def balances():
    import json as _j
    fi, fia = H.rbac.sf("id")
    rows = H.q(f"SELECT id, name, info, last_seen FROM stores WHERE active=1{fi} ORDER BY id", fia)
    out = []
    for r in rows:
        i = _j.loads(r["info"] or "{}")
        out.append({"id": r["id"], "name": r["name"], "last_seen": r["last_seen"], "customers_debt": i.get("customers_debt"),
                    "debtors": i.get("debtors"), "suppliers_debt": i.get("suppliers_debt"), "cash_in_drawer": i.get("cash_in_drawer"),
                    "shift": i.get("shift"), "stock_value": i.get("stock_value")})
    tot = {k: sum((x[k] or 0) for x in out) for k in ("customers_debt", "suppliers_debt", "cash_in_drawer", "stock_value")}
    return {"stores": out, "total": tot}


# ---------------------------------------------------------------- API
def _wrap(fn):
    try:
        return jsonify(fn(flt()))
    except PermissionError as e:
        return H.err(str(e), 403)


@bp.get("/api/an/filters")
def an_filters():
    fi, fia = H.rbac.sf("id")
    return jsonify(stores=H.q(f"SELECT id, name FROM stores WHERE active=1{fi} ORDER BY id", fia),
                   categories=[r["c"] for r in H.q("SELECT DISTINCT category c FROM products WHERE COALESCE(category,'')<>'' ORDER BY c")],
                   brands=[r["b"] for r in H.q("SELECT DISTINCT brand b FROM products WHERE COALESCE(brand,'')<>'' ORDER BY b")],
                   expense_categories=EXP_CATS, finance=H.rbac.can("finance"), can_expense=H.rbac.can("expenses"))


@bp.get("/api/an/sales")
def an_sales():
    return _wrap(sales_data)


@bp.get("/api/an/products")
def an_products():
    top = int(request.args.get("top", 20)) if request.args.get("top") in ("10", "20", "30", "50") else 20
    return _wrap(lambda fl: products_data(fl, top))


@bp.get("/api/an/season")
def an_season():
    return _wrap(season_data)


@bp.get("/api/fin/pnl")
def fin_pnl():
    return _wrap(pnl)


@bp.get("/api/fin/cashflow")
def fin_cashflow():
    return _wrap(cashflow)


@bp.get("/api/fin/balances")
def fin_balances():
    return jsonify(balances())


@bp.get("/api/fin/expenses")
def fin_expenses():
    return _wrap(lambda fl: {"items": expenses(fl), "categories": EXP_CATS})


@bp.post("/api/fin/expenses")
def fin_expense_add():
    d = request.json or {}
    amount = H.num(d.get("amount"))
    cat = d.get("category")
    if amount <= 0:
        return H.err("Сумма должна быть больше нуля")
    if cat not in EXP_CATS:
        return H.err("Выберите статью расхода")
    sid = int(d["store_id"]) if d.get("store_id") else None
    if sid is None and H.rbac.store_ids() is not None:
        return H.err("Выберите магазин")
    if sid and not H.rbac.store_allowed(sid):
        return H.err("Нет доступа к магазину", 403)
    import uuid
    when = (d.get("date") or H.tnow().strftime("%Y-%m-%d")) + " 12:00:00"
    c = H.con()
    c.execute("""INSERT INTO money_ops(uid,store_id,created_at,kind,type,method,amount,category,note,user_name,source)
                 VALUES(?,?,?,?,?,?,?,?,?,?,'hq')""", (uuid.uuid4().hex, sid, when, "hq_expense", "expense", d.get("method") or "bank",
                                                      amount, cat, d.get("note") or "", (H.rbac.user() or {}).get("name", "")))
    H.rbac.audit("Расход внесён", f"{cat}: {amount:,.0f} {d.get('note') or ''}".replace(",", " "), sid, c=c)
    c.commit()
    c.close()
    return jsonify(ok=True)


@bp.delete("/api/fin/expenses/<uid>")
def fin_expense_del(uid):
    r = H.q("SELECT * FROM money_ops WHERE uid=? AND kind='hq_expense'", (uid,), one=True)
    if not r:
        return H.err("Удалять можно только расходы, внесённые в Центре", 404)
    if r["store_id"] and not H.rbac.store_allowed(r["store_id"]):
        return H.err("Нет доступа", 403)
    c = H.con()
    c.execute("DELETE FROM money_ops WHERE uid=?", (uid,))
    H.rbac.audit("Расход удалён", f"{r['category']}: {r['amount']:,.0f}".replace(",", " "), r["store_id"], c=c)
    c.commit()
    c.close()
    return jsonify(ok=True)


# ---------------------------------------------------------------- Excel и PDF (печать)
def _xlsx(sheets, name):
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, hdr, rows in sheets:
        ws = wb.create_sheet(title[:31])
        ws.append(hdr)
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="1D4ED8")
        for r in rows:
            ws.append(r)
        for i, h in enumerate(hdr, 1):
            ws.column_dimensions[ws.cell(1, i).column_letter].width = 34 if i == 1 else max(12, len(str(h)) + 3)
        ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return Response(buf.getvalue(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename={name}.xlsx"})


def build_sheets(kind, fl):
    fin = H.rbac.can("finance")
    if kind == "sales":
        d = sales_data(fl)
        return [("По дням", ["Дата", "Выручка", "Чеков"], [[x["d"], x["net"], x["checks"]] for x in d["by_day"]]),
                ("Магазины", ["Магазин", "Выручка", "Чеков"] + (["Прибыль"] if fin else []), [[x["name"], x["net"], x["checks"]] + ([x["profit"]] if fin else []) for x in d["by_store"]]),
                ("Категории", ["Категория", "Выручка", "Кол-во"] + (["Прибыль"] if fin else []), [[x["name"], x["net"], x["qty"]] + ([x["profit"]] if fin else []) for x in d["by_category"]]),
                ("Бренды", ["Бренд", "Выручка", "Кол-во"] + (["Прибыль"] if fin else []), [[x["name"], x["net"], x["qty"]] + ([x["profit"]] if fin else []) for x in d["by_brand"]])]
    if kind == "products":
        d = products_data(fl, 50)
        return [("ABC-XYZ", ["Товар", "Категория", "Бренд", "Продано", "Выручка", "Доля %", "ABC", "XYZ"] + (["Прибыль"] if fin else []),
                 [[x["name"], x["category"] or "", x["brand"] or "", x["qty"], x["net"], x["share"], x["abc"], x["xyz"]] + ([x["profit"]] if fin else []) for x in d["items"]]),
                ("Без продаж", ["Товар", "Остаток"] + (["По закупке"] if fin else []), [[x["name"], x["stock"]] + ([x["value"]] if fin else []) for x in d["zero_sales"]]),
                ("Нулевой остаток", ["Товар", "Продано за период"], [[x["name"], x["sold"]] for x in d["zero_stock"]]),
                ("Зона риска", ["Товар", "Остаток", "В день", "Хватит, дней"], [[x["name"], x["stock"], x["per_day"], x["cover"]] for x in d["risk"]]),
                ("Избыток", ["Товар", "Остаток", "В день", "Хватит, дней"], [[x["name"], x["stock"], x["per_day"], x["cover"]] for x in d["over"]])]
    if kind == "pnl":
        d = pnl(fl)
        hdr = ["Статья"] + [x["store"] for x in d["stores"]] + ["Итого"]
        rows = [["Выручка"] + [x["revenue"] for x in d["stores"]] + [d["total"]["revenue"]],
                ["Себестоимость"] + [-x["cost"] or 0 for x in d["stores"]] + [-d["total"]["cost"] or 0],
                ["Валовая прибыль"] + [x["gross"] for x in d["stores"]] + [d["total"]["gross"]]]
        rows += [[c] + [-x["expenses"][c] or 0 for x in d["stores"]] + [-d["total"]["expenses"][c] or 0] for c in d["categories"]]
        rows += [["Чистая прибыль"] + [x["net"] for x in d["stores"]] + [d["total"]["net"]],
                 ["Маржа чистая, %"] + [x["net_margin"] for x in d["stores"]] + [d["total"]["net_margin"]]]
        return [("Прибыль и убытки", hdr, rows)]
    if kind == "cashflow":
        d = cashflow(fl)
        return [("Денежный поток", ["Дата", "Наличные", "Карта", "QR", "Погашение долгов", "Поступления", "Возвраты", "Расходы", "Поставщикам", "Выплаты", "Итого за день", "Инкассация"],
                 [[x["d"], x["in_cash"], x["in_card"], x["in_qr"], x["in_debt"], x["inflow"], x["out_refund"], x["out_exp"], x["out_sup"], x["outflow"], x["net"], x["out_cash"]] for x in d["days"]])]
    if kind == "expenses":
        return [("Расходы", ["Дата", "Магазин", "Статья", "Как записано", "Сумма", "Комментарий", "Кто", "Источник"],
                 [[x["created_at"][:16], x["store"] or "Сеть", x["cat"], x["category"], x["amount"], x["note"], x["user_name"], "Центр" if x["source"] == "hq" else "Касса"] for x in expenses(fl)])]
    return None


@bp.get("/api/an/export/<kind>.xlsx")
def an_export(kind):
    try:
        fl = flt()
    except PermissionError as e:
        return H.err(str(e), 403)
    if kind in ("pnl", "cashflow", "expenses") and not H.rbac.can("finance"):
        return H.err("Недостаточно прав", 403)
    sh = build_sheets(kind, fl)
    return _xlsx(sh, f"{kind}_{fl['from']}_{fl['to']}") if sh else H.err("Неизвестный отчёт", 404)


PRINT = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>{{title}} {{fl['from']}} — {{fl['to']}}</title>
<style>@page{size:A4 landscape;margin:12mm}body{font:11px/1.4 Arial,sans-serif;color:#000;margin:0}h2{margin:0 0 2px;font-size:16px}
.m{color:#555;margin-bottom:8px}h3{font-size:13px;margin:14px 0 4px}table{width:100%;border-collapse:collapse;page-break-inside:auto}
th,td{border:1px solid #bbb;padding:3px 5px;text-align:left}th{background:#eef2ff}td.n,th.n{text-align:right;white-space:nowrap}
tr{page-break-inside:avoid}.bar{position:fixed;top:8px;right:8px}@media print{.bar{display:none}}</style></head><body>
<div class="bar"><button onclick="print()">Сохранить в PDF / печать</button></div>
<h2>{{title}}</h2><div class="m">Период {{fl['from']}} — {{fl['to']}}{% if store %} · {{store}}{% endif %}{% if fl.category %} · категория {{fl.category}}{% endif %}{% if fl.brand %} · бренд {{fl.brand}}{% endif %} · сформирован {{now}}</div>
{% for title2, hdr, rows in sheets %}<h3>{{title2}}</h3><table><tr>{% for h in hdr %}<th class="{{'n' if loop.index>1}}">{{h}}</th>{% endfor %}</tr>
{% for r in rows[:500] %}<tr>{% for v in r %}<td class="{{'n' if loop.index>1}}">{{fmt(v)}}</td>{% endfor %}</tr>{% endfor %}</table>{% endfor %}
<script>setTimeout(()=>print(),400)</script></body></html>"""
TITLES = {"sales": "Аналитика продаж", "products": "Аналитика товаров", "pnl": "Отчёт о прибылях и убытках", "cashflow": "Денежный поток", "expenses": "Расходы"}


@bp.get("/report/<kind>")
def print_report(kind):
    if not H.rbac.load_user():
        return "Требуется вход", 401
    H.rbac.g.user = H.rbac.load_user()
    try:
        fl = flt()
    except PermissionError as e:
        return str(e), 403
    if kind in ("pnl", "cashflow", "expenses") and not H.rbac.can("finance"):
        return "Недостаточно прав", 403
    if kind in ("sales", "products") and not H.rbac.can("sales"):
        return "Недостаточно прав", 403
    sh = build_sheets(kind, fl)
    if not sh:
        return "Неизвестный отчёт", 404
    st = H.q("SELECT name FROM stores WHERE id=?", (fl["store"],), one=True)["name"] if fl["store"] else ""

    def fmt(v):
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v == 0:
            return "0"
        if isinstance(v, float):
            return f"{v:,.2f}".replace(",", " ").rstrip("0").rstrip(".") if abs(v) < 1000 and v != int(v) else f"{v:,.0f}".replace(",", " ")
        if isinstance(v, int):
            return f"{v:,}".replace(",", " ")
        return "" if v is None else v
    return render_template_string(PRINT, title=TITLES.get(kind, kind), fl=fl, store=st, sheets=sh, fmt=fmt,
                                  now=H.tnow().strftime("%Y-%m-%d %H:%M"))


def register(hq_module):
    global H
    H = hq_module
    H.app.register_blueprint(bp)
