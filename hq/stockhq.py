"""
Центр: статусы остатков по сети — Товар | Артикул | Магазин | Остаток | Продажи | Статус
(В наличии / Мало / Нет / Избыток / Без продаж / Зона риска), фильтры и выгрузка в Excel.
"""
import io
from datetime import timedelta

from flask import Blueprint, Response, jsonify, request

bp = Blueprint("stockhq", __name__)
H = None
STATUS = {"out": "Нет в наличии", "low": "Мало", "risk": "Зона риска", "nosale": "Без продаж", "over": "Избыток", "in": "В наличии"}


def status_of(stock, min_stock, sold30, over_days=60, risk_days=3):
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
    if cover is not None and cover > over_days:
        return "over", cover
    return "in", cover


def build():
    now = H.tnow()
    fi, fia = H.rbac.sf("id")
    stores = {s["id"]: s["name"] for s in H.q(f"SELECT id, name FROM stores WHERE active=1{fi} ORDER BY id", fia)}
    sid = request.args.get("store")
    if sid:
        if not H.rbac.store_allowed(sid):
            return None, "Нет доступа к магазину"
        stores = {k: v for k, v in stores.items() if k == int(sid)}
    if not stores:
        return {"items": [], "counts": {k: 0 for k in STATUS}, "titles": STATUS, "stores": []}, None
    ph = ",".join("?" * len(stores))
    ids = tuple(stores)
    since = {d: (now - timedelta(days=d)).strftime("%Y-%m-%d %H:%M:%S") for d in (7, 30)}
    sold = {(sid, pu): (v[2], v[0]) for (sid, pu), v in H.salescache.sold().items() if sid in stores}
    t = (request.args.get("q") or "").strip().lower()
    cat = request.args.get("category") or ""
    brand = request.args.get("brand") or ""
    fin = H.rbac.can("finance")
    prods = H.q("SELECT uid, name, barcode, sku, category, brand, min_stock, cost_price, price, unit_name FROM products WHERE active=1")
    stock = {k: v for k, v in H.salescache.stock().items() if k[0] in stores}
    items, counts = [], {k: 0 for k in STATUS}
    for p in prods:
        if t and t not in (p["name"] or "").lower() and t not in (p["barcode"] or "") and t not in (p["sku"] or "").lower():
            continue
        if cat and (p["category"] or "") != cat:
            continue
        if brand and (p["brand"] or "") != brand:
            continue
        for s_id, s_name in stores.items():
            qty = stock.get((s_id, p["uid"]))
            q30, q7 = sold.get((s_id, p["uid"]), (0, 0))
            if qty is None and not q30:
                continue          # товара в этом магазине никогда не было
            qty = qty or 0
            st, cover = status_of(qty, p["min_stock"], q30)
            counts[st] += 1
            items.append({"uid": p["uid"], "name": p["name"], "barcode": p["barcode"], "sku": p["sku"], "category": p["category"],
                          "brand": p["brand"], "store_id": s_id, "store": s_name, "stock": qty, "sold7": round(q7, 3), "sold30": round(q30, 3),
                          "per_day": round(q30 / 30, 2), "cover_days": round(cover, 1) if cover else None, "status": st,
                          "value": round(max(qty, 0) * (p["cost_price"] or 0)) if fin else None, "unit": p["unit_name"]})
    want = request.args.get("status")
    if want:
        items = [i for i in items if i["status"] == want]
    order = list(STATUS)
    items.sort(key=lambda i: (order.index(i["status"]), i["name"], i["store"]))
    cats = sorted({p["category"] for p in prods if p["category"]})
    brands = sorted({p["brand"] for p in prods if p["brand"]})
    return {"items": items[:3000], "total": len(items), "counts": counts, "titles": STATUS, "categories": cats, "brands": brands,
            "stores": [{"id": k, "name": v} for k, v in stores.items()], "finance": fin}, None


@bp.get("/api/stock/status")
def stock_status():
    r, e = build()
    return H.err(e, 403) if e else jsonify(r)


@bp.get("/api/stock/status.xlsx")
def stock_status_xlsx():
    r, e = build()
    if e:
        return H.err(e, 403)
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Статусы остатков"
    hdr = ["Товар", "Штрихкод", "Артикул", "Бренд", "Категория", "Магазин", "Остаток", "Продажи 7 дн.", "Продажи 30 дн.", "В день",
           "Хватит, дней", "Статус"] + (["Остаток по закупке"] if r["finance"] else [])
    ws.append(hdr)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1D4ED8")
    for i in r["items"]:
        ws.append([i["name"], i["barcode"] or "", i["sku"] or "", i["brand"] or "", i["category"] or "", i["store"], i["stock"], i["sold7"],
                   i["sold30"], i["per_day"], i["cover_days"], STATUS[i["status"]]] + ([i["value"]] if r["finance"] else []))
    for col, w in zip("ABCDEFGHIJKLM", (40, 16, 14, 16, 18, 20, 10, 12, 12, 10, 12, 16, 16)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return Response(buf.getvalue(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=stock_status.xlsx"})


def register(hq_module):
    global H
    H = hq_module
    H.app.register_blueprint(bp)
