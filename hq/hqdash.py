"""
Центр сети: главный Dashboard по ТЗ (периоды и сравнения, выручка и прибыль по магазинам, категории,
топ-10, товары без продаж, средний чек) и ALERTS сети. Работает и на PostgreSQL, и на SQLite.
"""
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request

bp = Blueprint("hqdash", __name__)
H = None
F = "%Y-%m-%d %H:%M:%S"
MONTHS = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
SGN = "(CASE WHEN s.operation='sale' THEN 1 ELSE -1 END)"


def _back(dt, period):
    if period == "day":
        return dt - timedelta(days=1)
    if period == "week":
        return dt - timedelta(days=7)
    if period == "month":
        y, m = (dt.year, dt.month - 1) if dt.month > 1 else (dt.year - 1, 12)
        last = (datetime(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).day
        return dt.replace(year=y, month=m, day=min(dt.day, last))
    try:
        return dt.replace(year=dt.year - 1)
    except ValueError:
        return dt.replace(year=dt.year - 1, day=28)


def bounds(period, now):
    d0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = {"day": d0, "week": d0 - timedelta(days=d0.weekday()), "month": d0.replace(day=1), "year": d0.replace(month=1, day=1)}[period]
    return start, now, _back(start, period), _back(now, period)


def sfs(col="s.store_id"):
    return H.rbac.sf(col)


def per_store(a0, a1):
    f, fa = sfs()
    a = (a0.strftime(F), a1.strftime(F)) + fa
    rows = H.q(f"""SELECT s.store_id, COALESCE(SUM({SGN}*s.total),0) net,
                   COUNT(CASE WHEN s.operation='sale' THEN 1 END) checks FROM sales s
                   WHERE s.created_at BETWEEN ? AND ?{f} GROUP BY s.store_id""", a)
    prof = {r["store_id"]: r for r in H.q(f"""SELECT s.store_id, COALESCE(SUM({SGN}*(si.total-si.qty*COALESCE(si.cost_price,0))),0) profit,
                   COALESCE(SUM({SGN}*si.qty),0) items
                   FROM sale_items si JOIN sales s ON s.uid=si.sale_uid WHERE s.created_at BETWEEN ? AND ?{f} GROUP BY s.store_id""", a)}
    out = {}
    for r in rows:
        p = prof.get(r["store_id"], {})
        out[r["store_id"]] = {"net": r["net"] or 0, "checks": r["checks"] or 0, "profit": p.get("profit") or 0, "items": p.get("items") or 0}
    return out


def total(ps):
    t = {k: sum(v[k] for v in ps.values()) for k in ("net", "checks", "profit", "items")}
    t["avg"] = round(t["net"] / t["checks"]) if t["checks"] else 0
    t["margin"] = round(t["profit"] / t["net"] * 100, 1) if t["net"] else 0
    return t


def _chg(c, p):
    return round((c - p) / abs(p) * 100, 1) if p else None


def series(period, start, end, pstart):
    if period == "day":
        expr, keys, lab = "CAST(substr(s.created_at,12,2) AS INTEGER)", list(range(24)), str
    elif period == "year":
        expr, keys, lab = "CAST(substr(s.created_at,6,2) AS INTEGER)", list(range(1, 13)), lambda i: MONTHS[i - 1]
    else:
        n = 7 if period == "week" else (start.replace(month=start.month % 12 + 1, year=start.year + (start.month == 12)) - start).days
        expr, keys = "substr(s.created_at,1,10)", list(range(n))
        lab = (lambda i: WD[i]) if period == "week" else (lambda i: str(i + 1))

    f, fa = sfs()

    def grab(a0, a1):
        return {r["k"]: r for r in H.q(f"""SELECT {expr} AS k, COALESCE(SUM({SGN}*s.total),0) net,
                   COUNT(CASE WHEN s.operation='sale' THEN 1 END) checks FROM sales s
                   WHERE s.created_at BETWEEN ? AND ?{f} GROUP BY {expr}""", (a0.strftime(F), a1.strftime(F)) + fa)}

    out = []
    if period in ("week", "month"):
        cur, prev = grab(start, end), grab(pstart, start - timedelta(seconds=1))
        for i in keys:
            dc, dp = start + timedelta(days=i), pstart + timedelta(days=i)
            c, p = cur.get(dc.strftime("%Y-%m-%d"), {}), prev.get(dp.strftime("%Y-%m-%d"), {})
            out.append({"label": lab(i), "net": None if dc > end else (c.get("net") or 0), "checks": c.get("checks") or 0, "prev": p.get("net") or 0})
    else:
        cur = grab(start, end)
        prev = grab(pstart, pstart + timedelta(days=1) - timedelta(seconds=1)) if period == "day" else grab(pstart, start - timedelta(seconds=1))
        lim = end.hour if period == "day" else end.month
        for i in keys:
            c, p = cur.get(i, {}), prev.get(i, {})
            out.append({"label": lab(i), "net": None if i > lim else (c.get("net") or 0), "checks": c.get("checks") or 0, "prev": p.get("net") or 0})
    for r in out:
        r["avg"] = round(r["net"] / r["checks"]) if r["net"] and r["checks"] else None
    return out


def month_plans(month):
    f, fa = sfs("store_id")
    return {r["store_id"]: r["amount"] or 0 for r in H.q(f"SELECT store_id, amount FROM store_plans WHERE month=?{f}", (month,) + fa)}


def _online(last_seen, now):
    return bool(last_seen) and (now - datetime.strptime(last_seen, F)).total_seconds() < 180


@bp.get("/api/dash2")
def dash2():
    period = request.args.get("period", "day")
    if period not in ("day", "week", "month", "year"):
        period = "day"
    now = H.tnow()
    s0, s1, p0, p1 = bounds(period, now)
    m0, m1, mp0, mp1 = bounds("month", now)
    cur_s, prev_s = per_store(s0, s1), per_store(p0, p1)
    cur, prev = total(cur_s), total(prev_s)
    month, pmonth = total(per_store(m0, m1)), total(per_store(mp0, mp1))
    fi, fia = sfs("id")
    stores = H.q(f"SELECT id, name, address, last_seen FROM stores WHERE active=1{fi} ORDER BY id", fia)
    stock_by = {r["store_id"]: r for r in H.q(f"""SELECT st.store_id, COALESCE(SUM(CASE WHEN st.qty>0 THEN st.qty*p.cost_price ELSE 0 END),0) value,
                    COUNT(CASE WHEN st.qty>0 THEN 1 END) skus FROM stock st JOIN products p ON p.uid=st.product_uid WHERE 1=1{sfs("st.store_id")[0]} GROUP BY st.store_id""", sfs("st.store_id")[1])}
    for s in stores:
        c, p = cur_s.get(s["id"], {}), prev_s.get(s["id"], {})
        s.update(net=c.get("net", 0), checks=c.get("checks", 0), profit=c.get("profit", 0), items=c.get("items", 0),
                 prev=p.get("net", 0), online=_online(s["last_seen"], now),
                 stock_value=(stock_by.get(s["id"]) or {}).get("value", 0), skus=(stock_by.get(s["id"]) or {}).get("skus", 0))
        s["avg"] = round(s["net"] / s["checks"]) if s["checks"] else 0
        s["margin"] = round(s["profit"] / s["net"] * 100, 1) if s["net"] else 0
        s["chg"] = _chg(s["net"], s["prev"])
    f, fa = sfs()
    a = (s0.strftime(F), s1.strftime(F)) + fa
    cats = H.q(f"""SELECT COALESCE(NULLIF(p.category,''),'Без категории') AS cat, COALESCE(SUM({SGN}*si.total),0) net
                   FROM sale_items si JOIN sales s ON s.uid=si.sale_uid LEFT JOIN products p ON p.uid=si.product_uid
                   WHERE s.created_at BETWEEN ? AND ?{f} GROUP BY COALESCE(NULLIF(p.category,''),'Без категории') ORDER BY net DESC""", a)
    cats = [{"name": c["cat"], "net": c["net"]} for c in cats if (c["net"] or 0) > 0]
    if len(cats) > 7:
        cats = cats[:6] + [{"name": "Остальные", "net": sum(c["net"] for c in cats[6:])}]
    top = H.q(f"""SELECT MAX(si.name) AS name, COALESCE(SUM({SGN}*si.qty),0) q, COALESCE(SUM({SGN}*si.total),0) t
                  FROM sale_items si JOIN sales s ON s.uid=si.sale_uid WHERE s.created_at BETWEEN ? AND ?{f}
                  GROUP BY si.product_uid ORDER BY t DESC LIMIT 10""", a)
    since30 = (now - timedelta(days=30)).strftime(F)
    fs_, fsa = sfs("st.store_id")
    nosale = H.q(f"""SELECT p.uid, p.name, SUM(st.qty) qty, SUM(st.qty)*p.cost_price AS value FROM products p
                    JOIN stock st ON st.product_uid=p.uid AND st.qty>0{fs_}
                    WHERE p.active=1 AND NOT EXISTS (SELECT 1 FROM sale_items si JOIN sales s ON s.uid=si.sale_uid
                      WHERE si.product_uid=p.uid AND s.store_id=st.store_id AND s.created_at>=?)
                    GROUP BY p.uid, p.name, p.cost_price ORDER BY value DESC""", fsa + (since30,))
    chg = {k: _chg(cur[k], prev[k]) for k in ("net", "checks", "avg", "profit", "items", "margin")}
    plans = month_plans(now.strftime("%Y-%m"))
    for s in stores:
        s["plan"] = plans.get(s["id"], 0)
    fin = H.rbac.can("finance")
    if not fin:     # без права «finance» прибыль и себестоимость не показываем
        for d in (cur, prev, month):
            d["profit"] = d["margin"] = None
        chg["profit"] = chg["margin"] = None
        for s in stores:
            s["profit"] = s["margin"] = s["stock_value"] = None
        for r in nosale:
            r["value"] = None
    return jsonify(finance=fin, month_plan=sum(plans.get(s["id"], 0) for s in stores),
                   month_store={s_id: v for s_id, v in per_store(m0, m1).items()},period=period, now=now.strftime("%H:%M"), cur=cur, prev=prev, chg=chg, month=month,
                   month_chg=_chg(month["net"], pmonth["net"]), stores=stores,
                   active_stores=sum(1 for s in stores if s["online"]), stock_value=sum(s["stock_value"] or 0 for s in stores) if fin else None,
                   series=series(period, s0, s1, p0), categories=cats, top=top,
                   nosale={"n": len(nosale), "value": sum(r["value"] or 0 for r in nosale) if fin else None, "items": nosale[:8]})


def alerts():
    now = H.tnow()
    out = []

    def add(aid, level, title, text, count, view, items=None):
        if count:
            out.append({"id": aid, "level": level, "title": title, "text": text, "count": count, "view": view, "items": items or []})

    since30 = (now - timedelta(days=30)).strftime(F)
    fz, fza = sfs("sr.id")
    rows = H.q("""SELECT p.name, st.qty, sr.name AS store FROM stock st JOIN products p ON p.uid=st.product_uid JOIN stores sr ON sr.id=st.store_id
                  WHERE sr.active=1{fz} AND st.qty<=0 AND EXISTS (SELECT 1 FROM sale_items si JOIN sales s ON s.uid=si.sale_uid
                  WHERE si.product_uid=st.product_uid AND s.store_id=st.store_id AND s.created_at>=?) ORDER BY sr.name, p.name""".replace("{fz}", fz), fza + (since30,))
    add("zero", "danger", "Нулевые остатки", "Товар продавался в магазине, а сейчас его нет", len(rows), "stock", [f"{r['store']}: {r['name']}" for r in rows[:5]])
    rows = H.q("""SELECT p.name, st.qty, sr.name AS store FROM stock st JOIN products p ON p.uid=st.product_uid JOIN stores sr ON sr.id=st.store_id
                  WHERE sr.active=1 AND p.min_stock>0 AND st.qty>0 AND st.qty<=p.min_stock{fz} ORDER BY sr.name""".replace("{fz}", fz), fza)
    add("low", "warn", "Заканчивается товар", "Остаток в магазине не выше минимального", len(rows), "stock", [f"{r['store']}: {r['name']} — {r['qty']:g}" for r in rows[:5]])
    s7, s14 = (now - timedelta(days=7)).strftime(F), (now - timedelta(days=14)).strftime(F)
    drop = []
    fi, fia = sfs("id")
    for s in H.q(f"SELECT id, name FROM stores WHERE active=1{fi}", fia):
        w0 = H.q(f"SELECT COALESCE(SUM({SGN}*s.total),0) v FROM sales s WHERE s.store_id=? AND s.created_at>=?", (s["id"], s7), one=True)["v"]
        w1 = H.q(f"SELECT COALESCE(SUM({SGN}*s.total),0) v FROM sales s WHERE s.store_id=? AND s.created_at>=? AND s.created_at<?", (s["id"], s14, s7), one=True)["v"]
        if w1 > 0 and w0 < w1 * 0.8:
            drop.append(f"{s['name']} −{round((1 - w0 / w1) * 100)}%")
    add("drop", "warn", "Магазины с падением продаж", "Выручка за 7 дней на 20%+ ниже предыдущей недели", len(drop), "sales", drop[:5])
    fd, fda = sfs("d.to_store_id")
    rows = H.q(f"""SELECT d.number, ts.name AS store FROM docs d LEFT JOIN stores ts ON ts.id=d.to_store_id WHERE d.status='received' AND d.received_at>=?{fd}
                  AND EXISTS(SELECT 1 FROM doc_lines l WHERE l.doc_uid=d.uid AND ABS(COALESCE(l.qty_fact,0)-l.qty)>0.0001)""", (s14,) + fda)
    add("disc", "warn", "Расхождения при приёмке", "Принято не столько, сколько отправлено (14 дней)", len(rows), "docs", [f"{r['number']} → {r['store']}" for r in rows[:5]])
    rows = H.q("""SELECT d.number, d.status, ts.name AS store, d.created_at FROM docs d LEFT JOIN stores ts ON ts.id=d.to_store_id
                  WHERE d.status IN ('to_ship','preparing','sent','in_transit'){fd} ORDER BY d.created_at""".replace("{fd}", fd), fda)
    add("transit", "info", "Необработанные перемещения и поставки", "Ждут отгрузки или приёмки", len(rows), "docs", [f"{r['number']} → {r['store']}" for r in rows[:5]])
    if H.rbac.can("docs.approve"):
        fa_, faa = sfs("d.from_store_id")
        rows = H.q(f"""SELECT d.number, fs.name AS fr, ts.name AS tt FROM docs d LEFT JOIN stores fs ON fs.id=d.from_store_id
                       LEFT JOIN stores ts ON ts.id=d.to_store_id WHERE d.status='pending'{fa_}""", faa)
        add("approve", "warn", "Перемещения ждут согласования", "Одобрите или верните на доработку", len(rows), "docs",
            [f"{r['number']}: {r['fr']} → {r['tt']}" for r in rows[:5]])
        rows = H.q(f"""SELECT d.number FROM docs d WHERE d.type='transfer' AND d.status='received'{fa_}""", faa)
        add("toclose", "info", "Перемещения с расхождениями", "Получены не полностью — проверьте и завершите", len(rows), "docs", [r["number"] for r in rows[:5]])
    off = [s["name"] for s in H.q(f"SELECT name, last_seen FROM stores WHERE active=1{fi}", fia) if not _online(s["last_seen"], now)]
    add("offline", "warn", "Кассы без связи", "Нет синхронизации больше 3 минут — данные могут быть неполными", len(off), "stores", off[:5])
    fst, fsta = sfs("st.store_id")
    rows = H.q(f"""SELECT p.name FROM products p WHERE p.active=1 AND EXISTS (SELECT 1 FROM stock st WHERE st.product_uid=p.uid AND st.qty>0{fst}
                  AND NOT EXISTS (SELECT 1 FROM sale_items si JOIN sales s ON s.uid=si.sale_uid WHERE si.product_uid=p.uid AND s.store_id=st.store_id AND s.created_at>=?))""", fsta + (since30,))
    add("nosale", "info", "Товары без продаж", "Есть в магазинах, но не продавались 30 дней", len(rows), "stock", [r["name"] for r in rows[:5]])
    fs7, fs7a = sfs("store_id")
    n = H.q(f"SELECT COUNT(*) n FROM sales WHERE fiscal_status IN ('pending','error') AND created_at>=?{fs7}", (s7,) + fs7a, one=True)["n"]
    add("unfiscal", "danger", "Чеки без фискализации", "В магазинах есть чеки, не отправленные в ФМ (7 дней)", n, "sales")
    order = {"danger": 0, "warn": 1, "info": 2}
    out.sort(key=lambda x: order[x["level"]])
    return out


@bp.get("/api/alerts")
def alerts_api():
    items = alerts()
    return jsonify(items=items, total=sum(1 for a in items if a["level"] != "info"))


def session_ok():
    from flask import session
    return bool(session.get("owner"))


def register(hq_module):
    global H
    H = hq_module
    H.app.register_blueprint(bp)
