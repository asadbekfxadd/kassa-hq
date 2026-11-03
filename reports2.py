"""
Аналитика магазина: обзор бизнеса, продажи (динамика, тепловая карта, оплаты, сравнение периодов),
товары (ABC/XYZ, оборачиваемость, замороженные деньги), оборотная ведомость, прибыль и расходы,
сотрудники (KPI и антифрод), НДС. Любой отчёт выгружается в Excel.
"""
import io
import math
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request, Response

import db

bp = Blueprint("reports2", __name__)
M = None
WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
HIST_NOTE = "История продаж%"


def _admin():
    u = M.current_user()
    return u and u["role"] == "admin"


def _period():
    today = datetime.now().strftime("%Y-%m-%d")
    f = request.args.get("from") or today
    t = request.args.get("to") or f
    return f, t


def _days(f, t):
    return (datetime.strptime(t, "%Y-%m-%d") - datetime.strptime(f, "%Y-%m-%d")).days + 1


def _shift(f, t):
    """Предыдущий период такой же длины."""
    n = _days(f, t)
    pf = (datetime.strptime(f, "%Y-%m-%d") - timedelta(days=n)).strftime("%Y-%m-%d")
    pt = (datetime.strptime(f, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    return pf, pt


def totals(f, t):
    a = (f + " 00:00:00", t + " 23:59:59")
    r = M.q("""SELECT
        COALESCE(SUM(CASE WHEN operation='sale' THEN total ELSE -total END),0) net,
        COALESCE(SUM(CASE WHEN operation='refund' THEN total END),0) refunds,
        COUNT(CASE WHEN operation='sale' THEN 1 END) checks,
        COALESCE(SUM(CASE WHEN operation='sale' THEN discount END),0) discount
        FROM sales WHERE created_at BETWEEN ? AND ?""", a, one=True)
    c = M.q("""SELECT COALESCE(SUM(CASE WHEN s.operation='sale' THEN si.qty*si.cost_price ELSE -si.qty*si.cost_price END),0) cost,
               COALESCE(SUM(CASE WHEN s.operation='sale' THEN 1 END),0) lines
               FROM sale_items si JOIN sales s ON s.id=si.sale_id WHERE s.created_at BETWEEN ? AND ?""", a, one=True)
    r["cost"] = round(c["cost"], 2)
    r["profit"] = round(r["net"] - c["cost"], 2)
    r["avg"] = round(r["net"] / r["checks"], 2) if r["checks"] else 0
    r["margin"] = round(r["profit"] / r["net"] * 100, 1) if r["net"] else 0
    r["lines_per_check"] = round(c["lines"] / r["checks"], 2) if r["checks"] else 0
    return r


def _cmp(cur, prev):
    out = {}
    for k in ("net", "checks", "avg", "profit", "margin", "lines_per_check", "discount", "refunds"):
        p = prev.get(k) or 0
        out[k] = round((cur[k] - p) / abs(p) * 100, 1) if p else None
    return out


# ================================================================ обзор
@bp.get("/api/dash")
def dash():
    if not _admin():
        return M.err("Доступ только для администратора", 403)
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    yest = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    cur = totals(today, today)
    # вчера к этому же часу — честное сравнение «сегодня пока»
    y = M.q("""SELECT COALESCE(SUM(CASE WHEN operation='sale' THEN total ELSE -total END),0) net,
               COUNT(CASE WHEN operation='sale' THEN 1 END) checks FROM sales
               WHERE created_at BETWEEN ? AND ?""", (yest + " 00:00:00", yest + now.strftime(" %H:%M:%S")), one=True)
    week = []
    for i in range(13, -1, -1):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        r = M.q("""SELECT COALESCE(SUM(CASE WHEN operation='sale' THEN total ELSE -total END),0) net,
                   COUNT(CASE WHEN operation='sale' THEN 1 END) checks FROM sales WHERE created_at BETWEEN ? AND ?""",
                (d + " 00:00:00", d + " 23:59:59"), one=True)
        week.append({"d": d, "wd": WD[(now - timedelta(days=i)).weekday()], "net": r["net"], "checks": r["checks"]})
    hours = M.q("""SELECT CAST(substr(created_at,12,2) AS INTEGER) h, SUM(CASE WHEN operation='sale' THEN total ELSE -total END) net
                   FROM sales WHERE created_at BETWEEN ? AND ? GROUP BY h ORDER BY h""", (today + " 00:00:00", today + " 23:59:59"))
    pay = M.q("""SELECT COALESCE(SUM(CASE WHEN operation='sale' THEN cash ELSE -cash END),0) cash,
                 COALESCE(SUM(CASE WHEN operation='sale' THEN card ELSE -card END),0) card,
                 COALESCE(SUM(CASE WHEN operation='sale' THEN qr ELSE -qr END),0) qr,
                 COALESCE(SUM(CASE WHEN operation='sale' THEN debt ELSE -debt END),0) debt
                 FROM sales WHERE created_at BETWEEN ? AND ?""", (today + " 00:00:00", today + " 23:59:59"), one=True)
    recent = M.q("""SELECT s.id, s.created_at, s.operation, s.total, s.cash, s.card, s.qr, s.debt, u.name AS cashier,
                    (SELECT COUNT(*) FROM sale_items WHERE sale_id=s.id) lines
                    FROM sales s LEFT JOIN users u ON u.id=s.user_id ORDER BY s.id DESC LIMIT 8""")
    top = M.q("""SELECT si.name, SUM(si.qty) q, SUM(si.total) t FROM sale_items si JOIN sales s ON s.id=si.sale_id
                 WHERE s.operation='sale' AND s.created_at BETWEEN ? AND ? GROUP BY si.product_id ORDER BY t DESC LIMIT 6""",
              (today + " 00:00:00", today + " 23:59:59"))
    since30 = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    stock = M.q("""SELECT COUNT(*) skus,
                   COUNT(CASE WHEN stock<=0 AND EXISTS(SELECT 1 FROM stock_moves m WHERE m.product_id=p.id AND m.type='sale' AND m.created_at>=?) THEN 1 END) out_selling,
                   COUNT(CASE WHEN min_stock>0 AND stock>0 AND stock<=min_stock THEN 1 END) low,
                   COUNT(CASE WHEN stock<0 THEN 1 END) negative,
                   COALESCE(SUM(CASE WHEN stock>0 THEN stock*cost_price END),0) value
                   FROM products p WHERE active=1""", (since30,), one=True)
    try:
        ex = M.grocery.expiry_summary()
    except Exception:
        ex = {"expired": 0, "soon": 0}
    stock.update(expired=ex["expired"], expiring=ex["soon"])
    problems = stock["out_selling"] + stock["negative"] + stock["expired"]
    stock["status"] = "bad" if problems else ("warn" if stock["low"] or stock["expiring"] else "ok")
    money_ = {"customers_debt": M.q("SELECT COALESCE(SUM(debt),0) v FROM customers", one=True)["v"],
              "suppliers_debt": M.q("SELECT COALESCE(SUM(balance),0) v FROM suppliers WHERE balance>0", one=True)["v"],
              "unfiscal": M.q("SELECT COUNT(*) n FROM sales WHERE fiscal_status IN ('pending','error')", one=True)["n"]}
    sh = M.open_shift()
    return jsonify(today=cur, yesterday=y, week=week, hours=hours, pay=pay, recent=recent, top=top, stock=stock,
                   money=money_, shift=sh, now=now.strftime("%H:%M"))


# ================================================================ продажи
def sales_report(f, t):
    a = (f + " 00:00:00", t + " 23:59:59")
    cur = totals(f, t)
    pf, pt = _shift(f, t)
    prev = totals(pf, pt)
    by_day = M.q("""SELECT substr(created_at,1,10) d, SUM(CASE WHEN operation='sale' THEN total ELSE -total END) net,
                    COUNT(CASE WHEN operation='sale' THEN 1 END) checks FROM sales WHERE created_at BETWEEN ? AND ?
                    GROUP BY d ORDER BY d""", a)
    heat_rows = M.q("""SELECT created_at, total FROM sales WHERE operation='sale' AND created_at BETWEEN ? AND ?""", a)
    heat = [[0] * 24 for _ in range(7)]
    heat_n = [[0] * 24 for _ in range(7)]
    for r in heat_rows:
        dt = datetime.strptime(r["created_at"][:19], "%Y-%m-%d %H:%M:%S")
        heat[dt.weekday()][dt.hour] += r["total"]
        heat_n[dt.weekday()][dt.hour] += 1
    pay = M.q("""SELECT COALESCE(SUM(CASE WHEN operation='sale' THEN cash ELSE -cash END),0) cash,
                 COALESCE(SUM(CASE WHEN operation='sale' THEN card ELSE -card END),0) card,
                 COALESCE(SUM(CASE WHEN operation='sale' THEN qr ELSE -qr END),0) qr,
                 COALESCE(SUM(CASE WHEN operation='sale' THEN debt ELSE -debt END),0) debt,
                 COALESCE(SUM(CASE WHEN operation='sale' THEN bonus_used ELSE -bonus_used END),0) bonus
                 FROM sales WHERE created_at BETWEEN ? AND ?""", a, one=True)
    cats = M.q("""SELECT COALESCE(c.name,'Без категории') AS cat, SUM(CASE WHEN s.operation='sale' THEN si.total ELSE -si.total END) net,
                  SUM(CASE WHEN s.operation='sale' THEN si.total-si.qty*si.cost_price ELSE -(si.total-si.qty*si.cost_price) END) profit
                  FROM sale_items si JOIN sales s ON s.id=si.sale_id LEFT JOIN products p ON p.id=si.product_id
                  LEFT JOIN categories c ON c.id=p.category_id WHERE s.created_at BETWEEN ? AND ? GROUP BY cat ORDER BY net DESC""", a)
    promos = M.q("""SELECT si.promo, COUNT(*) n, SUM(si.total) net FROM sale_items si JOIN sales s ON s.id=si.sale_id
                    WHERE s.operation='sale' AND si.promo IS NOT NULL AND s.created_at BETWEEN ? AND ? GROUP BY si.promo""", a)
    customers = M.q("""SELECT COUNT(DISTINCT customer_id) n, COALESCE(SUM(CASE WHEN customer_id IS NOT NULL AND operation='sale' THEN total END),0) net
                       FROM sales WHERE created_at BETWEEN ? AND ?""", a, one=True)
    return {"totals": cur, "prev": prev, "prev_period": [pf, pt], "change": _cmp(cur, prev), "by_day": by_day,
            "heat": heat, "heat_n": heat_n, "pay": pay, "categories": cats, "promos": promos, "customers": customers}


@bp.get("/api/rep/sales")
def rep_sales():
    if not _admin():
        return M.err("Доступ только для администратора", 403)
    return jsonify(sales_report(*_period()))


# ================================================================ товары: ABC / XYZ / оборачиваемость
def products_report(f, t):
    a = (f + " 00:00:00", t + " 23:59:59")
    days = max(1, _days(f, t))
    rows = M.q("""SELECT p.id, p.name, p.barcode, p.stock, p.cost_price, p.price, p.unit_name, COALESCE(c.name,'') AS category,
                  COALESCE(SUM(CASE WHEN s.operation='sale' THEN si.qty WHEN s.operation='refund' THEN -si.qty END),0) qty,
                  COALESCE(SUM(CASE WHEN s.operation='sale' THEN si.total WHEN s.operation='refund' THEN -si.total END),0) revenue,
                  COALESCE(SUM(CASE WHEN s.operation='sale' THEN si.total-si.qty*si.cost_price
                                    WHEN s.operation='refund' THEN -(si.total-si.qty*si.cost_price) END),0) profit
                  FROM products p LEFT JOIN categories c ON c.id=p.category_id
                  LEFT JOIN sale_items si ON si.product_id=p.id
                  LEFT JOIN sales s ON s.id=si.sale_id AND s.created_at BETWEEN ? AND ?
                  WHERE p.active=1 GROUP BY p.id""", a)
    # продажи по корзинам времени для XYZ: недели, если период >= 28 дней, иначе дни
    bucket = 7 if days >= 28 else 1
    nb = max(1, math.ceil(days / bucket))
    series = {}
    start = datetime.strptime(f, "%Y-%m-%d")
    for r in M.q("""SELECT product_id, created_at, -qty AS q FROM stock_moves WHERE type='sale' AND created_at BETWEEN ? AND ?""", a):
        i = min(nb - 1, (datetime.strptime(r["created_at"][:10], "%Y-%m-%d") - start).days // bucket)
        series.setdefault(r["product_id"], [0.0] * nb)[i] += r["q"]
    total_rev = sum(max(r["revenue"], 0) for r in rows) or 1
    rows.sort(key=lambda r: -r["revenue"])
    acc = 0
    for r in rows:
        if r["revenue"] > 0:
            acc += r["revenue"]
            cum = acc / total_rev * 100
            r["abc"] = "A" if cum <= 80 or acc == r["revenue"] else ("B" if cum <= 95 else "C")
        else:
            r["abc"] = "C"
        sr = series.get(r["id"])
        if sr and sum(sr) > 0 and nb > 1:
            mean = sum(sr) / nb
            cv = math.sqrt(sum((x - mean) ** 2 for x in sr) / nb) / mean * 100
            r["cv"] = round(cv, 1)
            r["xyz"] = "X" if cv <= 10 else ("Y" if cv <= 25 else "Z")
        else:
            r["cv"] = None
            r["xyz"] = "Z" if r["qty"] > 0 and nb > 1 else "-"
        per_day = r["qty"] / days
        r["per_day"] = round(per_day, 2)
        r["cover_days"] = round(r["stock"] / per_day, 1) if per_day > 0 and r["stock"] > 0 else None
        r["stock_value"] = round(max(r["stock"], 0) * (r["cost_price"] or 0), 2)
        r["margin"] = round(r["profit"] / r["revenue"] * 100, 1) if r["revenue"] else None
        r["frozen"] = r["stock"] > 0 and (r["qty"] <= 0 or (r["cover_days"] or 0) > 90)
    matrix = {}
    for r in rows:
        if r["xyz"] in ("X", "Y", "Z"):
            k = r["abc"] + r["xyz"]
            m = matrix.setdefault(k, {"n": 0, "revenue": 0})
            m["n"] += 1
            m["revenue"] += r["revenue"]
    frozen = [r for r in rows if r["frozen"]]
    return {"items": rows, "matrix": matrix, "days": days, "bucket": "неделя" if bucket == 7 else "день",
            "frozen_sum": round(sum(r["stock_value"] for r in frozen), 2), "frozen_n": len(frozen),
            "stock_value": round(sum(r["stock_value"] for r in rows), 2)}


@bp.get("/api/rep/products")
def rep_products():
    if not _admin():
        return M.err("Доступ только для администратора", 403)
    return jsonify(products_report(*_period()))


# ================================================================ оборотная ведомость
IN_TYPES = ("purchase", "receive", "refund")
OUT_TYPES = ("sale", "writeoff", "transfer_out", "supplier_return")


def movement_report(f, t):
    a0, a1 = f + " 00:00:00", t + " 23:59:59"
    prods = {r["id"]: dict(r, opening=0, closing=0, **{k: 0 for k in IN_TYPES + OUT_TYPES + ("inv_plus", "inv_minus")})
             for r in M.q("""SELECT p.id, p.name, p.barcode, p.stock, p.cost_price, p.unit_name, COALESCE(c.name,'') AS category
                             FROM products p LEFT JOIN categories c ON c.id=p.category_id WHERE p.active=1""")}
    after = {r["product_id"]: r["q"] for r in M.q(
        "SELECT product_id, SUM(qty) q FROM stock_moves WHERE created_at>? AND COALESCE(note,'') NOT LIKE ? GROUP BY product_id",
        (a1, HIST_NOTE))}
    for r in M.q("""SELECT product_id, type, SUM(qty) q FROM stock_moves WHERE created_at BETWEEN ? AND ?
                    AND COALESCE(note,'') NOT LIKE ? GROUP BY product_id, type""", (a0, a1, HIST_NOTE)):
        p = prods.get(r["product_id"])
        if not p:
            continue
        if r["type"] == "inventory":
            if r["q"] >= 0:
                p["inv_plus"] += r["q"]
            else:
                p["inv_minus"] += -r["q"]
        elif r["type"] in IN_TYPES:
            p[r["type"]] += r["q"]
        elif r["type"] in OUT_TYPES:
            p[r["type"]] += -r["q"]
    out = []
    for p in prods.values():
        p["closing"] = round(p["stock"] - after.get(p["id"], 0), 3)
        inc = p["purchase"] + p["receive"] + p["refund"] + p["inv_plus"]
        outq = p["sale"] + p["writeoff"] + p["transfer_out"] + p["supplier_return"] + p["inv_minus"]
        p["opening"] = round(p["closing"] - inc + outq, 3)
        p["in"], p["out"] = round(inc, 3), round(outq, 3)
        if p["opening"] or inc or outq or p["closing"]:
            out.append(p)
    out.sort(key=lambda x: x["name"])
    tot = {k: round(sum(x[k] * (x["cost_price"] or 0) for x in out), 2) for k in ("opening", "in", "out", "closing")}
    return {"items": out, "totals_cost": tot}


@bp.get("/api/rep/movement")
def rep_movement():
    if not _admin():
        return M.err("Доступ только для администратора", 403)
    return jsonify(movement_report(*_period()))


# ================================================================ прибыль и расходы
def profit_report(f, t):
    a = (f + " 00:00:00", t + " 23:59:59")
    tt = totals(f, t)
    exp = M.q("""SELECT COALESCE(NULLIF(category,''),'Прочее') AS cat, SUM(amount) amount FROM cash_ops
                 WHERE type='expense' AND COALESCE(category,'')<>'Поставщику' AND created_at BETWEEN ? AND ? GROUP BY cat ORDER BY amount DESC""", a)
    wo = M.q("""SELECT COALESCE(SUM(-m.qty*p.cost_price),0) v FROM stock_moves m JOIN products p ON p.id=m.product_id
                WHERE m.type='writeoff' AND m.created_at BETWEEN ? AND ?""", a, one=True)["v"]
    inv = M.q("""SELECT COALESCE(SUM(m.qty*p.cost_price),0) v FROM stock_moves m JOIN products p ON p.id=m.product_id
                 WHERE m.type='inventory' AND COALESCE(m.note,'') NOT LIKE 'Перенос%' AND COALESCE(m.note,'') NOT LIKE 'Начальный%'
                 AND COALESCE(m.note,'')<>'Импорт' AND m.created_at BETWEEN ? AND ?""", a, one=True)["v"]
    expenses = round(sum(e["amount"] for e in exp), 2)
    net_profit = round(tt["profit"] - expenses - wo + inv, 2)
    by_day = M.q("""SELECT substr(s.created_at,1,10) d,
                    SUM(CASE WHEN s.operation='sale' THEN si.total-si.qty*si.cost_price ELSE -(si.total-si.qty*si.cost_price) END) gross
                    FROM sale_items si JOIN sales s ON s.id=si.sale_id WHERE s.created_at BETWEEN ? AND ? GROUP BY d ORDER BY d""", a)
    exp_day = {r["d"]: r["v"] for r in M.q("""SELECT substr(created_at,1,10) d, SUM(amount) v FROM cash_ops WHERE type='expense'
                 AND COALESCE(category,'')<>'Поставщику' AND created_at BETWEEN ? AND ? GROUP BY d""", a)}
    for r in by_day:
        r["expenses"] = exp_day.get(r["d"], 0)
        r["net"] = round(r["gross"] - r["expenses"], 2)
    return {"revenue": tt["net"], "cost": tt["cost"], "gross": tt["profit"], "margin": tt["margin"], "expenses": exp,
            "expenses_total": expenses, "writeoff": round(wo, 2), "inventory_diff": round(inv, 2), "net_profit": net_profit,
            "net_margin": round(net_profit / tt["net"] * 100, 1) if tt["net"] else 0, "by_day": by_day}


@bp.get("/api/rep/profit")
def rep_profit():
    if not _admin():
        return M.err("Доступ только для администратора", 403)
    return jsonify(profit_report(*_period()))


# ================================================================ сотрудники
def staff_report(f, t):
    a = (f + " 00:00:00", t + " 23:59:59")
    rows = M.q("""SELECT u.id, u.name, u.role,
        COUNT(CASE WHEN s.operation='sale' THEN 1 END) checks,
        COALESCE(SUM(CASE WHEN s.operation='sale' THEN s.total ELSE -s.total END),0) net,
        COALESCE(SUM(CASE WHEN s.operation='sale' THEN s.discount END),0) discount,
        COUNT(CASE WHEN s.operation='refund' THEN 1 END) refunds,
        COALESCE(SUM(CASE WHEN s.operation='refund' THEN s.total END),0) refunds_sum
        FROM users u LEFT JOIN sales s ON s.user_id=u.id AND s.created_at BETWEEN ? AND ?
        GROUP BY u.id ORDER BY net DESC""", a)
    for r in rows:
        au = M.q("""SELECT
            COUNT(CASE WHEN action='Удалена строка из чека' THEN 1 END) deleted,
            COUNT(CASE WHEN action='Чек очищен без оплаты' THEN 1 END) cleared,
            COUNT(CASE WHEN action LIKE 'Расхождение кассы%' THEN 1 END) cash_diff
            FROM audit WHERE user_id=? AND created_at BETWEEN ? AND ?""", (r["id"],) + a, one=True)
        r.update(au)
        hours = 0.0
        for sh in M.q("SELECT opened_at, closed_at FROM shifts WHERE user_id=? AND opened_at BETWEEN ? AND ?", (r["id"],) + a):
            o = datetime.strptime(sh["opened_at"], "%Y-%m-%d %H:%M:%S")
            c = datetime.strptime(sh["closed_at"], "%Y-%m-%d %H:%M:%S") if sh["closed_at"] else datetime.now()
            hours += (c - o).total_seconds() / 3600
        r["hours"] = round(hours, 1)
        r["per_hour"] = round(r["net"] / hours) if hours >= 0.5 else None
        r["avg"] = round(r["net"] / r["checks"]) if r["checks"] else 0
        r["discount_pct"] = round(r["discount"] / (r["net"] + r["discount"]) * 100, 1) if r["net"] + r["discount"] > 0 else 0
        risk = r["deleted"] + r["cleared"] * 2 + r["refunds"] + r["cash_diff"] * 3
        r["risk"] = "high" if risk >= 10 else ("mid" if risk >= 4 else "low")
    return {"items": [r for r in rows if r["checks"] or r["refunds"] or r["hours"]]}


@bp.get("/api/rep/staff")
def rep_staff():
    if not _admin():
        return M.err("Доступ только для администратора", 403)
    return jsonify(staff_report(*_period()))


# ================================================================ НДС
def vat_report(f, t):
    a = (f + " 00:00:00", t + " 23:59:59")
    rows = M.q("""SELECT COALESCE(si.vat_percent,0) rate,
                  SUM(CASE WHEN s.operation='sale' THEN si.total ELSE -si.total END) gross
                  FROM sale_items si JOIN sales s ON s.id=si.sale_id WHERE s.created_at BETWEEN ? AND ? GROUP BY rate ORDER BY rate""", a)
    for r in rows:
        r["vat"] = round(r["gross"] * r["rate"] / (100 + r["rate"]), 2) if r["rate"] else 0
        r["net"] = round(r["gross"] - r["vat"], 2)
    return {"items": rows, "vat_total": round(sum(r["vat"] for r in rows), 2), "gross_total": round(sum(r["gross"] for r in rows), 2)}


@bp.get("/api/rep/vat")
def rep_vat():
    if not _admin():
        return M.err("Доступ только для администратора", 403)
    return jsonify(vat_report(*_period()))


# ================================================================ Excel
def _sheet(ws, title, headers, rows, widths=None):
    from openpyxl.styles import Font, PatternFill, Alignment
    ws.title = title[:31]
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="0F766E")
        c.alignment = Alignment(vertical="center", wrap_text=True)
    for r in rows:
        ws.append(r)
    for i, h in enumerate(headers, 1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = (widths or {}).get(i, max(12, min(45, len(str(h)) + 4)))
    for row in ws.iter_rows(min_row=2):
        for c in row:
            if isinstance(c.value, float):
                c.number_format = "#,##0.##"
            elif isinstance(c.value, int):
                c.number_format = "#,##0"
    ws.freeze_panes = "A2"


@bp.get("/api/rep/export/<kind>")
def rep_export(kind):
    if not _admin():
        return M.err("Доступ только для администратора", 403)
    import openpyxl
    f, t = _period()
    wb = openpyxl.Workbook()
    ws = wb.active
    store = db.get_settings().get("store_name") or "Магазин"
    if kind == "sales":
        r = sales_report(f, t)
        _sheet(ws, "По дням", ["Дата", "Выручка", "Чеков"], [[x["d"], x["net"], x["checks"]] for x in r["by_day"]])
        ws2 = wb.create_sheet()
        _sheet(ws2, "По категориям", ["Категория", "Выручка", "Прибыль"], [[x["cat"], x["net"], x["profit"]] for x in r["categories"]], {1: 30})
        ws3 = wb.create_sheet()
        _sheet(ws3, "День недели × час", ["День"] + [f"{h}:00" for h in range(24)],
               [[WD[i]] + [round(v) for v in r["heat"][i]] for i in range(7)])
        ws4 = wb.create_sheet()
        p = r["pay"]
        _sheet(ws4, "Оплаты", ["Способ", "Сумма"], [["Наличные", p["cash"]], ["Карта", p["card"]], ["QR", p["qr"]],
                                                   ["Насия", p["debt"]], ["Бонусы", p["bonus"]]])
    elif kind == "products":
        r = products_report(f, t)
        _sheet(ws, "ABC-XYZ", ["Товар", "Штрихкод", "Категория", "Продано", "Выручка", "Прибыль", "Маржа %", "ABC", "XYZ",
                               "CV %", "Остаток", "Хватит на, дней", "Остаток по себест."],
               [[x["name"], x["barcode"] or "", x["category"], x["qty"], x["revenue"], x["profit"], x["margin"], x["abc"], x["xyz"],
                 x["cv"], x["stock"], x["cover_days"], x["stock_value"]] for x in r["items"]], {1: 40, 2: 16, 3: 18})
    elif kind == "movement":
        r = movement_report(f, t)
        _sheet(ws, "Оборотная ведомость", ["Товар", "Штрихкод", "Остаток на начало", "Приход", "Приёмка", "Возвраты",
                                            "Излишки", "Продажи", "Списания", "Отгрузки", "Возврат поставщику", "Недостачи", "Остаток на конец",
                                            "Себестоимость"],
               [[x["name"], x["barcode"] or "", x["opening"], x["purchase"], x["receive"], x["refund"], x["inv_plus"], x["sale"],
                 x["writeoff"], x["transfer_out"], x["supplier_return"], x["inv_minus"], x["closing"], x["cost_price"]] for x in r["items"]], {1: 40, 2: 16})
    elif kind == "profit":
        r = profit_report(f, t)
        rows = [["Выручка", r["revenue"]], ["Себестоимость проданного", -r["cost"]], ["Валовая прибыль", r["gross"]]]
        rows += [[f"Расход: {e['cat']}", -e["amount"]] for e in r["expenses"]]
        rows += [["Списания (по себестоимости)", -r["writeoff"]], ["Результат ревизий", r["inventory_diff"]],
                 ["Чистая прибыль", r["net_profit"]]]
        _sheet(ws, "Прибыль", ["Статья", "Сумма"], rows, {1: 40, 2: 18})
    elif kind == "staff":
        r = staff_report(f, t)
        _sheet(ws, "Сотрудники", ["Сотрудник", "Часов", "Чеков", "Выручка", "Средний чек", "Выручка в час", "Скидки", "Скидки %",
                                  "Возвратов", "Сумма возвратов", "Удалено строк", "Очищено чеков", "Расхождений кассы"],
               [[x["name"], x["hours"], x["checks"], x["net"], x["avg"], x["per_hour"], x["discount"], x["discount_pct"],
                 x["refunds"], x["refunds_sum"], x["deleted"], x["cleared"], x["cash_diff"]] for x in r["items"]], {1: 24})
    elif kind == "vat":
        r = vat_report(f, t)
        _sheet(ws, "НДС", ["Ставка НДС %", "Продажи с НДС", "В т.ч. НДС", "Без НДС"],
               [[x["rate"], x["gross"], x["vat"], x["net"]] for x in r["items"]] + [["Итого", r["gross_total"], r["vat_total"], r["gross_total"] - r["vat_total"]]])
    else:
        return M.err("Неизвестный отчёт", 404)
    buf = io.BytesIO()
    wb.save(buf)
    fn = f"{kind}_{f}_{t}.xlsx"
    return Response(buf.getvalue(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename={fn}"})


def register(app_module):
    global M
    M = app_module
    M.app.register_blueprint(bp)
