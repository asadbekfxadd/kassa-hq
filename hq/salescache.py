"""
Центр (этап 9): общий кэш «продажи магазин × товар» за 7/14/30/60 дней.

Его используют тревоги, обзор сети, статусы остатков, рекомендации перемещений и AI-помощник.
На 100 магазинах это самый тяжёлый расчёт — делаем его один раз и держим до 2 минут
(кассы и так присылают данные раз в минуту); фоновый поток обновляет кэш заранее, чтобы экраны не ждали.
"""
import threading
import time
from datetime import timedelta

H = None
TTL = 120
F = "%Y-%m-%d %H:%M:%S"
_C = {"at": 0, "data": None}
VER = [0]          # растёт при каждом изменении данных кэша — по нему кэшируются производные расчёты
_L = threading.Lock()


def _build():
    now = H.tnow()
    since = {k: (now - timedelta(days=k)).strftime(F) for k in (7, 14, 30, 60)}
    out = {}
    for r in H.q("""SELECT s.store_id, si.product_uid,
                    SUM(CASE WHEN s.created_at>=? THEN (CASE WHEN s.operation='sale' THEN si.qty ELSE -si.qty END) ELSE 0 END) d7,
                    SUM(CASE WHEN s.created_at>=? THEN (CASE WHEN s.operation='sale' THEN si.qty ELSE -si.qty END) ELSE 0 END) d14,
                    SUM(CASE WHEN s.created_at>=? THEN (CASE WHEN s.operation='sale' THEN si.qty ELSE -si.qty END) ELSE 0 END) d30,
                    SUM(CASE WHEN s.operation='sale' THEN si.qty ELSE -si.qty END) d60,
                    COUNT(CASE WHEN s.created_at>=? THEN 1 END) n30
                    FROM sale_items si JOIN sales s ON s.uid=si.sale_uid WHERE s.created_at>=?
                    GROUP BY s.store_id, si.product_uid""", (since[7], since[14], since[30], since[30], since[60])):
        out[(r["store_id"], r["product_uid"])] = (r["d7"] or 0, r["d14"] or 0, r["d30"] or 0, r["d60"] or 0, r["n30"] or 0)
    return out


def sold(fresh=False):
    """{(store_id, product_uid): (d7, d14, d30, d60, n30)} — по ВСЕМ магазинам; права фильтрует вызывающий код."""
    if not fresh and _C["data"] is not None and time.time() - _C["at"] < TTL:
        return _C["data"]
    with _L:
        if not fresh and _C["data"] is not None and time.time() - _C["at"] < TTL:
            return _C["data"]
        data = _build()
        _C.update(at=time.time(), data=data)
        VER[0] += 1
        return data


def add_sale(store_id, created_at, operation, items):
    """Новый чек с кассы сразу учитывается в кэше (окна по датам «съезжают» при плановом пересчёте раз в 2 минуты)."""
    d = _C["data"]
    if d is None:
        return
    VER[0] += 1
    sg = 1 if operation == "sale" else -1
    now = H.tnow()
    inw = [(created_at or "") >= (now - timedelta(days=k)).strftime(F) for k in (7, 14, 30, 60)]
    if not inw[3]:
        return          # старый чек (касса была без связи долго) — учтётся при плановом пересчёте
    with _L:
        for it in items or []:
            k = (store_id, it.get("product_uid"))
            q = sg * H.num(it.get("qty"))
            d7, d14, d30, d60, n = d.get(k, (0, 0, 0, 0, 0))
            d[k] = (d7 + q * inw[0], d14 + q * inw[1], d30 + q * inw[2], d60 + q, n + inw[2])


# ---------------------------------------------------------------- остатки магазин × товар (обновляются при каждом обмене)
_S = {"at": 0, "data": None}


def stock():
    """{(store_id, product_uid): qty}. Кассы присылают изменения — они применяются сразу (set_stock), полная перечитка — раз в 2 минуты."""
    if _S["data"] is not None and time.time() - _S["at"] < TTL:
        return _S["data"]
    with _L:
        if _S["data"] is None or time.time() - _S["at"] >= TTL:
            c = H.con()
            data = {(r[0], r[1]): r[2] for r in (tuple(dict(x).values()) for x in c.execute("SELECT store_id, product_uid, qty FROM stock").fetchall())}
            c.close()
            _S.update(at=time.time(), data=data)
            VER[0] += 1
        return _S["data"]


def set_stock(store_id, product_uid, qty):
    VER[0] += 1
    d = _S["data"]
    if d is not None:
        d[(store_id, product_uid)] = qty


def invalidate():
    VER[0] += 1
    _C["at"] = 0
    _S["at"] = 0


def warm():
    """Из фонового потока: пересчитать заранее, если кэш скоро устареет."""
    if _C["data"] is None or time.time() - _C["at"] > TTL - 30:
        _C.update(at=time.time(), data=_build())
        VER[0] += 1
    if _S["data"] is None or time.time() - _S["at"] > TTL - 30:
        _S["at"] = 0
        stock()


def register(hq_module):
    global H
    H = hq_module
