"""
Центр (этап 8): AI Retail Assistant.

Отвечает на вопросы по РЕАЛЬНЫМ данным сети с учётом прав пользователя (его магазины, право «Прибыль»):
  • какой магазин сегодня продаёт лучше;      • какие товары переместить;
  • какие товары не продаются;                • где заканчиваются ходовые товары (размеров в продуктах нет — честно говорим);
  • какие филиалы не выполняют план;          • что заказать;
  • где избыток товара;                       • как изменилась прибыль за месяц;
  • сводка дня (всё самое важное сразу).
Каждый ответ — расчёт по базе (цифры, таблица, ссылка на раздел). Если подключён Claude (Интеграции),
свободные вопросы и «объясни подробнее» формулирует модель — но только по цифрам, которые посчитала система.
"""
import calendar
import json
import math
import re
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request

bp = Blueprint("assistant", __name__)
H = None
F = "%Y-%m-%d %H:%M:%S"
SGN = "(CASE WHEN s.operation='sale' THEN 1 ELSE -1 END)"


def fmt(v):
    return f"{round(v or 0):,}".replace(",", " ")


def pct(a, b):
    return round((a - b) / abs(b) * 100, 1) if b else None


def chg(p):
    return "" if p is None else (f"▲ {p:g}%" if p >= 0 else f"▼ {abs(p):g}%")


def ans(intent, title, text, cols=None, rows=None, view=None, data=None, tips=None):
    return {"intent": intent, "title": title, "text": text, "table": {"cols": cols, "rows": rows} if cols else None,
            "view": view, "data": data or {}, "tips": tips or [], "source": "rules"}


def need(perm):
    if not H.rbac.can(perm):
        raise PermissionError(f"Для этого ответа нужно право «{H.rbac.PERMS[perm]}»")


def stores():
    fi, fia = H.rbac.sf("id")
    return {s["id"]: s["name"] for s in H.q(f"SELECT id, name FROM stores WHERE active=1{fi} ORDER BY id", fia)}


# ---------------------------------------------------------------- ответы
def best_store():
    now = H.tnow()
    d0 = now.strftime("%Y-%m-%d") + " 00:00:00"
    w0, w1 = (now - timedelta(days=7)).strftime("%Y-%m-%d") + " 00:00:00", (now - timedelta(days=7)).strftime(F)
    fs, fa = H.rbac.sf("s.store_id")
    q = f"""SELECT s.store_id, COALESCE(SUM({SGN}*s.total),0) net, COUNT(CASE WHEN s.operation='sale' THEN 1 END) checks
            FROM sales s WHERE s.created_at BETWEEN ? AND ?{fs} GROUP BY s.store_id"""
    today = {r["store_id"]: r for r in H.q(q, (d0, now.strftime(F)) + tuple(fa))}
    week = {r["store_id"]: r for r in H.q(q, (w0, w1) + tuple(fa))}
    st = stores()
    rows = []
    for sid, name in st.items():
        t, w = today.get(sid, {}), week.get(sid, {})
        net, ch = t.get("net", 0) or 0, t.get("checks", 0) or 0
        rows.append({"name": name, "net": net, "checks": ch, "avg": round(net / ch) if ch else 0, "prev": w.get("net", 0) or 0, "chg": pct(net, w.get("net", 0) or 0)})
    rows.sort(key=lambda r: -r["net"])
    total = sum(r["net"] for r in rows)
    if not rows or not total:
        return ans("best_store", "Продажи сегодня", "Сегодня продаж ещё нет" + (" — кассы ещё не прислали данные." if rows else "."), view="dash")
    b = rows[0]
    text = (f"Лучше всех сегодня продаёт **{b['name']}**: {fmt(b['net'])} сум, {b['checks']} чеков, средний чек {fmt(b['avg'])}"
            + (f" ({chg(b['chg'])} к прошлому {['понедельнику','вторнику','среде','четвергу','пятнице','субботе','воскресенью'][now.weekday()]} на это же время)." if b["chg"] is not None else ".")
            + f" Это {round(b['net'] / total * 100)}% выручки сети за день ({fmt(total)}).")
    weak = [r for r in rows if r["chg"] is not None and r["chg"] < -20]
    tips = [f"«{r['name']}» отстаёт от прошлой недели на {abs(r['chg']):g}% — проверьте кассу и выкладку" for r in weak[:3]]
    return ans("best_store", "Какой магазин сегодня продаёт лучше", text, ["Магазин", "Выручка", "Чеков", "Ср. чек", "К прошлой неделе"],
               [[r["name"], fmt(r["net"]), r["checks"], fmt(r["avg"]), chg(r["chg"]) or "—"] for r in rows], "dash", {"stores": rows}, tips)


def transfers():
    need("docs.view")
    items, _ = H.transfers.recommend(window=30, target=14, keep=7)
    if not items:
        return ans("transfer", "Что переместить", "Перемещать сейчас нечего: там, где товар продаётся, его хватает минимум на 2 недели, или излишков в других магазинах нет.", view="docs")
    hi = [x for x in items if x["priority"] == "high"]
    val = sum(x["value"] for x in items)
    text = (f"Найдено **{len(items)}** перемещений" + (f", из них {len(hi)} срочных — товара уже нет на полке" if hi else "") +
            (f". Стоимость по закупке — {fmt(val)} сум" if H.rbac.can("finance") else "") +
            ". Расчёт: продажи за 30 дней, цель — запас на 14 дней у получателя, у отправителя остаётся минимум на 7 дней (с учётом товара в пути).")
    return ans("transfer", "Какие товары нужно переместить", text, ["Товар", "Откуда", "Куда", "Кол-во", "Почему"],
               [[x["name"], x["from"], x["to"], f"{x['qty']:g}", x["reason"]] for x in items[:15]], "docs",
               {"items": [{k: x[k] for k in ("name", "from", "to", "qty", "priority")} for x in items[:30]]},
               ["Откройте «Поставки и перемещения → Рекомендации», отметьте нужное и создайте перемещения одной кнопкой"])


def stock_rows():
    """Остатки и продажи 30 дней по магазину × товару (только доступные магазины)."""
    st = stores()
    if not st:
        return [], st
    ids = tuple(st)
    ph = ",".join("?" * len(ids))
    sold = {(sid, pu): v[2] for (sid, pu), v in H.salescache.sold().items() if sid in st and v[4]}
    prods = {p["uid"]: p for p in H.q("SELECT uid, name, category, min_stock, cost_price, unit_name FROM products WHERE active=1")}
    stock = {k: v for k, v in H.salescache.stock().items() if k[0] in st}
    out = []
    for key in set(stock) | set(sold):
        sid, pu = key
        p = prods.get(pu)
        if not p or sid not in st:
            continue
        q, s30 = stock.get(key, 0) or 0, max(0, sold.get(key, 0))
        pd = s30 / 30
        out.append({"store": st[sid], "store_id": sid, "uid": pu, "name": p["name"], "category": p["category"], "stock": q, "sold30": s30,
                    "per_day": pd, "cover": q / pd if pd > 0 and q > 0 else None, "min": p["min_stock"] or 0,
                    "value": max(q, 0) * (p["cost_price"] or 0), "cost": p["cost_price"] or 0})
    return out, st


def not_selling():
    need("stock")
    rows, _ = stock_rows()
    ns = sorted([r for r in rows if r["stock"] > 0 and r["sold30"] <= 0], key=lambda r: -r["value"])
    if not ns:
        return ans("nosale", "Товары без продаж", "Все товары, которые есть в магазинах, продавались за последние 30 дней.", view="stock")
    fin = H.rbac.can("finance")
    val = sum(r["value"] for r in ns)
    text = (f"**{len(ns)}** позиций лежат в магазинах без единой продажи за 30 дней" + (f" — заморожено {fmt(val)} сум по закупке" if fin else "") + ".")
    tips = ["Переместите их туда, где этот товар продаётся (раздел «Рекомендации»)", "Сделайте уценку или акцию «2 по цене 1» на позиции с близким сроком годности",
            "Не заказывайте эти позиции у поставщика, пока остаток не уйдёт"]
    return ans("nosale", "Какие товары не продаются", text, ["Товар", "Магазин", "Остаток"] + (["По закупке"] if fin else []),
               [[r["name"], r["store"], f"{r['stock']:g}"] + ([fmt(r["value"])] if fin else []) for r in ns[:20]], "stock",
               {"count": len(ns), "value": val if fin else None, "top": [{"name": r["name"], "store": r["store"], "stock": r["stock"]} for r in ns[:20]]}, tips)


def running_out():
    need("stock")
    rows, _ = stock_rows()
    hot = sorted([r for r in rows if r["per_day"] > 0 and (r["stock"] <= 0 or (r["cover"] or 99) < 3)], key=lambda r: -r["per_day"])
    pre = "В продуктовой сети размеры не ведутся — показываю ходовые товары, которые заканчиваются. "
    if not hot:
        return ans("running_out", "Где заканчиваются ходовые товары", pre + "Ходовых товаров с запасом меньше 3 дней нет.", view="stock")
    out = [r for r in hot if r["stock"] <= 0]
    text = pre + f"**{len(hot)}** ходовых позиций закончатся меньше чем за 3 дня" + (f", из них {len(out)} уже закончились" if out else "") + "."
    return ans("running_out", "Где заканчиваются популярные товары", text, ["Товар", "Магазин", "Остаток", "Продаётся в день", "Хватит, дней"],
               [[r["name"], r["store"], f"{r['stock']:g}", f"{r['per_day']:.1f}", "нет" if r["stock"] <= 0 else f"{r['cover']:.1f}"] for r in hot[:20]], "stock",
               {"items": [{"name": r["name"], "store": r["store"], "stock": r["stock"], "per_day": round(r["per_day"], 2)} for r in hot[:20]]},
               ["Сначала проверьте перемещение из соседнего магазина — это быстрее поставки", "Дозаказ у поставщика — вопрос «Что заказать»"])


def plans():
    need("stores.view")
    now = H.tnow()
    ym = now.strftime("%Y-%m")
    rows = []
    for sid, name in stores().items():
        p = H.stores2.plan_progress(sid, ym, now)
        rows.append({"name": name, **p})
    with_plan = [r for r in rows if r["plan"]]
    if not with_plan:
        return ans("plan", "Выполнение плана", "Планы на этот месяц не заданы. Задайте план в карточке магазина (Магазины → магазин → План продаж).", view="stores")
    behind = sorted([r for r in with_plan if (r["forecast_pct"] or 0) < 100], key=lambda r: r["forecast_pct"] or 0)
    no_plan = [r["name"] for r in rows if not r["plan"]]
    if behind:
        text = (f"По прогнозу на конец месяца план не выполнят **{len(behind)}** из {len(with_plan)} магазинов: " +
                ", ".join(f"{r['name']} ({r['forecast_pct']:g}%)" for r in behind[:5]) + ".")
    else:
        text = f"Все {len(with_plan)} магазинов с планом идут на выполнение — прогноз от 100%."
    tips = [f"«{r['name']}»: чтобы выполнить план, нужно продавать по {fmt(r['need_per_day'])} сум в день оставшиеся {r['days_left']} дн." for r in behind[:3] if r["need_per_day"]]
    if no_plan:
        tips.append("Без плана: " + ", ".join(no_plan[:5]))
    return ans("plan", "Какие филиалы не выполняют план", text, ["Магазин", "План", "Факт", "Выполнено", "Прогноз", "Нужно в день"],
               [[r["name"], fmt(r["plan"]), fmt(r["fact"]), f"{r['pct']:g}%", f"{r['forecast_pct']:g}%", fmt(r["need_per_day"]) if r["need_per_day"] else "—"]
                for r in sorted(with_plan, key=lambda r: r["forecast_pct"] or 0)], "stores", {"stores": with_plan}, tips)


def reorder():
    need("stock")
    rows, st = stock_rows()
    by = {}
    for r in rows:
        x = by.setdefault(r["uid"], {"name": r["name"], "stock": 0, "per_day": 0, "min": 0, "cost": r["cost"], "stores": 0})
        x["stock"] += max(0, r["stock"])
        x["per_day"] += r["per_day"]
        x["min"] += r["min"]
        x["stores"] += 1
    # товар в пути из поставок (отправлен в магазин, ещё не принят)
    c = H.con()
    transit = H.transfers.in_transit(c, tuple(st)) if st else {}
    c.close()
    for (sid, pu), q in transit.items():
        if pu in by:
            by[pu]["stock"] += q
    out = []
    for pu, x in by.items():
        if x["per_day"] <= 0 and not x["min"]:
            continue
        cover = x["stock"] / x["per_day"] if x["per_day"] > 0 else None
        target = max(math.ceil(x["per_day"] * 14), x["min"])
        q = target - x["stock"]
        if q > 0 and (cover is None or cover < 7 or x["stock"] <= x["min"]):
            out.append({**x, "cover": cover, "qty": math.ceil(q), "sum": math.ceil(q) * (x["cost"] or 0)})
    out.sort(key=lambda r: (r["cover"] if r["cover"] is not None else -1))
    if not out:
        return ans("reorder", "Что заказать", "Заказывать сейчас ничего не нужно: товаров с запасом по сети меньше 7 дней или ниже минимального остатка нет.", view="stock")
    fin = H.rbac.can("finance")
    tot = sum(r["sum"] for r in out)
    text = (f"Нужно заказать **{len(out)}** позиций" + (f" примерно на {fmt(tot)} сум по закупке" if fin else "") +
            ". Количество — до запаса на 14 дней по продажам за 30 дней (с учётом минимального остатка и товара в пути), по всей сети.")
    return ans("reorder", "Какие товары нужно заказать", text, ["Товар", "Остаток в сети", "Продаётся в день", "Хватит, дней", "Заказать"] + (["Сумма"] if fin else []),
               [[r["name"], f"{r['stock']:g}", f"{r['per_day']:.1f}", "—" if r["cover"] is None else f"{r['cover']:.1f}", r["qty"]] + ([fmt(r["sum"])] if fin else [])
                for r in out[:25]], "stock", {"items": [{"name": r["name"], "qty": r["qty"], "cover": r["cover"]} for r in out[:25]]},
               ["Перед заказом проверьте рекомендации перемещений — часть товара может лежать в другом магазине",
                "Оформите поставку в «Поставки и перемещения → + Поставка» — магазин примет её сканером"])


def overstock():
    need("stock")
    rows, _ = stock_rows()
    ov = sorted([r for r in rows if r["cover"] is not None and r["cover"] > 60], key=lambda r: -r["value"])
    if not ov:
        return ans("overstock", "Избыток товара", "Явного избытка нет: ни один продающийся товар не лежит запасом больше чем на 60 дней.", view="stock")
    fin = H.rbac.can("finance")
    by_store = {}
    for r in ov:
        by_store[r["store"]] = by_store.get(r["store"], 0) + (r["value"] if fin else 1)
    top = max(by_store, key=by_store.get)
    text = (f"Избыток (запас больше 60 дней продаж) — **{len(ov)}** позиций" +
            (f" на {fmt(sum(r['value'] for r in ov))} сум. Больше всего — в «{top}» ({fmt(by_store[top])})." if fin else f". Больше всего — в «{top}»."))
    return ans("overstock", "Где наблюдается избыток товара", text, ["Товар", "Магазин", "Остаток", "Продаётся в день", "Хватит, дней"] + (["По закупке"] if fin else []),
               [[r["name"], r["store"], f"{r['stock']:g}", f"{r['per_day']:.1f}", f"{r['cover']:.0f}"] + ([fmt(r["value"])] if fin else []) for r in ov[:20]], "stock",
               {"by_store": by_store, "items": [{"name": r["name"], "store": r["store"], "cover": round(r["cover"])} for r in ov[:20]]},
               ["Переместите излишки в магазины, где товар продаётся быстрее", "Уменьшите следующий заказ этих позиций у поставщика"])


def profit():
    need("finance")
    now = H.tnow()
    d = now.day
    cur0 = now.strftime("%Y-%m-01")
    prev_last = now.replace(day=1) - timedelta(days=1)
    prev0 = prev_last.strftime("%Y-%m-01")
    prev1 = prev_last.replace(day=min(d, prev_last.day)).strftime("%Y-%m-%d")

    def fl(f, t):
        return {"from": f, "to": t, "a0": f + " 00:00:00", "a1": t + " 23:59:59", "store": None, "category": "", "brand": ""}
    a, b = H.analytics.pnl(fl(cur0, now.strftime("%Y-%m-%d")))["total"], H.analytics.pnl(fl(prev0, prev1))["total"]
    rows = [["Выручка", a["revenue"], b["revenue"]], ["Себестоимость", a["cost"], b["cost"]], ["Валовая прибыль", a["gross"], b["gross"]],
            ["Расходы", a["expenses_total"], b["expenses_total"]], ["Чистая прибыль", a["net"], b["net"]]]
    pg, pn = pct(a["gross"], b["gross"]), pct(a["net"], b["net"])
    if b["revenue"]:
        text = (f"С 1 по {d} число: валовая прибыль **{fmt(a['gross'])}** сум ({chg(pg) or '—'} к тем же дням прошлого месяца), "
                f"маржа {a['gross_margin']:g}% (была {b['gross_margin']:g}%). Чистая прибыль после расходов — **{fmt(a['net'])}** сум" + (f" ({chg(pn)})." if pn is not None else "."))
    else:
        text = (f"С 1 по {d} число: валовая прибыль **{fmt(a['gross'])}** сум, маржа {a['gross_margin']:g}%, чистая прибыль после расходов — **{fmt(a['net'])}** сум. "
                "Сравнить не с чем: за те же дни прошлого месяца продаж в системе нет.")
    reasons = []
    if b["revenue"] and a["revenue"] < b["revenue"]:
        reasons.append(f"выручка ниже на {abs(pct(a['revenue'], b['revenue'])):g}%")
    if a["gross_margin"] < b["gross_margin"] - 0.5:
        reasons.append(f"маржа упала на {round(b['gross_margin'] - a['gross_margin'], 1):g} п.п. — проверьте скидки, акции и закупочные цены")
    if b["expenses_total"] and a["expenses_total"] > b["expenses_total"] * 1.1:
        reasons.append(f"расходы выросли на {pct(a['expenses_total'], b['expenses_total']):g}%")
    tips = (["Причины: " + "; ".join(reasons)] if reasons else []) + ["Расходы, не прошедшие через кассу (аренда, оклады), внесите в «Финансы → Расходы» — иначе чистая прибыль завышена"]
    return ans("profit", "Как изменилась прибыль за месяц", text, ["Показатель", f"1–{d} этого месяца", "Тот же период прошлого", "Изменение"],
               [[r[0], fmt(r[1]), fmt(r[2]), chg(pct(r[1], r[2])) or "—"] for r in rows], "fin", {"current": a, "previous": b}, tips)


def brief():
    parts, data = [], {}
    for fn in (best_store, plans, running_out, transfers, profit):
        try:
            r = fn()
            parts.append(f"• {r['text']}")
            data[r["intent"]] = r["data"]
        except PermissionError:
            continue
    try:
        al = H.hqdash.alerts()
        if al:
            parts.append("• Требуют внимания: " + "; ".join(f"{a['title'].lower()} ({a['count']})" for a in al[:5]) + ".")
            data["alerts"] = [{"title": a["title"], "count": a["count"]} for a in al]
    except Exception:
        pass
    return ans("brief", "Сводка по сети", "\n".join(parts) or "Данных пока нет — подключите кассы.", view="dash", data=data)


INTENTS = [
    ("transfer", transfers, ("перемест", "перемещ", "переброс", "перекин", "ko'chir", "kochir")),
    ("nosale", not_selling, ("не прода", "без продаж", "неликвид", "залеж", "sotilmay", "zalej")),
    ("running_out", running_out, ("размер", "заканчив", "законч", "кончает", "дефицит", "o'lcham", "tugay")),
    ("plan", plans, ("план", "reja")),
    ("reorder", reorder, ("заказ", "закуп", "дозаказ", "buyurtma")),
    ("overstock", overstock, ("избыт", "излиш", "переизбыт", "лишн", "ortiqcha")),
    ("profit", profit, ("прибыл", "маржа", "марж", "заработ", "foyda", "daromad")),
    ("best_store", best_store, ("лучше", "лидер", "какой магазин", "продаёт", "продает", "выручк", "eng yaxshi", "sotuv")),
    ("brief", brief, ("сводк", "итог", "как дела", "что важно", "обзор", "xulosa")),
]
SUGGEST = ["Какой магазин сегодня продаёт лучше?", "Какие товары нужно переместить?", "Какие товары не продаются?", "Где заканчиваются популярные товары?",
           "Какие филиалы не выполняют план?", "Какие товары нужно заказать?", "Где наблюдается избыток товара?", "Как изменилась прибыль за месяц?",
           "Сводка по сети"]


def detect(q):
    ql = q.lower().replace("ё", "е")
    for key, fn, words in INTENTS:
        if any(w.replace("ё", "е") in ql for w in words):
            return key, fn
    return None, None


SYSTEM = ("Ты — аналитик сети продуктовых магазинов в Узбекистане (валюта — сум, UZS). Отвечай на языке вопроса (русский или узбекский), "
          "кратко и по делу: 3–8 предложений или короткий список, с конкретными цифрами и действиями. Используй ТОЛЬКО данные из JSON — ничего не выдумывай; "
          "если данных для ответа нет, прямо скажи, каких данных не хватает. Не используй markdown-таблицы.")


@bp.get("/api/ai/info")
def ai_info():
    return jsonify(suggest=SUGGEST, llm=bool(H.integrations.active("claude")))


@bp.post("/api/ai/ask")
def ai_ask():
    d = request.json or {}
    q = (d.get("q") or "").strip()[:500]
    if not q:
        return H.err("Задайте вопрос")
    key, fn = detect(q)
    cfg = H.integrations.active("claude")
    try:
        res = fn() if fn else None
    except PermissionError as e:
        return jsonify(ans("denied", "Нет доступа", str(e)))
    if cfg and (d.get("llm") or not res):
        ctx = res["data"] if res else brief()["data"]
        if res:
            ctx = {"расчёт": res["text"], "таблица": res["table"], "данные": res["data"]}
        try:
            text = H.integrations.ask_claude(cfg, q, json.dumps(ctx, ensure_ascii=False, default=str)[:24000], system=SYSTEM)
            out = res or ans("free", "Ответ", "")
            out.update(text=text, source="llm")
            if not res:
                out["title"] = "Ответ по данным сети"
            return jsonify(out)
        except Exception as e:
            if not res:
                return jsonify(ans("error", "Не удалось получить ответ", f"Claude недоступен: {e}. Задайте один из вопросов ниже — на них система отвечает сама."))
            res["tips"] = res["tips"] + [f"Подробный ответ Claude сейчас недоступен: {e}"]
    if not res:
        return jsonify(ans("unknown", "Пока не умею отвечать на этот вопрос",
                           "Я отвечаю по данным сети на вопросы о продажах магазинов, перемещениях, товарах без продаж и с избытком, "
                           "заканчивающихся товарах, заказе, плане и прибыли. Чтобы задавать любые вопросы своими словами — подключите Claude в «Интеграциях».", data={"suggest": SUGGEST}))
    return jsonify(res)


def register(hq_module):
    global H
    H = hq_module
    H.app.register_blueprint(bp)
