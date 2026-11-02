"""
Главный Dashboard магазина по ТЗ, центр уведомлений (ALERTS) и быстрый поиск по всей системе.
Все цифры — из базы, без заглушек.
"""
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request

bp = Blueprint("dash", __name__)
M = None
MONTHS = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
F = "%Y-%m-%d %H:%M:%S"
NET = "COALESCE(SUM(CASE WHEN s.operation='sale' THEN s.total ELSE -s.total END),0)"


def _admin():
    u = M.current_user()
    return u and u["role"] == "admin"


def _shift_back(dt, period):
    """Та же точка времени в прошлом периоде."""
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
    except ValueError:                    # 29 февраля
        return dt.replace(year=dt.year - 1, day=28)


def period_bounds(period, now):
    d0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = {"day": d0, "week": d0 - timedelta(days=d0.weekday()), "month": d0.replace(day=1),
             "year": d0.replace(month=1, day=1)}[period]
    return start, now, _shift_back(start, period), _shift_back(now, period)


def kpis(a0, a1):
    a = (a0.strftime(F), a1.strftime(F))
    r = M.q(f"""SELECT {NET} net, COUNT(CASE WHEN s.operation='sale' THEN 1 END) checks,
                COALESCE(SUM(CASE WHEN s.operation='refund' THEN s.total END),0) refunds,
                COALESCE(SUM(CASE WHEN s.operation='sale' THEN s.discount END),0) discount
                FROM sales s WHERE s.created_at BETWEEN ? AND ?""", a, one=True)
    c = M.q("""SELECT COALESCE(SUM(CASE WHEN s.operation='sale' THEN si.qty*si.cost_price ELSE -si.qty*si.cost_price END),0) cost,
               COALESCE(SUM(CASE WHEN s.operation='sale' THEN si.qty ELSE -si.qty END),0) items
               FROM sale_items si JOIN sales s ON s.id=si.sale_id WHERE s.created_at BETWEEN ? AND ?""", a, one=True)
    r["profit"] = round(r["net"] - c["cost"], 2)
    r["items"] = round(c["items"], 3)
    r["avg"] = round(r["net"] / r["checks"]) if r["checks"] else 0
    r["margin"] = round(r["profit"] / r["net"] * 100, 1) if r["net"] else 0
    return r


def _chg(c, p):
    return round((c - p) / abs(p) * 100, 1) if p else None


def series(period, start, end, pstart):
    """Динамика: часы (день), дни (неделя/месяц), месяцы (год). Текущий и прошлый период по одинаковым позициям."""
    if period == "day":
        expr, keys = "CAST(substr(s.created_at,12,2) AS INTEGER)", list(range(24))
        lab = lambda i: f"{i}"
    elif period == "year":
        expr, keys = "CAST(substr(s.created_at,6,2) AS INTEGER)", list(range(1, 13))
        lab = lambda i: MONTHS[i - 1]
    else:
        n = 7 if period == "week" else (start.replace(month=start.month % 12 + 1, year=start.year + (start.month == 12)) - start).days
        expr, keys = "substr(s.created_at,1,10)", list(range(n))
        lab = (lambda i: WD[i]) if period == "week" else (lambda i: str(i + 1))

    def grab(a0, a1):
        rows = M.q(f"""SELECT {expr} k, {NET} net, COUNT(CASE WHEN s.operation='sale' THEN 1 END) checks
                       FROM sales s WHERE s.created_at BETWEEN ? AND ? GROUP BY k""", (a0.strftime(F), a1.strftime(F)))
        return {r["k"]: r for r in rows}

    if period in ("week", "month"):
        base_c, base_p = start, pstart
        cur = grab(start, end)
        prev_end = (start - timedelta(seconds=1)) if period == "week" else (start - timedelta(seconds=1))
        prev = grab(pstart, prev_end)
        idx = lambda base, i: (base + timedelta(days=i)).strftime("%Y-%m-%d")
        out = []
        for i in keys:
            c, p = cur.get(idx(base_c, i), {}), prev.get(idx(base_p, i), {})
            fut = base_c + timedelta(days=i) > end
            out.append({"k": i, "label": lab(i), "net": None if fut else (c.get("net") or 0), "checks": c.get("checks") or 0,
                        "prev": p.get("net") or 0})
    else:
        cur = grab(start, end)
        if period == "day":
            prev = grab(pstart, pstart + timedelta(days=1) - timedelta(seconds=1))
            fut = lambda i: i > end.hour
        else:
            prev = grab(pstart, start - timedelta(seconds=1))
            fut = lambda i: i > end.month
        out = [{"k": i, "label": lab(i), "net": None if fut(i) else ((cur.get(i) or {}).get("net") or 0),
                "checks": (cur.get(i) or {}).get("checks") or 0, "prev": (prev.get(i) or {}).get("net") or 0} for i in keys]
    for r in out:
        r["avg"] = round(r["net"] / r["checks"]) if r["net"] and r["checks"] else None
    return out


@bp.get("/api/dash2")
def dash2():
    if not _admin():
        return M.err("Доступ только для администратора", 403)
    period = request.args.get("period", "day")
    if period not in ("day", "week", "month", "year"):
        period = "day"
    now = datetime.now()
    s0, s1, p0, p1 = period_bounds(period, now)
    cur, prev = kpis(s0, s1), kpis(p0, p1)
    m0, m1, mp0, mp1 = period_bounds("month", now)
    month, pmonth = kpis(m0, m1), kpis(mp0, mp1)
    chg = {k: _chg(cur[k], prev[k]) for k in ("net", "checks", "avg", "profit", "items", "margin")}
    a = (s0.strftime(F), s1.strftime(F))
    cats = M.q("""SELECT COALESCE(c.name,'Без категории') AS cat_name,
                  SUM(CASE WHEN s.operation='sale' THEN si.total ELSE -si.total END) net
                  FROM sale_items si JOIN sales s ON s.id=si.sale_id LEFT JOIN products p ON p.id=si.product_id
                  LEFT JOIN categories c ON c.id=p.category_id WHERE s.created_at BETWEEN ? AND ?
                  GROUP BY cat_name HAVING net>0 ORDER BY net DESC""", a)
    cats = [{"name": c["cat_name"], "net": c["net"]} for c in cats]
    if len(cats) > 7:
        cats = cats[:6] + [{"name": "Остальные", "net": sum(c["net"] for c in cats[6:])}]
    top = M.q("""SELECT si.product_id id, MAX(si.name) AS name, SUM(CASE WHEN s.operation='sale' THEN si.qty ELSE -si.qty END) q,
                 SUM(CASE WHEN s.operation='sale' THEN si.total ELSE -si.total END) t,
                 SUM(CASE WHEN s.operation='sale' THEN si.total-si.qty*si.cost_price ELSE -(si.total-si.qty*si.cost_price) END) profit
                 FROM sale_items si JOIN sales s ON s.id=si.sale_id WHERE s.created_at BETWEEN ? AND ?
                 GROUP BY si.product_id HAVING t>0 ORDER BY t DESC LIMIT 10""", a)
    since30 = (now - timedelta(days=30)).strftime(F)
    nosale = M.q("""SELECT p.id, p.name, p.stock, p.unit_name, ROUND(p.stock*p.cost_price) value,
                    (SELECT MAX(created_at) FROM stock_moves m WHERE m.product_id=p.id AND m.type='sale') last_sale
                    FROM products p WHERE p.active=1 AND p.stock>0 AND NOT EXISTS
                    (SELECT 1 FROM stock_moves m WHERE m.product_id=p.id AND m.type='sale' AND m.created_at>=?)
                    ORDER BY value DESC""", (since30,))
    stock = M.q("""SELECT COALESCE(SUM(CASE WHEN stock>0 THEN stock*cost_price END),0) cost,
                   COALESCE(SUM(CASE WHEN stock>0 THEN stock*price END),0) retail, COUNT(*) skus FROM products WHERE active=1""", one=True)
    return jsonify(period=period, now=now.strftime("%H:%M"), range=[s0.strftime(F), s1.strftime(F)], prev_range=[p0.strftime(F), p1.strftime(F)],
                   cur=cur, prev=prev, chg=chg, month=month, month_chg=_chg(month["net"], pmonth["net"]),
                   series=series(period, s0, s1, p0), categories=cats, top=top,
                   nosale={"n": len(nosale), "value": sum(r["value"] or 0 for r in nosale), "items": nosale[:8]},
                   stock=stock, shift=M.open_shift())


# ================================================================ ALERTS / уведомления
def alerts():
    now = datetime.now()
    since30 = (now - timedelta(days=30)).strftime(F)
    since7 = (now - timedelta(days=7)).strftime(F)
    out = []

    def add(aid, level, title, text, count, view, items=None):
        if count:
            out.append({"id": aid, "level": level, "title": title, "text": text, "count": count, "view": view, "items": items or []})

    rows = M.q("""SELECT name, stock FROM products p WHERE active=1 AND stock<=0 AND EXISTS
                  (SELECT 1 FROM stock_moves m WHERE m.product_id=p.id AND m.type='sale' AND m.created_at>=?) ORDER BY name""", (since30,))
    add("zero", "danger", "Нулевые остатки", "Товар продавался последние 30 дней, а сейчас его нет", len(rows), "products", [r["name"] for r in rows[:5]])
    rows = M.q("SELECT name, stock FROM products WHERE active=1 AND stock<0 ORDER BY stock")
    add("negative", "danger", "Минусовые остатки", "Продано больше, чем было — проверьте приход или проведите ревизию", len(rows), "inventory",
        [f"{r['name']} ({r['stock']:g})" for r in rows[:5]])
    rows = M.q("SELECT name, stock, min_stock FROM products WHERE active=1 AND min_stock>0 AND stock>0 AND stock<=min_stock ORDER BY stock/min_stock")
    add("low", "warn", "Заканчивается товар", "Остаток не выше минимального", len(rows), "products", [f"{r['name']} — {r['stock']:g}" for r in rows[:5]])
    try:
        ex = M.grocery.expiry_summary()
        add("expired", "danger", "Просроченный товар", "Уберите с полки и спишите", ex.get("expired", 0), "expiry", ex.get("names", [])[:5])
        add("expiring", "warn", "Истекает срок годности", "Уцените или поставьте на видное место", ex.get("soon", 0), "expiry")
    except Exception:
        pass
    rows = M.q("""SELECT name FROM products p WHERE active=1 AND stock>0 AND NOT EXISTS
                  (SELECT 1 FROM stock_moves m WHERE m.product_id=p.id AND m.type='sale' AND m.created_at>=?)
                  AND EXISTS (SELECT 1 FROM stock_moves m WHERE m.product_id=p.id AND m.created_at<?)""", (since30, since30))
    add("nosale", "info", "Товары без продаж", "Есть на складе, но не продавались 30 дней", len(rows), "reports", [r["name"] for r in rows[:5]])
    w0 = M.q(f"SELECT {NET} v FROM sales s WHERE s.created_at>=?", (since7,), one=True)["v"]
    w1 = M.q(f"SELECT {NET} v FROM sales s WHERE s.created_at>=? AND s.created_at<?",
             ((now - timedelta(days=14)).strftime(F), since7), one=True)["v"]
    if w1 > 0 and w0 < w1 * 0.8:
        add("drop", "warn", "Падение продаж", f"За 7 дней выручка на {round((1 - w0 / w1) * 100)}% ниже, чем неделей раньше", 1, "reports")
    rows = M.q("""SELECT p.name, SUM(m.qty) q FROM stock_moves m JOIN products p ON p.id=m.product_id
                  WHERE m.type='inventory' AND m.created_at>=? AND COALESCE(m.note,'') NOT LIKE 'Перенос%'
                  AND COALESCE(m.note,'') NOT LIKE 'Начальный%' AND COALESCE(m.note,'')<>'Импорт'
                  GROUP BY m.product_id HAVING q<0 ORDER BY q""", (since7,))
    add("invdiff", "warn", "Расхождения инвентаризации", "Недостачи по ревизиям за 7 дней", len(rows), "inventory",
        [f"{r['name']} ({r['q']:g})" for r in rows[:5]])
    rows = M.q("SELECT action, details, created_at FROM audit WHERE severity>=2 AND created_at>=? ORDER BY id DESC", (since7,))
    add("suspicious", "danger", "Подозрительные операции", "Важные события журнала за 7 дней", len(rows), "audit",
        [f"{r['created_at'][5:16]} {r['action']}" for r in rows[:5]])
    n = M.q("SELECT COUNT(*) n FROM sales WHERE fiscal_status IN ('pending','error')", one=True)["n"]
    add("unfiscal", "danger", "Чеки не отправлены в фискальный модуль", "Система повторяет автоматически; проверьте связь с ФМ", n, "sales")
    n = M.q("SELECT COUNT(*) n FROM docs WHERE status IN ('expected','receiving','to_ship')", one=True)["n"]
    add("docs", "info", "Необработанные накладные и перемещения", "Ждут приёмки или отгрузки", n, "receive")
    rows = M.q("""SELECT s.name, s.balance FROM suppliers s WHERE s.balance>0 AND EXISTS
                  (SELECT 1 FROM purchases p WHERE p.supplier_id=s.id AND p.due_date IS NOT NULL AND p.due_date<?)""", (now.strftime("%Y-%m-%d"),))
    add("supdue", "warn", "Просрочена оплата поставщикам", "Наступил срок оплаты", len(rows), "suppliers",
        [f"{r['name']} — {r['balance']:,.0f}".replace(",", " ") for r in rows[:5]])
    order = {"danger": 0, "warn": 1, "info": 2}
    out.sort(key=lambda x: order[x["level"]])
    return out


@bp.get("/api/alerts")
def alerts_api():
    u = M.current_user()
    if not u:
        return M.err("Требуется вход", 401)
    items = alerts()
    if u["role"] != "admin":   # кассиру — только то, что касается его работы
        items = [a for a in items if a["id"] in ("expired", "expiring", "unfiscal", "docs", "zero")]
    return jsonify(items=items, total=sum(1 for a in items if a["level"] != "info"))


# ================================================================ поиск по системе
@bp.get("/api/search")
def search():
    u = M.current_user()
    if not u:
        return M.err("Требуется вход", 401)
    t = (request.args.get("q") or "").strip()
    if len(t) < 2:
        return jsonify(groups=[])
    like = f"%{t.lower()}%"
    digits = "".join(c for c in t if c.isdigit())
    groups = []
    prods = M.q(M.PRODUCT_SELECT + """ WHERE p.active=1 AND (ulower(p.name) LIKE ? OR p.barcode=? OR ulower(p.sku) LIKE ?
                OR p.id IN (SELECT product_id FROM product_barcodes WHERE barcode=?)) ORDER BY p.name LIMIT 8""", (like, t, like, t))
    if prods:
        groups.append({"kind": "products", "title": "Товары", "items": prods})
    cust = M.q("""SELECT id, name, phone, bonus, debt FROM customers WHERE ulower(name) LIKE ? OR card=?
                  OR (? <> '' AND phone LIKE ?) ORDER BY name LIMIT 5""", (like, t, digits, f"%{digits}%"))
    if cust:
        groups.append({"kind": "customers", "title": "Клиенты", "items": cust})
    if digits and len(digits) <= 9:
        s = M.q("""SELECT s.id, s.created_at, s.total, s.operation, u.name AS cashier FROM sales s LEFT JOIN users u ON u.id=s.user_id
                   WHERE s.id=? OR s.receipt_seq=?""", (int(digits), int(digits)))
        if s:
            groups.append({"kind": "sales", "title": "Чеки", "items": s})
    if u["role"] == "admin":
        sup = M.q("SELECT id, name, phone, balance FROM suppliers WHERE ulower(name) LIKE ? OR phone LIKE ? LIMIT 5", (like, like))
        if sup:
            groups.append({"kind": "suppliers", "title": "Поставщики", "items": sup})
    return jsonify(groups=groups)


def register(app_module):
    global M
    M = app_module
    M.app.register_blueprint(bp)
