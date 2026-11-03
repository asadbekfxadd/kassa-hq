"""
Магазины: план продаж по месяцам, карточка филиала (показатели, выполнение плана, прогноз,
рентабельность, история 12 месяцев, сотрудники, журнал), сравнение филиалов за период.
"""
import calendar
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request

bp = Blueprint("stores2", __name__)
H = None
F = "%Y-%m-%d %H:%M:%S"
SGN = "(CASE WHEN s.operation='sale' THEN 1 ELSE -1 END)"
MONTHS = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def metrics(store_ids, a0, a1):
    """Выручка, чеки, прибыль, кол-во товаров по магазинам за период [a0; a1] (строки дат)."""
    if not store_ids:
        return {}
    ph = ",".join("?" * len(store_ids))
    out = {i: {"net": 0, "checks": 0, "profit": 0, "items": 0, "refunds": 0, "discount": 0} for i in store_ids}
    for r in H.q(f"""SELECT s.store_id, COALESCE(SUM({SGN}*s.total),0) net, COUNT(CASE WHEN s.operation='sale' THEN 1 END) checks,
                     COALESCE(SUM(CASE WHEN s.operation='refund' THEN s.total ELSE 0 END),0) refunds,
                     COALESCE(SUM(CASE WHEN s.operation='sale' THEN s.discount ELSE 0 END),0) discount
                     FROM sales s WHERE s.store_id IN ({ph}) AND s.created_at BETWEEN ? AND ? GROUP BY s.store_id""",
                 tuple(store_ids) + (a0, a1)):
        out[r["store_id"]].update(net=r["net"] or 0, checks=r["checks"] or 0, refunds=r["refunds"] or 0, discount=r["discount"] or 0)
    for r in H.q(f"""SELECT s.store_id, COALESCE(SUM({SGN}*(s.total-COALESCE(s.cost_total,0))),0) profit,
                     COALESCE(SUM({SGN}*COALESCE(s.units,0)),0) items FROM sales s
                     WHERE s.store_id IN ({ph}) AND s.created_at BETWEEN ? AND ? GROUP BY s.store_id""", tuple(store_ids) + (a0, a1)):
        out[r["store_id"]].update(profit=r["profit"] or 0, items=r["items"] or 0)
    for m in out.values():
        m["avg"] = round(m["net"] / m["checks"]) if m["checks"] else 0
        m["margin"] = round(m["profit"] / m["net"] * 100, 1) if m["net"] else 0
        m["items_per_check"] = round(m["items"] / m["checks"], 2) if m["checks"] else 0
    return out


def month_bounds(ym):
    y, m = map(int, ym.split("-"))
    last = calendar.monthrange(y, m)[1]
    return f"{ym}-01 00:00:00", f"{ym}-{last:02d} 23:59:59", last


def plan_progress(sid, ym, now):
    a0, a1, last = month_bounds(ym)
    plan = (H.q("SELECT amount FROM store_plans WHERE store_id=? AND month=?", (sid, ym), one=True) or {}).get("amount") or 0
    fact = metrics([sid], a0, a1)[sid]["net"]
    cur = now.strftime("%Y-%m") == ym
    day = now.day if cur else last
    forecast = round(fact / day * last) if cur and day else fact
    return {"month": ym, "plan": plan, "fact": fact, "pct": round(fact / plan * 100, 1) if plan else None,
            "forecast": forecast, "forecast_pct": round(forecast / plan * 100, 1) if plan else None,
            "need_per_day": round(max(0, plan - fact) / max(1, last - day)) if cur and plan and last > day else 0,
            "days_left": last - day if cur else 0}


def _fin(d):
    if not H.rbac.can("finance"):
        for k in ("profit", "margin"):
            if k in d:
                d[k] = None
    return d


@bp.get("/api/stores/<int:sid>/card")
def store_card(sid):
    if not H.rbac.store_allowed(sid):
        return H.err("Нет доступа к этому магазину", 403)
    st = H.q("""SELECT s.*, u.name AS manager_name, u.phone AS manager_phone FROM stores s LEFT JOIN users u ON u.id=s.manager_id
                WHERE s.id=?""", (sid,), one=True)
    if not st:
        return H.err("Магазин не найден", 404)
    st.pop("token", None)
    now = H.tnow()
    ym = now.strftime("%Y-%m")
    d0 = now.strftime("%Y-%m-%d 00:00:00")
    today = metrics([sid], d0, now.strftime(F))[sid]
    a0, _, _ = month_bounds(ym)
    month = metrics([sid], a0, now.strftime(F))[sid]
    stock = H.q("""SELECT COALESCE(SUM(CASE WHEN st.qty>0 THEN st.qty END),0) qty, COALESCE(SUM(CASE WHEN st.qty>0 THEN st.qty*p.cost_price END),0) value,
                   COALESCE(SUM(CASE WHEN st.qty>0 THEN st.qty*p.price END),0) retail, COUNT(CASE WHEN st.qty>0 THEN 1 END) skus,
                   COUNT(CASE WHEN st.qty<=0 THEN 1 END) zero
                   FROM stock st JOIN products p ON p.uid=st.product_uid WHERE st.store_id=?""", (sid,), one=True)
    # история 12 месяцев
    hist = []
    y, m = now.year, now.month
    for _ in range(12):
        k = f"{y}-{m:02d}"
        b0, b1, _ = month_bounds(k)
        mm = metrics([sid], b0, b1)[sid]
        pl = (H.q("SELECT amount FROM store_plans WHERE store_id=? AND month=?", (sid, k), one=True) or {}).get("amount") or 0
        hist.append(_fin({"month": k, "label": f"{MONTHS[m - 1]} {str(y)[2:]}", "net": mm["net"], "profit": mm["profit"],
                          "margin": mm["margin"], "checks": mm["checks"], "avg": mm["avg"], "plan": pl,
                          "pct": round(mm["net"] / pl * 100, 1) if pl else None}))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    hist.reverse()
    staff = H.q("""SELECT u.id, u.name, u.role, u.position, u.phone, u.last_login FROM users u JOIN user_stores us ON us.user_id=u.id
                   WHERE us.store_id=? AND u.active=1 ORDER BY u.name""", (sid,))
    for u in staff:
        u["role_title"] = H.rbac.ROLES.get(u["role"], (u["role"],))[0]
    plan = plan_progress(sid, ym, now)
    # рентабельность: валовая прибыль / выручка и окупаемость запаса (прибыль за 30 дней / товарный запас)
    p30 = metrics([sid], (now - timedelta(days=30)).strftime(F), now.strftime(F))[sid]
    roi = round(p30["profit"] / stock["value"] * 100, 1) if stock["value"] else None
    online = bool(st["last_seen"]) and (now - datetime.strptime(st["last_seen"], F)).total_seconds() < 180
    log = H.q("SELECT created_at, user_name, action, details FROM audit WHERE store_id=? ORDER BY id DESC LIMIT 30", (sid,)) \
        if H.rbac.can("audit") or H.rbac.can("stores.edit") else []
    return jsonify(store={**st, "online": online}, today=_fin(today), month=_fin(month), stock=stock, history=hist, staff=staff,
                   plan=plan, rentability=_fin({"margin": month["margin"], "profit": month["profit"], "stock_roi_30": roi,
                                                "revenue_per_m2": round(month["net"] / st["area"]) if st.get("area") else None}),
                   log=log, finance=H.rbac.can("finance"), can_edit=H.rbac.can("stores.edit"), can_plan=H.rbac.can("plans.edit"))


@bp.post("/api/stores/<int:sid>/plan")
def store_plan(sid):
    if not H.rbac.store_allowed(sid):
        return H.err("Нет доступа к этому магазину", 403)
    d = request.json or {}
    rows = d.get("plans") or [{"month": d.get("month"), "amount": d.get("amount")}]
    c = H.con()
    for r in rows:
        ym = str(r.get("month") or "")
        try:
            datetime.strptime(ym, "%Y-%m")
        except ValueError:
            c.close()
            return H.err("Месяц в формате ГГГГ-ММ")
        amt = H.num(r.get("amount"))
        if amt < 0:
            c.close()
            return H.err("План не может быть отрицательным")
        c.execute("DELETE FROM store_plans WHERE store_id=? AND month=?", (sid, ym))
        c.execute("INSERT INTO store_plans(store_id,month,amount,updated_at) VALUES(?,?,?,?)", (sid, ym, amt, H.now()))
        H.rbac.audit("План продаж", f"{ym}: {amt:,.0f}".replace(",", " "), sid, c=c)
    c.commit()
    c.close()
    return jsonify(ok=True)


@bp.get("/api/stores/compare")
def stores_compare():
    now = H.tnow()
    f = request.args.get("from") or now.strftime("%Y-%m-01")
    t = request.args.get("to") or now.strftime("%Y-%m-%d")
    fi, fia = H.rbac.sf("id")
    stores = H.q(f"SELECT id, name, area, registers FROM stores WHERE active=1{fi} ORDER BY id", fia)
    ids = [s["id"] for s in stores]
    cur = metrics(ids, f + " 00:00:00", t + " 23:59:59")
    days = (datetime.strptime(t, "%Y-%m-%d") - datetime.strptime(f, "%Y-%m-%d")).days + 1
    pf = (datetime.strptime(f, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
    pt = (datetime.strptime(f, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    prev = metrics(ids, pf + " 00:00:00", pt + " 23:59:59")
    stock = {r["store_id"]: r["v"] for r in H.q(f"""SELECT st.store_id, COALESCE(SUM(CASE WHEN st.qty>0 THEN st.qty*p.cost_price END),0) v
                FROM stock st JOIN products p ON p.uid=st.product_uid WHERE 1=1{H.rbac.sf('st.store_id')[0]} GROUP BY st.store_id""",
                                                H.rbac.sf("st.store_id")[1])}
    # план за месяцы, попадающие в период (пропорционально дням)
    plans = {}
    for r in H.q(f"SELECT store_id, month, amount FROM store_plans WHERE 1=1{H.rbac.sf('store_id')[0]}", H.rbac.sf("store_id")[1]):
        a0, a1, last = month_bounds(r["month"])
        lo, hi = max(f, a0[:10]), min(t, a1[:10])
        if lo <= hi:
            ov = (datetime.strptime(hi, "%Y-%m-%d") - datetime.strptime(lo, "%Y-%m-%d")).days + 1
            plans[r["store_id"]] = plans.get(r["store_id"], 0) + (r["amount"] or 0) * ov / last
    total = sum(c["net"] for c in cur.values()) or 1
    out = []
    for s in stores:
        c, p = cur[s["id"]], prev[s["id"]]
        pl = round(plans.get(s["id"], 0))
        out.append(_fin({**s, **c, "prev": p["net"], "chg": round((c["net"] - p["net"]) / abs(p["net"]) * 100, 1) if p["net"] else None,
                         "share": round(c["net"] / total * 100, 1), "plan": pl, "plan_pct": round(c["net"] / pl * 100, 1) if pl else None,
                         "stock_value": stock.get(s["id"], 0), "per_day": round(c["net"] / days),
                         "per_m2": round(c["net"] / s["area"]) if s.get("area") else None}))
    out.sort(key=lambda x: -x["net"])
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return jsonify(items=out, period=[f, t], prev_period=[pf, pt], days=days, finance=H.rbac.can("finance"))


def register(hq_module):
    global H
    H = hq_module
    H.app.register_blueprint(bp)
