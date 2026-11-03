"""
Центр (этап 7): персонал (HRM) и клиенты (CRM).

Персонал
  • карточка: ФИО, должность, магазин, контакт, дата приёма, оклад, график, статус;
  • доступ на кассу: роль и ПИН задаются здесь и приходят на кассу магазина (касса не даст их изменить);
    кассиры, заведённые на самой кассе, появляются здесь автоматически;
  • KPI за период: чеки, выручка, средний чек, позиций и единиц в чеке, возвраты, скидки, выполнение личного плана;
    конверсия — только при наличии данных о посетителях (сейчас их нет — показываем честно «нет данных»);
  • рейтинг сотрудников; начисление окладов в расходы «Зарплаты» (один раз за месяц на сотрудника).
Клиенты
  • единая база сети: клиенты всех касс, склейка по телефону / карте; бонусы и долг — свои в каждом магазине;
  • уровни лояльности по сумме покупок в сети за 12 месяцев (скидка и кешбэк уровня работают на всех кассах);
  • сегменты, частота покупок, LTV, история покупок, любимые товары; Excel.
"""
import io
import re
import uuid
from datetime import datetime, timedelta

from flask import Blueprint, Response, jsonify, request

bp = Blueprint("people", __name__)
H = None
F = "%Y-%m-%d %H:%M:%S"
SGN = "(CASE WHEN s.operation='sale' THEN 1 ELSE -1 END)"
STATUSES = {"active": "Работает", "vacation": "В отпуске", "fired": "Уволен"}
POSITIONS = ["Кассир", "Старший кассир", "Продавец-консультант", "Управляющий магазином", "Заместитель управляющего",
             "Кладовщик", "Мерчендайзер", "Грузчик", "Охранник", "Уборщица"]
SEGMENTS = {"vip": "VIP", "regular": "Постоянные", "new": "Новые", "casual": "Обычные", "once": "Разовые",
            "risk": "Под угрозой ухода", "lost": "Ушедшие", "none": "Без покупок"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
  id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT UNIQUE NOT NULL, store_id INTEGER, full_name TEXT NOT NULL, position TEXT,
  phone TEXT, hired_at TEXT, salary REAL DEFAULT 0, schedule TEXT, status TEXT DEFAULT 'active', till_role TEXT DEFAULT '',
  pin TEXT, source TEXT DEFAULT 'hq', note TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS employee_plans (
  employee_uid TEXT NOT NULL, month TEXT NOT NULL, amount REAL NOT NULL DEFAULT 0, updated_at TEXT, PRIMARY KEY (employee_uid, month));
CREATE TABLE IF NOT EXISTS customers (
  id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT UNIQUE NOT NULL, name TEXT NOT NULL, phone TEXT, phone_key TEXT, card TEXT,
  birthday TEXT, note TEXT, level TEXT, first_store_id INTEGER, created_at TEXT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS ix_cust_phone ON customers(phone_key);
CREATE INDEX IF NOT EXISTS ix_cust_upd ON customers(updated_at);
CREATE TABLE IF NOT EXISTS customer_stores (
  customer_id INTEGER NOT NULL, store_id INTEGER NOT NULL, till_uid TEXT, bonus REAL DEFAULT 0, debt REAL DEFAULT 0,
  discount_pct REAL DEFAULT 0, updated_at TEXT, PRIMARY KEY (customer_id, store_id));
CREATE TABLE IF NOT EXISTS loyalty_levels (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, min_spend REAL NOT NULL DEFAULT 0,
  discount_pct REAL NOT NULL DEFAULT 0, cashback_pct REAL NOT NULL DEFAULT 0);
CREATE UNIQUE INDEX IF NOT EXISTS ux_cs_till ON customer_stores(store_id, till_uid);
CREATE INDEX IF NOT EXISTS ix_sales_cashier ON sales(cashier_uid);
CREATE INDEX IF NOT EXISTS ix_sales_customer ON sales(customer_uid);
"""


def phone_key(p):
    d = re.sub(r"\D", "", p or "")
    return d[-9:] if len(d) >= 9 else d


def dt(s):
    return datetime.strptime(s[:19], F) if s and len(s) >= 19 else datetime.strptime(s[:10], "%Y-%m-%d")


def period():
    a, now = request.args, H.tnow()
    f = a.get("from") or now.strftime("%Y-%m-01")
    t = a.get("to") or now.strftime("%Y-%m-%d")
    sid = a.get("store") or ""
    if sid and not H.rbac.store_allowed(sid):
        raise PermissionError("Нет доступа к магазину")
    return f, t, (int(sid) if sid else None)


def months_between(f, t):
    d0, d1 = datetime.strptime(f, "%Y-%m-%d"), datetime.strptime(t, "%Y-%m-%d")
    out, y, m = [], d0.year, d0.month
    while (y, m) <= (d1.year, d1.month):
        first = datetime(y, m, 1)
        last = (datetime(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
        days_in = (min(last, d1) - max(first, d0)).days + 1
        # месяц с 1-го числа по сегодня (или до конца) — план берётся целиком: видно, сколько месячного плана уже выполнено
        today = H.tnow().replace(hour=0, minute=0, second=0, microsecond=0)
        whole = d0 <= first and d1 >= min(last, today)
        out.append((f"{y}-{m:02d}", 1.0 if whole else days_in / last.day))
        y, m = y + (m == 12), m % 12 + 1
    return out


def J(**kw):
    return jsonify(**kw)


# ================================================================ синхронизация (вызывается из /sync)
def from_till(c, st, d, t, acks):
    """Сотрудники и клиенты кассы → Центр. Идемпотентно по uid."""
    # ---- сотрудники
    errors = (d.get("info") or {}).get("emp_errors") or {}
    for u in d.get("users") or []:
        if not u.get("uid"):
            continue
        ex = c.execute("SELECT * FROM employees WHERE uid=?", (u["uid"],)).fetchone()
        role = "admin" if u.get("role") == "admin" else "cashier"
        if not ex:
            c.execute("""INSERT INTO employees(uid,store_id,full_name,position,status,till_role,source,created_at,updated_at)
                         VALUES(?,?,?,?,?,?,'till',?,?)""", (u["uid"], st["id"], u.get("name") or "Сотрудник",
                                                           "Администратор кассы" if role == "admin" else "Кассир",
                                                           "active" if u.get("active") else "fired", role, t, t))
        elif ex["source"] == "till" and (ex["full_name"], ex["till_role"], ex["status"] == "fired") != (u.get("name"), role, not u.get("active")):
            c.execute("UPDATE employees SET full_name=?, till_role=?, status=?, updated_at=? WHERE uid=?",
                      (u.get("name") or ex["full_name"], role, ("fired" if not u.get("active") else
                                                                 (ex["status"] if ex["status"] != "fired" else "active")), t, u["uid"]))
        # старые продажи без uid кассира — по имени в этом магазине
        c.execute("UPDATE sales SET cashier_uid=? WHERE store_id=? AND cashier=? AND cashier_uid IS NULL",
                  (u["uid"], st["id"], u.get("name")))
    # ---- клиенты
    affected = set()
    for x in d.get("customers") or []:
        if not x.get("uid"):
            continue
        cid = link_customer(c, st, x, t)
        affected.add(cid)
        acks.setdefault("customers", []).append(x["uid"])
    uids = list({s.get("customer_uid") for s in d.get("sales") or [] if s.get("customer_uid")})
    for u in uids:   # клиент сети (пришёл из Центра) купил здесь, а касса ещё не присылала его карточку — привязываем
        if not c.execute("SELECT 1 FROM customer_stores WHERE store_id=? AND till_uid=?", (st["id"], u)).fetchone():
            cu = c.execute("SELECT id FROM customers WHERE uid=?", (u,)).fetchone()
            if cu and not c.execute("SELECT 1 FROM customer_stores WHERE customer_id=? AND store_id=?", (cu["id"], st["id"])).fetchone():
                c.execute("INSERT INTO customer_stores(customer_id,store_id,till_uid,updated_at) VALUES(?,?,?,?)", (cu["id"], st["id"], u, t))
    if uids:
        for r in c.execute(f"SELECT customer_id FROM customer_stores WHERE store_id=? AND till_uid IN ({','.join('?' * len(uids))})", [st["id"]] + uids).fetchall():
            affected.add(r["customer_id"])
    if affected:
        recalc_levels(c, t, affected)
    return errors


def link_customer(c, st, x, t):
    pk = phone_key(x.get("phone"))
    cs = c.execute("SELECT customer_id FROM customer_stores WHERE store_id=? AND till_uid=?", (st["id"], x["uid"])).fetchone()
    cu = c.execute("SELECT * FROM customers WHERE id=?", (cs["customer_id"],)).fetchone() if cs else None
    if not cu:
        cu = c.execute("SELECT * FROM customers WHERE uid=?", (x["uid"],)).fetchone()
    if not cu and len(pk) >= 7:
        cu = c.execute("SELECT * FROM customers WHERE phone_key=? ORDER BY id LIMIT 1", (pk,)).fetchone()
    if not cu and x.get("card"):
        cu = c.execute("SELECT * FROM customers WHERE card=? ORDER BY id LIMIT 1", (x["card"],)).fetchone()
    if cu:
        cid = cu["id"]
        new = (x.get("name") or cu["name"], x.get("phone") or cu["phone"], x.get("card") or cu["card"], x.get("birthday") or cu["birthday"])
        if new != (cu["name"], cu["phone"], cu["card"], cu["birthday"]):
            c.execute("UPDATE customers SET name=?, phone=?, phone_key=?, card=?, birthday=?, updated_at=? WHERE id=?",
                      new[:2] + (phone_key(new[1]),) + new[2:] + (t, cid))
    else:
        c.execute("""INSERT INTO customers(uid,name,phone,phone_key,card,birthday,first_store_id,created_at,updated_at)
                     VALUES(?,?,?,?,?,?,?,?,?)""", (x["uid"], x.get("name") or "Клиент", x.get("phone"), pk, x.get("card"),
                                                    x.get("birthday"), st["id"], x.get("created_at") or t, t))
        cid = c.execute("SELECT id FROM customers WHERE uid=?", (x["uid"],)).fetchone()["id"]
    ex = c.execute("SELECT till_uid FROM customer_stores WHERE customer_id=? AND store_id=?", (cid, st["id"])).fetchone()
    vals = (x["uid"], H.num(x.get("bonus")), H.num(x.get("debt")), H.num(x.get("discount_pct")), t)
    if ex:
        c.execute("UPDATE customer_stores SET till_uid=?, bonus=?, debt=?, discount_pct=?, updated_at=? WHERE customer_id=? AND store_id=?",
                  vals + (cid, st["id"]))
    else:
        c.execute("DELETE FROM customer_stores WHERE store_id=? AND till_uid=?", (st["id"], x["uid"]))    # перепривязка (клиента объединили)
        c.execute("INSERT INTO customer_stores(till_uid,bonus,debt,discount_pct,updated_at,customer_id,store_id) VALUES(?,?,?,?,?,?,?)",
                  vals + (cid, st["id"]))
    return cid


def levels(c=None):
    return [dict(r) for r in (c.execute("SELECT * FROM loyalty_levels ORDER BY min_spend").fetchall() if c
                              else H.q("SELECT * FROM loyalty_levels ORDER BY min_spend"))]


def level_for(lv, spend):
    cur = None
    for x in lv:
        if spend >= x["min_spend"]:
            cur = x
    return cur


def recalc_levels(c, t, ids=None):
    """Уровень = по сумме покупок в сети за 12 месяцев. Изменившиеся клиенты уйдут на кассы (updated_at)."""
    lv = levels(c)
    y = (H.tnow() - timedelta(days=365)).strftime(F)
    w, a = "", [y]
    if ids is not None:
        ids = list(ids)
        if not ids:
            return 0
        w = f" AND cs.customer_id IN ({','.join('?' * len(ids))})"
        a += ids
    spend = {r["customer_id"]: r["v"] or 0 for r in c.execute(
        f"""SELECT cs.customer_id, SUM({SGN}*s.total) v FROM sales s JOIN customer_stores cs ON cs.till_uid=s.customer_uid AND cs.store_id=s.store_id
            WHERE s.created_at>=?{w} GROUP BY cs.customer_id""", a).fetchall()}
    rows = c.execute(f"SELECT id, level FROM customers{' WHERE id IN (' + ','.join('?' * len(ids)) + ')' if ids is not None else ''}",
                     ids or ()).fetchall()
    n = 0
    for r in rows:
        x = level_for(lv, spend.get(r["id"], 0))
        name = x["name"] if x else None
        if name != r["level"]:
            c.execute("UPDATE customers SET level=?, updated_at=? WHERE id=?", (name, t, r["id"]))
            n += 1
    return n


def to_till(c, st, d, t):
    """Вниз: сотрудники магазина (управляемые Центром) и единая база клиентов с уровнями (по курсору)."""
    emps = [{"uid": r["uid"], "name": r["full_name"], "pin": r["pin"], "role": r["till_role"], "active": r["status"] == "active"}
            for r in c.execute("""SELECT uid, full_name, pin, till_role, status FROM employees WHERE store_id=? AND source='hq'
                                  AND COALESCE(till_role,'')<>'' AND COALESCE(pin,'')<>''""", (st["id"],)).fetchall()]
    since = d.get("cust_since") or "1970-01-01 00:00:00"
    lim = 3000
    rows = [dict(r) for r in c.execute("""SELECT COALESCE(cs.till_uid, c.uid) AS uid, c.name, c.phone, c.card, c.birthday, c.level,
               l.discount_pct AS level_discount, l.cashback_pct AS level_cashback, c.updated_at
               FROM customers c LEFT JOIN customer_stores cs ON cs.customer_id=c.id AND cs.store_id=?
               LEFT JOIN loyalty_levels l ON l.name=c.level
               WHERE c.updated_at>=? AND (cs.till_uid IS NOT NULL OR COALESCE(c.phone_key,'')<>'' OR COALESCE(c.card,'')<>'')
               ORDER BY c.updated_at, c.id LIMIT ?""", (st["id"], since, lim)).fetchall()]
    cust_time = rows[-1]["updated_at"] if len(rows) == lim else t
    for r in rows:
        r.pop("updated_at", None)
    return {"employees": emps, "customers_net": rows, "cust_time": cust_time}


# ================================================================ персонал
def can_edit():
    return H.rbac.can("staff.edit")


def emp_public(e, errors):
    e = dict(e)
    e["has_pin"] = bool(e.pop("pin", None))
    e["status_title"] = STATUSES.get(e["status"], e["status"])
    e["till_error"] = errors.get(e["uid"])
    if not can_edit():
        e["salary"] = None
    return e


def store_errors():
    import json as _j
    out = {}
    for r in H.q("SELECT info FROM stores"):
        out.update((_j.loads(r["info"] or "{}") or {}).get("emp_errors") or {})
    return out


def staff_kpi(f, t, store=None, uids=None):
    a0, a1 = f + " 00:00:00", t + " 23:59:59"
    w, args = "s.created_at BETWEEN ? AND ? AND s.cashier_uid IS NOT NULL", [a0, a1]
    if store:
        w += " AND s.store_id=?"
        args.append(store)
    fs, fa = H.rbac.sf("s.store_id")
    w += fs
    args += list(fa)
    k = {r["u"]: dict(r) for r in H.q(f"""SELECT s.cashier_uid u, COUNT(CASE WHEN s.operation='sale' THEN 1 END) checks,
            COALESCE(SUM({SGN}*s.total),0) net, COUNT(CASE WHEN s.operation='refund' THEN 1 END) refunds,
            COALESCE(SUM(CASE WHEN s.operation='refund' THEN s.total ELSE 0 END),0) refunds_sum,
            COALESCE(SUM(CASE WHEN s.operation='sale' THEN s.discount ELSE 0 END),0) discount
            FROM sales s WHERE {w} GROUP BY s.cashier_uid""", args)}
    for r in H.q(f"""SELECT s.cashier_uid u, COALESCE(SUM(s.items_n),0) lines, COALESCE(SUM(s.units),0) units FROM sales s
                     WHERE {w} AND s.operation='sale' GROUP BY s.cashier_uid""", args):
        if r["u"] in k:
            k[r["u"]].update(lines=r["lines"], units=r["units"])
    mon = months_between(f, t)
    plans = {}
    if mon:
        for r in H.q(f"SELECT employee_uid, month, amount FROM employee_plans WHERE month IN ({','.join('?' * len(mon))})", [m for m, _ in mon]):
            share = dict(mon)[r["month"]]
            plans[r["employee_uid"]] = plans.get(r["employee_uid"], 0) + r["amount"] * share
    return k, plans


def rate(rows):
    """Рейтинг: среднее нормированных показателей (выручка, средний чек, позиций в чеке, выполнение плана — если есть планы)."""
    act = [r for r in rows if r["checks"]]
    if not act:
        return
    mx = {k: max(r[k] or 0 for r in act) or 1 for k in ("net", "avg", "upt")}
    has_plan = any(r["plan_pct"] is not None for r in act)
    for r in act:
        parts = [max(0, r["net"]) / mx["net"], r["avg"] / mx["avg"], r["upt"] / mx["upt"]]
        if has_plan:
            parts.append(min(r["plan_pct"] or 0, 150) / 150)
        r["score"] = round(sum(parts) / len(parts) * 100)
    for i, r in enumerate(sorted(act, key=lambda r: -r["score"]), 1):
        r["rank"] = i


@bp.get("/api/staff")
def staff_list():
    try:
        f, t, store = period()
    except PermissionError as e:
        return H.err(str(e), 403)
    fs, fa = H.rbac.sf("e.store_id")
    w, a = "1=1" + fs, list(fa)
    if store:
        w += " AND e.store_id=?"
        a.append(store)
    if request.args.get("status") != "all":
        w += " AND e.status<>'fired'"
    rows = H.q(f"SELECT e.*, st.name AS store FROM employees e LEFT JOIN stores st ON st.id=e.store_id WHERE {w} ORDER BY st.name, e.full_name", a)
    k, plans = staff_kpi(f, t, store)
    errs = store_errors()
    out = []
    for e in rows:
        x = k.get(e["uid"], {})
        ch = x.get("checks", 0)
        r = emp_public(e, errs)
        r.update(checks=ch, net=round(x.get("net", 0)), avg=round(x["net"] / ch) if ch else 0, upt=round(x.get("lines", 0) / ch, 2) if ch else 0,
                 units=round(x.get("units", 0) / ch, 2) if ch else 0, refunds=x.get("refunds", 0), refunds_sum=x.get("refunds_sum", 0),
                 discount=x.get("discount", 0), plan=round(plans[e["uid"]]) if e["uid"] in plans else None, score=None, rank=None)
        r["plan_pct"] = round(r["net"] / r["plan"] * 100, 1) if r["plan"] else None
        out.append(r)
    rate(out)
    tot = {"employees": len(out), "active": sum(1 for r in out if r["status"] == "active"), "checks": sum(r["checks"] for r in out),
           "net": sum(r["net"] for r in out), "payroll": sum((r["salary"] or 0) for r in out if r["status"] != "fired") if can_edit() else None}
    tot["avg"] = round(tot["net"] / tot["checks"]) if tot["checks"] else 0
    return J(items=out, total=tot, can_edit=can_edit(), can_payroll=can_edit() and H.rbac.can("expenses"), positions=POSITIONS,
             statuses=STATUSES, visitors=False, period=[f, t])


@bp.get("/api/staff/<uid>")
def staff_card(uid):
    e = H.q("SELECT e.*, st.name AS store FROM employees e LEFT JOIN stores st ON st.id=e.store_id WHERE e.uid=?", (uid,), one=True)
    if not e or not H.rbac.store_allowed(e["store_id"]):
        return H.err("Сотрудник не найден", 404)
    now = H.tnow()
    m0 = (now.replace(day=1) - timedelta(days=330)).strftime("%Y-%m-01")
    hist = {r["m"]: r for r in H.q(f"""SELECT substr(s.created_at,1,7) m, COUNT(CASE WHEN s.operation='sale' THEN 1 END) checks,
              COALESCE(SUM({SGN}*s.total),0) net FROM sales s WHERE s.cashier_uid=? AND s.created_at>=? GROUP BY substr(s.created_at,1,7)""",
                                   (uid, m0 + " 00:00:00"))}
    plans = {r["month"]: r["amount"] for r in H.q("SELECT month, amount FROM employee_plans WHERE employee_uid=?", (uid,))}
    months = []
    y, m = int(m0[:4]), int(m0[5:7])
    for _ in range(12):
        key = f"{y}-{m:02d}"
        h = hist.get(key, {})
        months.append({"month": key, "checks": h.get("checks", 0), "net": round(h.get("net", 0)), "plan": plans.get(key),
                       "avg": round(h["net"] / h["checks"]) if h.get("checks") else 0})
        y, m = y + (m == 12), m % 12 + 1
    nxt = (now.replace(day=28) + timedelta(days=5)).strftime("%Y-%m")
    log = H.q("SELECT created_at, user_name, action, details FROM audit WHERE details LIKE ? ORDER BY id DESC LIMIT 30", (f"%{e['full_name']}%",))
    return J(employee=emp_public(e, store_errors()), months=months, plan_cur=plans.get(now.strftime("%Y-%m")), plan_next=plans.get(nxt),
             cur_month=now.strftime("%Y-%m"), next_month=nxt, log=log, can_edit=can_edit())


@bp.post("/api/staff")
def staff_save():
    d = request.json or {}
    name = (d.get("full_name") or "").strip()
    if not name:
        return H.err("Укажите ФИО")
    sid = int(d["store_id"]) if d.get("store_id") else None
    if not sid:
        return H.err("Выберите магазин")
    if not H.rbac.store_allowed(sid):
        return H.err("Нет доступа к магазину", 403)
    status = d.get("status") or "active"
    role = d.get("till_role") or ""
    if status not in STATUSES or role not in ("", "cashier", "admin"):
        return H.err("Неверный статус или роль")
    pin = str(d.get("pin") or "").strip()
    if pin and not re.fullmatch(r"\d{4,8}", pin):
        return H.err("ПИН — от 4 до 8 цифр")
    c = H.con()
    ex = c.execute("SELECT * FROM employees WHERE uid=?", (d["uid"],)).fetchone() if d.get("uid") else None
    if d.get("uid") and (not ex or not H.rbac.store_allowed(ex["store_id"])):
        c.close()
        return H.err("Сотрудник не найден", 404)
    uid = ex["uid"] if ex else uuid.uuid4().hex
    source = ex["source"] if ex else "hq"
    if pin:
        source = "hq"                       # ПИН задан в Центре — дальше доступом на кассу управляет Центр
    if role and source == "hq" and not pin and not (ex and ex["pin"]) and not (ex and ex["source"] == "till"):
        c.close()
        return H.err("Для входа на кассу задайте ПИН")
    if pin and c.execute("SELECT 1 FROM employees WHERE store_id=? AND pin=? AND uid<>? AND status<>'fired'", (sid, pin, uid)).fetchone():
        c.close()
        return H.err("Такой ПИН уже у другого сотрудника этого магазина")
    t = H.now()
    v = {"store_id": sid, "full_name": name, "position": (d.get("position") or "").strip(), "phone": (d.get("phone") or "").strip(),
         "hired_at": d.get("hired_at") or None, "schedule": (d.get("schedule") or "").strip(), "status": status, "till_role": role,
         "note": (d.get("note") or "").strip(), "source": source, "updated_at": t}
    if can_edit() and "salary" in d:
        v["salary"] = H.num(d.get("salary"))
    if pin:
        v["pin"] = pin
    if ex:
        if ex["source"] == "till" and source == "till":
            v["till_role"] = ex["till_role"]          # роль кассы пока задаёт касса
        c.execute(f"UPDATE employees SET {','.join(k + '=?' for k in v)} WHERE uid=?", list(v.values()) + [uid])
        ch = [lbl for k, lbl in (("full_name", "ФИО"), ("position", "должность"), ("store_id", "магазин"), ("status", "статус"),
                                 ("till_role", "роль на кассе"), ("salary", "оклад"), ("schedule", "график"), ("phone", "телефон"))
              if k in v and str(v[k] or "") != str(ex[k] or "")]
        if pin and pin != ex["pin"]:
            ch.append("ПИН")
        H.rbac.audit("Сотрудник изменён", f"{name}: {', '.join(ch) or 'без изменений'}" +
                     (f" → {STATUSES[status]}" if status != ex["status"] else ""), sid, c=c)
    else:
        v.update(uid=uid, created_at=t, salary=v.get("salary", 0))
        c.execute(f"INSERT INTO employees({','.join(v)}) VALUES({','.join('?' * len(v))})", list(v.values()))
        H.rbac.audit("Сотрудник принят", f"{name} — {v['position'] or 'без должности'}" + (" · доступ на кассу" if role else ""), sid, c=c)
    c.commit()
    c.close()
    return J(ok=True, uid=uid)


@bp.post("/api/staff/<uid>/plan")
def staff_plan(uid):
    d = request.json or {}
    e = H.q("SELECT * FROM employees WHERE uid=?", (uid,), one=True)
    if not e or not H.rbac.store_allowed(e["store_id"]):
        return H.err("Сотрудник не найден", 404)
    c = H.con()
    for p in d.get("plans") or []:
        if not re.fullmatch(r"\d{4}-\d{2}", p.get("month") or ""):
            continue
        c.execute("DELETE FROM employee_plans WHERE employee_uid=? AND month=?", (uid, p["month"]))
        if H.num(p.get("amount")) > 0:
            c.execute("INSERT INTO employee_plans(employee_uid,month,amount,updated_at) VALUES(?,?,?,?)", (uid, p["month"], H.num(p["amount"]), H.now()))
    H.rbac.audit("Личный план продаж", f"{e['full_name']}: " + ", ".join(f"{p['month']} — {H.num(p.get('amount')):,.0f}".replace(",", " ")
                                                                       for p in d.get("plans") or []), e["store_id"], c=c)
    c.commit()
    c.close()
    return J(ok=True)


@bp.post("/api/staff/plan_split")
def staff_plan_split():
    """План магазина на месяц делится поровну между работающими сотрудниками с доступом на кассу."""
    d = request.json or {}
    sid, month = int(d.get("store_id") or 0), d.get("month") or H.tnow().strftime("%Y-%m")
    if not sid or not H.rbac.store_allowed(sid):
        return H.err("Выберите магазин")
    sp = H.q("SELECT amount FROM store_plans WHERE store_id=? AND month=?", (sid, month), one=True)
    if not sp or not sp["amount"]:
        return H.err("У магазина нет плана на этот месяц — задайте его в карточке магазина")
    emps = H.q("SELECT uid, full_name FROM employees WHERE store_id=? AND status='active' AND COALESCE(till_role,'')<>''", (sid,))
    if not emps:
        return H.err("В магазине нет работающих сотрудников с доступом на кассу")
    part = round(sp["amount"] / len(emps))
    c = H.con()
    for e in emps:
        c.execute("DELETE FROM employee_plans WHERE employee_uid=? AND month=?", (e["uid"], month))
        c.execute("INSERT INTO employee_plans(employee_uid,month,amount,updated_at) VALUES(?,?,?,?)", (e["uid"], month, part, H.now()))
    H.rbac.audit("План магазина разделён между сотрудниками", f"{month}: {len(emps)} × {part:,.0f}".replace(",", " "), sid, c=c)
    c.commit()
    c.close()
    return J(ok=True, n=len(emps), each=part)


@bp.post("/api/staff/payroll")
def staff_payroll():
    """Оклады месяца → расходы «Зарплаты» по магазинам. Повторное начисление за тот же месяц не дублируется."""
    if not H.rbac.can("expenses"):
        return H.err("Недостаточно прав: «Расходы»", 403)
    d = request.json or {}
    month = d.get("month") or H.tnow().strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        return H.err("Неверный месяц")
    y, m = int(month[:4]), int(month[5:])
    last = datetime(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    when = min(last, H.tnow()).strftime("%Y-%m-%d") + " 18:00:00"
    fs, fa = H.rbac.sf("store_id")
    emps = H.q(f"SELECT * FROM employees WHERE status<>'fired' AND salary>0{fs}" + (" AND store_id=?" if d.get("store_id") else ""),
               list(fa) + ([int(d["store_id"])] if d.get("store_id") else []))
    c = H.con()
    made, skipped, total = 0, 0, 0
    for e in emps:
        uid = f"sal-{e['uid']}-{month}"
        if c.execute("SELECT 1 FROM money_ops WHERE uid=?", (uid,)).fetchone():
            skipped += 1
            continue
        c.execute("""INSERT INTO money_ops(uid,store_id,created_at,kind,type,method,amount,category,note,user_name,source)
                     VALUES(?,?,?,'hq_expense','expense','bank',?,'Зарплаты',?,?,'hq')""",
                  (uid, e["store_id"], when, e["salary"], f"Оклад за {month}: {e['full_name']}", (H.rbac.user() or {}).get("name", "")))
        made += 1
        total += e["salary"]
    if made:
        H.rbac.audit("Начислены зарплаты", f"{month}: {made} сотр. на {total:,.0f}".replace(",", " "), None, c=c)
    c.commit()
    c.close()
    return J(ok=True, made=made, skipped=skipped, total=total)


# ================================================================ клиенты
def cust_scope():
    ids = H.rbac.store_ids()
    if ids is None:
        return "", ()
    return f" AND EXISTS (SELECT 1 FROM customer_stores x WHERE x.customer_id=c.id AND x.store_id IN ({','.join('?' * len(ids)) or 'NULL'}))", tuple(ids)


def cust_stats(store=None):
    now = H.tnow()
    y, d90 = (now - timedelta(days=365)).strftime(F), (now - timedelta(days=90)).strftime(F)
    fs, fa = H.rbac.sf("s.store_id")
    w, a = "1=1" + fs, [y, d90] + list(fa)
    if store:
        w += " AND s.store_id=?"
        a.append(store)
    return {r["cid"]: r for r in H.q(f"""SELECT cs.customer_id cid, COUNT(DISTINCT CASE WHEN s.operation='sale' THEN s.uid END) orders,
            COALESCE(SUM({SGN}*s.total),0) net, MIN(s.created_at) first, MAX(CASE WHEN s.operation='sale' THEN s.created_at END) last,
            COALESCE(SUM(CASE WHEN s.created_at>=? THEN {SGN}*s.total ELSE 0 END),0) net365,
            COUNT(DISTINCT CASE WHEN s.operation='sale' AND s.created_at>=? THEN s.uid END) orders90
            FROM sales s JOIN customer_stores cs ON cs.till_uid=s.customer_uid AND cs.store_id=s.store_id WHERE {w} GROUP BY cs.customer_id""", a)}


def segment(st, vip, now):
    if not st or not st["orders"] or not st["last"]:
        return "none"
    dsl, dsf = (now - dt(st["last"])).days, (now - dt(st["first"])).days
    if dsf <= 30:
        return "new"
    if dsl > 120:
        return "lost"
    if st["orders"] >= 3 and dsl > 45:
        return "risk"
    if vip and st["net365"] >= vip:
        return "vip"
    if st["orders90"] >= 3:
        return "regular"
    if st["orders"] == 1:
        return "once"
    return "casual"


def birthday_soon(b, now):
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", b or "") or re.match(r"(\d{2})\.(\d{2})", b or "")
    if not m:
        return False
    mon = int(m.group(2))
    return mon == now.month


def customers_data(store=None):
    now = H.tnow()
    sc, sa = cust_scope()
    w, a = "1=1" + sc, list(sa)
    if store:
        w += " AND EXISTS (SELECT 1 FROM customer_stores y WHERE y.customer_id=c.id AND y.store_id=?)"
        a.append(store)
    base = H.q(f"SELECT c.* FROM customers c WHERE {w}", a)
    links = {}
    fs, fa = H.rbac.sf("cs.store_id")
    for r in H.q(f"SELECT cs.customer_id, cs.store_id, st.name, cs.bonus, cs.debt FROM customer_stores cs JOIN stores st ON st.id=cs.store_id WHERE 1=1{fs}", fa):
        links.setdefault(r["customer_id"], []).append(r)
    stats = cust_stats(store)
    n365 = sorted([s["net365"] for s in stats.values() if s["net365"] > 0])
    vip = n365[int(len(n365) * 0.9)] if len(n365) >= 10 else None
    lv = levels()
    out = []
    for c in base:
        st = stats.get(c["id"])
        r = {"id": c["id"], "name": c["name"], "phone": c["phone"], "card": c["card"], "birthday": c["birthday"], "level": c["level"],
             "created_at": c["created_at"], "orders": st["orders"] if st else 0, "ltv": round(st["net"]) if st else 0,
             "net365": round(st["net365"]) if st else 0, "last": st["last"] if st else None, "first": st["first"] if st else None,
             "bonus": sum(x["bonus"] or 0 for x in links.get(c["id"], [])), "debt": sum(x["debt"] or 0 for x in links.get(c["id"], [])),
             "stores": [x["name"] for x in links.get(c["id"], [])]}
        r["avg"] = round(r["ltv"] / r["orders"]) if r["orders"] else 0
        r["freq"] = round((dt(r["last"]) - dt(r["first"])).days / (r["orders"] - 1), 1) if r["orders"] > 1 and r["last"] else None
        r["days_since"] = (now - dt(r["last"])).days if r["last"] else None
        r["segment"] = segment(st, vip, now)
        r["bday"] = birthday_soon(c["birthday"], now)
        nxt = next((x for x in lv if x["min_spend"] > r["net365"]), None)
        r["next_level"] = {"name": nxt["name"], "left": round(nxt["min_spend"] - r["net365"])} if nxt else None
        out.append(r)
    return out, vip


def cust_summary(rows, store=None):
    now = H.tnow()
    buyers = [r for r in rows if r["orders"]]
    d30 = (now - timedelta(days=30)).strftime(F)
    fs, fa = H.rbac.sf("s.store_id")
    w, a = "s.created_at>=?" + fs, [d30] + list(fa)
    if store:
        w += " AND s.store_id=?"
        a.append(store)
    rv = H.q(f"SELECT COALESCE(SUM({SGN}*s.total),0) allv, COALESCE(SUM(CASE WHEN s.customer_uid IS NOT NULL THEN {SGN}*s.total ELSE 0 END),0) custv FROM sales s WHERE {w}", a, one=True)
    fr = [r["freq"] for r in buyers if r["freq"] is not None]
    seg = {k: 0 for k in SEGMENTS}
    for r in rows:
        seg[r["segment"]] += 1
    lvc = {}
    for r in rows:
        lvc[r["level"] or ""] = lvc.get(r["level"] or "", 0) + 1
    tot_orders = sum(r["orders"] for r in buyers)
    return {"total": len(rows), "buyers": len(buyers), "active90": sum(1 for r in buyers if r["days_since"] is not None and r["days_since"] <= 90),
            "new30": sum(1 for r in rows if (r["created_at"] or "") >= d30), "avg_ltv": round(sum(r["ltv"] for r in buyers) / len(buyers)) if buyers else 0,
            "avg_check": round(sum(r["ltv"] for r in buyers) / tot_orders) if tot_orders else 0,
            "avg_freq": round(sum(fr) / len(fr), 1) if fr else None, "share30": round(rv["custv"] / rv["allv"] * 100, 1) if rv["allv"] else None,
            "debtors": sum(1 for r in rows if r["debt"] > 0), "bdays": sum(1 for r in rows if r["bday"]), "segments": seg, "levels": lvc}


def cust_filter(rows):
    a = request.args
    qv = (a.get("q") or "").strip().lower()
    if qv:
        qd = re.sub(r"\D", "", qv)
        rows = [r for r in rows if qv in (r["name"] or "").lower() or (len(qd) >= 3 and qd in re.sub(r"\D", "", r["phone"] or "")) or qv == (r["card"] or "").lower()]
    if a.get("segment"):
        rows = [r for r in rows if r["segment"] == a["segment"]]
    if a.get("level") is not None and a.get("level") != "*":
        rows = [r for r in rows if (r["level"] or "") == a["level"]]
    if a.get("flag") == "debt":
        rows = [r for r in rows if r["debt"] > 0]
    if a.get("flag") == "bday":
        rows = [r for r in rows if r["bday"]]
    key = a.get("sort") or "ltv"
    if key not in ("ltv", "orders", "avg", "net365", "bonus", "debt", "days_since", "freq", "name", "created_at"):
        key = "ltv"
    if key == "name":
        return sorted(rows, key=lambda r: (r["name"] or "").lower())
    has = [r for r in rows if r.get(key) is not None]
    has.sort(key=lambda r: r[key], reverse=key not in ("days_since", "freq"))
    return has + [r for r in rows if r.get(key) is None]


@bp.get("/api/crm/customers")
def crm_list():
    try:
        _, _, store = period()
    except PermissionError as e:
        return H.err(str(e), 403)
    rows, vip = customers_data(store)
    summ = cust_summary(rows, store)
    rows = cust_filter(rows)
    return J(items=rows[:500], found=len(rows), summary=summ, segments=SEGMENTS, levels=levels(), vip_from=round(vip) if vip else None,
             can_edit=H.rbac.can("crm.edit"), can_levels=H.rbac.can("loyalty"))


@bp.get("/api/crm/customers/<int:cid>")
def crm_card(cid):
    sc, sa = cust_scope()
    c = H.q(f"SELECT c.* FROM customers c WHERE c.id=?{sc}", (cid,) + tuple(sa), one=True)
    if not c:
        return H.err("Клиент не найден", 404)
    rows, _ = customers_data()
    me = next((r for r in rows if r["id"] == cid), {})
    fs, fa = H.rbac.sf("cs.store_id")
    stores = H.q(f"""SELECT st.name, cs.bonus, cs.debt, cs.discount_pct, cs.updated_at FROM customer_stores cs JOIN stores st ON st.id=cs.store_id
                     WHERE cs.customer_id=?{fs} ORDER BY st.name""", (cid,) + tuple(fa))
    fs2, fa2 = H.rbac.sf("s.store_id")
    sales = H.q(f"""SELECT s.uid, s.created_at, s.operation, s.total, s.cash, s.card, COALESCE(s.qr,0) qr, s.debt, s.discount, st.name AS store, s.cashier
                    FROM sales s JOIN customer_stores cs ON cs.till_uid=s.customer_uid AND cs.store_id=s.store_id JOIN stores st ON st.id=s.store_id
                    WHERE cs.customer_id=?{fs2} ORDER BY s.created_at DESC LIMIT 100""", (cid,) + tuple(fa2))
    if sales:
        items = {}
        uids = [s["uid"] for s in sales]
        for it in H.q(f"SELECT sale_uid, name, qty, total FROM sale_items WHERE sale_uid IN ({','.join('?' * len(uids))})", uids):
            items.setdefault(it["sale_uid"], []).append(it)
        for s in sales:
            s["items"] = items.get(s["uid"], [])
    top = H.q(f"""SELECT si.name, COALESCE(SUM({SGN}*si.qty),0) qty, COALESCE(SUM({SGN}*si.total),0) net, COUNT(DISTINCT s.uid) times
                  FROM sale_items si JOIN sales s ON s.uid=si.sale_uid JOIN customer_stores cs ON cs.till_uid=s.customer_uid AND cs.store_id=s.store_id
                  WHERE cs.customer_id=?{fs2} GROUP BY si.name ORDER BY net DESC LIMIT 8""", (cid,) + tuple(fa2))
    cats = H.q(f"""SELECT COALESCE(NULLIF(p.category,''),'Без категории') AS name, COALESCE(SUM({SGN}*si.total),0) net
                   FROM sale_items si JOIN sales s ON s.uid=si.sale_uid JOIN customer_stores cs ON cs.till_uid=s.customer_uid AND cs.store_id=s.store_id
                   LEFT JOIN products p ON p.uid=si.product_uid WHERE cs.customer_id=?{fs2}
                   GROUP BY COALESCE(NULLIF(p.category,''),'Без категории') ORDER BY net DESC LIMIT 6""", (cid,) + tuple(fa2))
    lv = levels()
    cur = next((x for x in lv if x["name"] == c["level"]), None)
    return J(customer=c, stats=me, stores=stores, sales=sales, top=top, categories=cats, level=cur, levels=lv,
             segment_title=SEGMENTS.get(me.get("segment"), ""), can_edit=H.rbac.can("crm.edit"))


@bp.post("/api/crm/customers/<int:cid>")
def crm_save(cid):
    sc, sa = cust_scope()
    c0 = H.q(f"SELECT c.* FROM customers c WHERE c.id=?{sc}", (cid,) + tuple(sa), one=True)
    if not c0:
        return H.err("Клиент не найден", 404)
    d = request.json or {}
    name, phone = (d.get("name") or "").strip(), (d.get("phone") or "").strip()
    if not name:
        return H.err("Укажите имя")
    pk = phone_key(phone)
    if pk:
        dup = H.q("SELECT name FROM customers WHERE phone_key=? AND id<>?", (pk, cid), one=True)
        if dup:
            return H.err(f"Этот телефон уже у клиента «{dup['name']}»")
    card = (d.get("card") or "").strip() or None
    c = H.con()
    c.execute("UPDATE customers SET name=?, phone=?, phone_key=?, card=?, birthday=?, note=?, updated_at=? WHERE id=?",
              (name, phone or None, pk, card, d.get("birthday") or None, (d.get("note") or "").strip(), H.now(), cid))
    H.rbac.audit("Клиент изменён", f"{c0['name']} → {name}, {phone}", None, c=c)
    c.commit()
    c.close()
    return J(ok=True)


@bp.get("/api/crm/levels")
def crm_levels():
    rows, _ = customers_data()
    cnt = {}
    for r in rows:
        cnt[r["level"] or ""] = cnt.get(r["level"] or "", 0) + 1
    return J(levels=levels(), counts=cnt, can_levels=H.rbac.can("loyalty"))


@bp.post("/api/crm/levels")
def crm_levels_save():
    d = request.json or {}
    lv, names = [], set()
    for x in d.get("levels") or []:
        n = (x.get("name") or "").strip()
        if not n:
            continue
        if n.lower() in names:
            return H.err(f"Уровень «{n}» указан дважды")
        names.add(n.lower())
        ms, dp, cb = H.num(x.get("min_spend")), H.num(x.get("discount_pct")), H.num(x.get("cashback_pct"))
        if ms < 0 or not (0 <= dp <= 50) or not (0 <= cb <= 30):
            return H.err(f"«{n}»: скидка 0–50%, кешбэк 0–30%, порог не меньше 0")
        lv.append((n, ms, dp, cb))
    if len({x[1] for x in lv}) != len(lv):
        return H.err("У уровней должны быть разные пороги суммы")
    c = H.con()
    c.execute("DELETE FROM loyalty_levels")
    for x in sorted(lv, key=lambda x: x[1]):
        c.execute("INSERT INTO loyalty_levels(name,min_spend,discount_pct,cashback_pct) VALUES(?,?,?,?)", x)
    t = H.now()
    recalc_levels(c, t)
    c.execute("UPDATE customers SET updated_at=? WHERE level IS NOT NULL", (t,))     # новые скидки/кешбэк уровня — на все кассы
    H.rbac.audit("Уровни лояльности", "; ".join(f"{n} от {ms:,.0f}: скидка {dp:g}%, кешбэк {cb:g}%".replace(",", " ") for n, ms, dp, cb in lv) or "отключены", None, c=c)
    c.commit()
    c.close()
    return J(ok=True)


# ================================================================ Excel
def _xlsx(title, hdr, rows, name):
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title[:31]
    ws.append(hdr)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1D4ED8")
    for r in rows:
        ws.append(r)
    for i in range(1, len(hdr) + 1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = 30 if i == 1 else 15
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return Response(buf.getvalue(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename={name}.xlsx"})


@bp.get("/api/staff/export.xlsx")
def staff_export():
    r = staff_list().get_json()
    ed = r["can_edit"]
    hdr = ["Сотрудник", "Должность", "Магазин", "Статус", "Телефон", "Принят", "График"] + (["Оклад"] if ed else []) + \
          ["Чеков", "Выручка", "Средний чек", "Позиций в чеке", "Единиц в чеке", "Возвратов", "Скидки", "План", "План, %", "Баллы", "Место"]
    rows = [[x["full_name"], x["position"], x["store"], x["status_title"], x["phone"], x["hired_at"], x["schedule"]] + ([x["salary"]] if ed else []) +
            [x["checks"], x["net"], x["avg"], x["upt"], x["units"], x["refunds"], x["discount"], x["plan"], x["plan_pct"], x["score"], x["rank"]]
            for x in sorted(r["items"], key=lambda x: (x["rank"] or 9999))]
    return _xlsx("Персонал", hdr, rows, f"staff_{r['period'][0]}_{r['period'][1]}")


@bp.get("/api/crm/export.xlsx")
def crm_export():
    try:
        _, _, store = period()
    except PermissionError as e:
        return H.err(str(e), 403)
    rows, _ = customers_data(store)
    rows = cust_filter(rows)
    hdr = ["Клиент", "Телефон", "Карта", "День рождения", "Уровень", "Сегмент", "Покупок", "LTV (сумма покупок)", "За 12 мес.",
           "Средний чек", "Частота, дней", "Последняя покупка", "Бонусы", "Долг", "Магазины"]
    return _xlsx("Клиенты", hdr, [[r["name"], r["phone"], r["card"], r["birthday"], r["level"], SEGMENTS[r["segment"]], r["orders"], r["ltv"],
                                   r["net365"], r["avg"], r["freq"], (r["last"] or "")[:10], r["bonus"], r["debt"], ", ".join(r["stores"])]
                                  for r in rows], "customers")


def register(hq_module):
    global H
    H = hq_module
    c = H.con()
    sch = SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY").replace(" REAL", " DOUBLE PRECISION") if H.PG else SCHEMA
    c.executescript(sch)
    c.commit()
    c.close()
    H.app.register_blueprint(bp)
