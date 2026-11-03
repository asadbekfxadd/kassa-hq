"""
Касса и учёт для магазина (white-label).
Запуск: python app.py  ->  http://127.0.0.1:5050
"""
import csv
import io
import os
import secrets
import sys
import threading
import time
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, jsonify, request, session, send_from_directory, render_template, Response, abort

import db
import fiscal

app = Flask(__name__, static_folder="static", template_folder="templates")

# ---------------------------------------------------------------- secret key
_key_file = os.path.join(db.DATA_DIR, "secret.key")
os.makedirs(db.DATA_DIR, exist_ok=True)
if not os.path.exists(_key_file):
    with open(_key_file, "w") as f:
        f.write(secrets.token_hex(32))
with open(_key_file) as f:
    app.secret_key = f.read().strip()
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=16)

FISCAL_LOCK = threading.Lock()


# ---------------------------------------------------------------- helpers
def q(sql, args=(), one=False):
    con = db.connect()
    cur = con.execute(sql, args)
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return (rows[0] if rows else None) if one else rows


def err(msg, code=400):
    return jsonify({"error": msg}), code


def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    return q("SELECT id,name,role FROM users WHERE id=? AND active=1", (uid,), one=True)


def login_required(fn):
    @wraps(fn)
    def w(*a, **kw):
        if not current_user():
            return err("Требуется вход", 401)
        return fn(*a, **kw)
    return w


def admin_required(fn):
    @wraps(fn)
    def w(*a, **kw):
        u = current_user()
        if not u:
            return err("Требуется вход", 401)
        if u["role"] != "admin":
            return err("Доступ только для администратора", 403)
        return fn(*a, **kw)
    return w


def num(v, default=0.0):
    try:
        if v is None or v == "":
            return default
        return float(str(v).replace(" ", "").replace(",", "."))
    except ValueError:
        return default


def money(v):
    return round(num(v), 2)


def open_shift(con=None):
    own = con is None
    con = con or db.connect()
    r = con.execute("SELECT * FROM shifts WHERE status='open' ORDER BY id DESC LIMIT 1").fetchone()
    r = dict(r) if r else None
    if own:
        con.close()
    return r


# ---------------------------------------------------------------- pages
@app.route("/")
def index():
    # index.html — чистый HTML/JS, отдаём как есть (без Jinja)
    return send_from_directory(os.path.join(db.BASE_DIR, "templates"), "index.html")


# ---------------------------------------------------------------- auth
@app.post("/api/login")
def login():
    pin = str((request.json or {}).get("pin", "")).strip()
    u = q("SELECT id,name,role FROM users WHERE pin=? AND active=1", (pin,), one=True)
    if not u:
        extras.audit("Неудачная попытка входа", "", 1, 0)
        return err("Неверный PIN", 401)
    session.permanent = True
    session["uid"] = u["id"]
    extras.audit("Вход в систему", u["name"], 0, u["id"])
    return jsonify(u)


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
def me():
    u = current_user()
    if not u:
        return err("Требуется вход", 401)
    s = db.get_settings()
    public = {k: v for k, v in s.items() if k != "fiscal_pos_secret"}
    return jsonify(user=u, settings=public, shift=open_shift())


# ---------------------------------------------------------------- settings & users
@app.get("/api/settings")
@admin_required
def get_settings():
    s = db.get_settings()
    s["fiscal_pos_secret"] = "••••" if s.get("fiscal_pos_secret") else ""
    return jsonify(s)


@app.post("/api/settings")
@admin_required
def save_settings():
    data = request.json or {}
    con = db.connect()
    for k, v in data.items():
        if k not in db.DEFAULT_SETTINGS:
            continue
        if k == "fiscal_pos_secret" and v == "••••":
            continue
        con.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (k, str(v)))
    con.commit()
    con.close()
    return jsonify(ok=True)


@app.get("/api/users")
@admin_required
def users_list():
    return jsonify(q("SELECT id,name,role,active FROM users ORDER BY id"))


@app.post("/api/users")
@admin_required
def users_save():
    d = request.json or {}
    name, pin, role = (d.get("name") or "").strip(), str(d.get("pin") or "").strip(), d.get("role", "cashier")
    if not name:
        return err("Укажите имя")
    con = db.connect()
    try:
        if d.get("id"):
            if pin:
                con.execute("UPDATE users SET name=?,pin=?,role=?,active=? WHERE id=?",
                            (name, pin, role, int(d.get("active", 1)), d["id"]))
            else:
                con.execute("UPDATE users SET name=?,role=?,active=? WHERE id=?",
                            (name, role, int(d.get("active", 1)), d["id"]))
        else:
            if len(pin) < 4:
                return err("PIN минимум 4 цифры")
            con.execute("INSERT INTO users(name,pin,role) VALUES(?,?,?)", (name, pin, role))
        con.commit()
    except Exception:
        return err("Такой PIN уже занят")
    finally:
        con.close()
    return jsonify(ok=True)


# ---------------------------------------------------------------- categories & products
@app.get("/api/categories")
@login_required
def categories():
    return jsonify(q("SELECT * FROM categories ORDER BY name"))


@app.post("/api/categories")
@admin_required
def categories_add():
    name = ((request.json or {}).get("name") or "").strip()
    if not name:
        return err("Пустое название")
    con = db.connect()
    con.execute("INSERT OR IGNORE INTO categories(name) VALUES(?)", (name,))
    con.commit()
    r = dict(con.execute("SELECT * FROM categories WHERE name=?", (name,)).fetchone())
    con.close()
    return jsonify(r)


PRODUCT_SELECT = """SELECT p.*, c.name AS category FROM products p
                    LEFT JOIN categories c ON c.id=p.category_id"""


@app.get("/api/products")
@login_required
def products():
    text = (request.args.get("q") or "").strip()
    where, args = ["p.active=1"], []
    if text:
        where.append("(ulower(p.name) LIKE ? OR p.barcode LIKE ? OR ulower(p.sku) LIKE ?)")
        args += [f"%{text.lower()}%"] * 3
    if request.args.get("low"):
        where.append("p.stock <= p.min_stock")
    if request.args.get("cat"):
        where.append("p.category_id=?")
        args.append(request.args["cat"])
    limit = min(int(request.args.get("limit", 500)), 5000)
    sql = PRODUCT_SELECT + " WHERE " + " AND ".join(where) + f" ORDER BY p.name LIMIT {limit}"
    return jsonify(q(sql, args))


@app.get("/api/products/barcode/<code>")
@login_required
def product_by_barcode(code):
    code = code.strip()
    con = db.connect()
    gp, extra = grocery.find_by_scan(con, code)
    con.close()
    if gp:
        p = q(PRODUCT_SELECT + " WHERE p.id=?", (gp["id"],), one=True)
        p.update(extra)
        return jsonify(p)
    if extra and extra.get("marking_unknown"):
        return err(f"Код маркировки распознан, но товара со штрихкодом {extra['marking_unknown']} нет в базе", 404)
    p = q(PRODUCT_SELECT + " WHERE p.active=1 AND (p.barcode=? OR p.sku=?)", (code, code), one=True)
    if not p and len(code) == 13 and code.isdigit():
        # весовой штрихкод: PP CCCCC WWWWW K  (вес в граммах)
        prefixes = [x.strip() for x in (db.get_settings().get("weight_prefixes") or "").split(",") if x.strip()]
        if code[:2] in prefixes:
            plu, grams = code[2:7], int(code[7:12])
            p = q(PRODUCT_SELECT + " WHERE p.active=1 AND (p.barcode=? OR p.sku=? OR p.barcode=? OR p.sku=?)",
                  (plu, plu, plu.lstrip("0"), plu.lstrip("0")), one=True)
            if p:
                p["scan_qty"] = round(grams / 1000, 3)
    if not p:
        return err("Товар не найден", 404)
    return jsonify(p)


PRODUCT_FIELDS = ["name", "barcode", "sku", "category_id", "unit_name", "units_code", "spic", "package_code",
                  "owner_type", "vat_percent", "cost_price", "price", "min_stock", "requires_marking", "restrict_kind",
                  "shelf_days", "quick", "supplier_id", "wholesale_price", "brand"]


def _clean_product(d):
    out = {}
    for k in PRODUCT_FIELDS:
        if k not in d:
            continue
        v = d[k]
        if k in ("cost_price", "price", "min_stock", "wholesale_price"):
            v = num(v)
        elif k in ("units_code", "owner_type", "vat_percent", "requires_marking", "restrict_kind", "shelf_days", "quick"):
            v = int(num(v))
        elif k in ("category_id", "supplier_id"):
            v = int(v) if v not in (None, "", 0, "0") else None
        else:
            v = (str(v).strip() if v is not None else "")
            if k == "barcode" and not v:
                v = None
        out[k] = v
    return out


@app.post("/api/products")
@admin_required
def product_create():
    d = request.json or {}
    p = _clean_product(d)
    if not p.get("name"):
        return err("Укажите название")
    if p.get("barcode") is None and d.get("auto_barcode"):
        p["barcode"] = _gen_barcode()
    stock = num(d.get("stock"))
    con = db.connect()
    try:
        cols = ",".join(p.keys())
        cur = con.execute(f"INSERT INTO products({cols}) VALUES({','.join('?' * len(p))})", list(p.values()))
        pid = cur.lastrowid
        if stock:
            con.execute("UPDATE products SET stock=? WHERE id=?", (stock, pid))
            con.execute("INSERT INTO stock_moves(product_id,created_at,type,qty,user_id,note) VALUES(?,?,?,?,?,?)",
                        (pid, db.now(), "inventory", stock, session.get("uid"), "Начальный остаток"))
        con.commit()
    except Exception as e:
        con.close()
        return err("Штрихкод уже существует" if "UNIQUE" in str(e) else str(e))
    con.close()
    return jsonify(q(PRODUCT_SELECT + " WHERE p.id=?", (pid,), one=True))


@app.put("/api/products/<int:pid>")
@admin_required
def product_update(pid):
    p = _clean_product(request.json or {})
    if not p:
        return err("Нет данных")
    if network.connected():
        row = q("SELECT hq FROM products WHERE id=?", (pid,), one=True)
        if row and row["hq"]:
            p = {k: v for k, v in p.items() if k in ("min_stock", "quick", "supplier_id")}
            if not p:
                return err("Каталог управляется Центром — измените товар там. Здесь можно менять мин. остаток, быструю кнопку и поставщика.")
    con = db.connect()
    old = con.execute("SELECT name, price FROM products WHERE id=?", (pid,)).fetchone()
    if old and "price" in p and abs((old["price"] or 0) - p["price"]) > 0.001:
        extras.audit("Изменение цены", f"{old['name']}: {old['price']:g} → {p['price']:g}", 1, con=con)
    try:
        sets = ",".join(f"{k}=?" for k in p)
        con.execute(f"UPDATE products SET {sets}, updated_at=? WHERE id=?", list(p.values()) + [db.now(), pid])
        con.commit()
    except Exception as e:
        con.close()
        return err("Штрихкод уже существует" if "UNIQUE" in str(e) else str(e))
    con.close()
    return jsonify(q(PRODUCT_SELECT + " WHERE p.id=?", (pid,), one=True))


@app.delete("/api/products/<int:pid>")
@admin_required
def product_delete(pid):
    con = db.connect()
    con.execute("UPDATE products SET active=0, barcode=NULL WHERE id=?", (pid,))
    con.commit()
    con.close()
    return jsonify(ok=True)


def _gen_barcode():
    """Внутренний EAN-13 с префиксом 200 (для товаров без заводского штрихкода)."""
    while True:
        base = "200" + "".join(str(secrets.randbelow(10)) for _ in range(9))
        s = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(base))
        code = base + str((10 - s % 10) % 10)
        if not q("SELECT 1 FROM products WHERE barcode=?", (code,), one=True):
            return code


IMPORT_MAP = {
    "name": ["name", "наименование", "название", "товар", "номенклатура"],
    "barcode": ["barcode", "штрихкод", "штрих-код", "шк", "ean"],
    "sku": ["sku", "артикул", "код"],
    "price": ["price", "цена", "цена продажи", "розничная цена"],
    "cost_price": ["cost_price", "cost", "себестоимость", "закупочная цена", "закупка"],
    "stock": ["stock", "остаток", "количество", "кол-во"],
    "category": ["category", "категория", "группа"],
    "spic": ["spic", "икпу", "mxik"],
    "package_code": ["package_code", "код упаковки"],
    "units_code": ["units_code", "код единицы"],
    "vat_percent": ["vat_percent", "ндс", "ндс %", "vat"],
    "unit_name": ["unit_name", "ед", "ед. изм", "единица"],
    "min_stock": ["min_stock", "мин. остаток", "минимальный остаток"],
}


def _rows_from_upload(f):
    name = (f.filename or "").lower()
    raw = f.read()
    if name.endswith((".xlsx", ".xlsm")):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
        ws = wb.active
        it = ws.iter_rows(values_only=True)
        head = [str(h or "").strip().lower() for h in next(it)]
        return [dict(zip(head, r)) for r in it]
    text = raw.decode("utf-8-sig", errors="ignore")
    if text.count(";") > text.count(","):
        dialect = ";"
    else:
        dialect = ","
    rd = csv.DictReader(io.StringIO(text), delimiter=dialect)
    return [{(k or "").strip().lower(): v for k, v in r.items()} for r in rd]


@app.post("/api/products/import")
@admin_required
def product_import():
    if network.connected():
        return err("Магазин подключён к сети — импорт каталога делается в Центре")
    f = request.files.get("file")
    if not f:
        return err("Файл не выбран")
    try:
        rows = _rows_from_upload(f)
    except Exception as e:
        return err("Не удалось прочитать файл: " + str(e))
    con = db.connect()
    s = db.get_settings(con)
    created = updated = 0
    for r in rows:
        rec = {}
        for field, aliases in IMPORT_MAP.items():
            for a in aliases:
                if a in r and r[a] not in (None, ""):
                    rec[field] = r[a]
                    break
        if not rec.get("name"):
            continue
        cat_id = None
        if rec.get("category"):
            cname = str(rec["category"]).strip()
            con.execute("INSERT OR IGNORE INTO categories(name) VALUES(?)", (cname,))
            cat_id = con.execute("SELECT id FROM categories WHERE name=?", (cname,)).fetchone()["id"]
        barcode = str(rec.get("barcode") or "").strip().split(".")[0] or None
        vals = {
            "name": str(rec["name"]).strip(),
            "barcode": barcode,
            "sku": str(rec.get("sku") or "").strip(),
            "price": num(rec.get("price")),
            "cost_price": num(rec.get("cost_price")),
            "category_id": cat_id,
            "spic": str(rec.get("spic") or "").strip().split(".")[0],
            "package_code": str(rec.get("package_code") or "").strip().split(".")[0],
            "units_code": int(num(rec.get("units_code"))),
            "vat_percent": int(num(rec.get("vat_percent"), num(s.get("default_vat"), 12))),
            "unit_name": str(rec.get("unit_name") or "шт"),
            "min_stock": num(rec.get("min_stock")),
        }
        existing = None
        if barcode:
            existing = con.execute("SELECT id,stock FROM products WHERE barcode=?", (barcode,)).fetchone()
        if not existing:
            existing = con.execute("SELECT id,stock FROM products WHERE name=? AND active=1", (vals["name"],)).fetchone()
        if existing:
            pid = existing["id"]
            sets = ",".join(f"{k}=?" for k in vals)
            con.execute(f"UPDATE products SET {sets},active=1,updated_at=? WHERE id=?", list(vals.values()) + [db.now(), pid])
            old_stock = existing["stock"]
            updated += 1
        else:
            cols = ",".join(vals)
            pid = con.execute(f"INSERT INTO products({cols}) VALUES({','.join('?' * len(vals))})", list(vals.values())).lastrowid
            old_stock = 0
            created += 1
        if "stock" in rec:
            new_stock = num(rec["stock"])
            diff = new_stock - old_stock
            if diff:
                con.execute("UPDATE products SET stock=? WHERE id=?", (new_stock, pid))
                con.execute("INSERT INTO stock_moves(product_id,created_at,type,qty,user_id,note) VALUES(?,?,?,?,?,?)",
                            (pid, db.now(), "inventory", diff, session.get("uid"), "Импорт"))
    con.commit()
    con.close()
    return jsonify(created=created, updated=updated)


@app.get("/api/products/export.csv")
@admin_required
def product_export():
    rows = q(PRODUCT_SELECT + " WHERE p.active=1 ORDER BY p.name")
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["Наименование", "Штрихкод", "Артикул", "Категория", "Цена", "Себестоимость", "Остаток",
                "Мин. остаток", "ИКПУ", "Код упаковки", "Код единицы", "НДС", "Ед"])
    for p in rows:
        w.writerow([p["name"], p["barcode"] or "", p["sku"] or "", p["category"] or "", p["price"], p["cost_price"],
                    p["stock"], p["min_stock"], p["spic"], p["package_code"], p["units_code"], p["vat_percent"],
                    p["unit_name"]])
    return Response("\ufeff" + out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=products.csv"})


@app.get("/api/products/<int:pid>/moves")
@admin_required
def product_moves(pid):
    return jsonify(q("""SELECT m.*, u.name AS user FROM stock_moves m LEFT JOIN users u ON u.id=m.user_id
                        WHERE product_id=? ORDER BY m.id DESC LIMIT 200""", (pid,)))


# ---------------------------------------------------------------- shifts
@app.get("/api/shift/current")
@login_required
def shift_current():
    return jsonify(open_shift())


@app.post("/api/shift/open")
@login_required
def shift_open():
    if open_shift():
        return err("Смена уже открыта")
    d = request.json or {}
    s = db.get_settings()
    zstatus = "off"
    if s.get("fiscal_mode") != "off":
        try:
            cl = fiscal.get_client(s)
            with FISCAL_LOCK:
                try:
                    cl.sync_state()
                except fiscal.FiscalError:
                    pass
                cl.zreport_open()
            zstatus = "ok"
        except fiscal.FiscalError as e:
            zstatus = "error: " + str(e)
    con = db.connect()
    sid = con.execute("INSERT INTO shifts(user_id,opened_at,opening_cash,zreport_status) VALUES(?,?,?,?)",
                      (session["uid"], db.now(), money(d.get("opening_cash")), zstatus)).lastrowid
    con.commit()
    con.close()
    return jsonify(id=sid, zreport_status=zstatus)


def shift_report(sid):
    sh = q("SELECT s.*, u.name AS user FROM shifts s LEFT JOIN users u ON u.id=s.user_id WHERE s.id=?", (sid,), one=True)
    if not sh:
        return None
    agg = q("""SELECT operation, COUNT(*) n, COALESCE(SUM(total),0) total, COALESCE(SUM(cash),0) cash,
                      COALESCE(SUM(card),0) card, COALESCE(SUM(qr),0) qr, COALESCE(SUM(discount),0) discount
               FROM sales WHERE shift_id=? GROUP BY operation""", (sid,))
    a = {r["operation"]: r for r in agg}
    sale = a.get("sale", {"n": 0, "total": 0, "cash": 0, "card": 0, "qr": 0, "discount": 0})
    ref = a.get("refund", {"n": 0, "total": 0, "cash": 0, "card": 0, "qr": 0, "discount": 0})
    ops = extras.cash_ops_totals(sid)
    debt = q("""SELECT COALESCE(SUM(CASE WHEN operation='sale' THEN debt ELSE -debt END),0) v FROM sales WHERE shift_id=?""",
             (sid,), one=True)["v"]
    expected_cash = ((sh["opening_cash"] or 0) + sale["cash"] - ref["cash"] + ops["cin"] - ops["cout"]
                     - ops["expense"] + ops["debt_cash"])
    unfiscal = q("SELECT COUNT(*) n FROM sales WHERE shift_id=? AND fiscal_status IN ('pending','error')", (sid,), one=True)["n"]
    return {"shift": sh, "sales": sale, "refunds": ref, "net": sale["total"] - ref["total"],
            "expected_cash": round(expected_cash, 2), "unfiscal": unfiscal, "ops": ops, "debt": debt}


@app.get("/api/shift/<int:sid>/report")
@login_required
def shift_report_api(sid):
    r = shift_report(sid)
    return jsonify(r) if r else err("Смена не найдена", 404)


@app.post("/api/shift/close")
@login_required
def shift_close():
    sh = open_shift()
    if not sh:
        return err("Нет открытой смены")
    d = request.json or {}
    s = db.get_settings()
    zstatus = "off"
    if s.get("fiscal_mode") != "off":
        retry_pending_fiscal()
        try:
            cl = fiscal.get_client(s)
            with FISCAL_LOCK:
                cl.zreport_close()
                try:
                    cl.sync_receipts()
                    cl.sync_zreports()
                except fiscal.FiscalError:
                    pass  # отправится фоновой синхронизацией
            zstatus = "ok"
        except fiscal.FiscalError as e:
            if not d.get("force"):
                return err("Не удалось закрыть Z-отчёт в ФМ: " + str(e) +
                           ". Проверьте ФМ и повторите, либо закройте смену принудительно.", 409)
            zstatus = "error: " + str(e)
    con = db.connect()
    con.execute("UPDATE shifts SET closed_at=?, closing_cash=?, status='closed', zreport_status=? WHERE id=?",
                (db.now(), money(d.get("closing_cash")), zstatus, sh["id"]))
    con.commit()
    con.close()
    db.backup_db()
    rep = shift_report(sh["id"])
    diff = money(d.get("closing_cash")) - rep["expected_cash"]
    if abs(diff) >= 1:
        extras.audit("Расхождение кассы при закрытии смены", f"{diff:+,.0f}".replace(",", " "), 2 if abs(diff) >= 50000 else 1)
    if s.get("tg_notify_shift") == "1":
        u = current_user()
        extras.tg_send(f"🔒 Смена №{sh['id']} закрыта — {extras._h(u['name'])}\n"
                       f"Выручка: <b>{extras.fmt(rep['net'])}</b> · чеков {rep['sales']['n']}\n"
                       f"Наличных должно быть {extras.fmt(rep['expected_cash'])}, сдано {extras.fmt(money(d.get('closing_cash')))}"
                       + (f"\n❗ Расхождение: {extras.fmt(diff)}" if abs(diff) >= 1 else " ✅")
                       + (f"\n⚠️ Не фискализировано чеков: {rep['unfiscal']}" if rep["unfiscal"] else ""), s)
    updater.on_shift_closed()
    return jsonify(rep)


@app.get("/api/shifts")
@admin_required
def shifts_list():
    return jsonify(q("""SELECT s.*, u.name AS user,
        (SELECT COALESCE(SUM(CASE WHEN operation='sale' THEN total ELSE -total END),0) FROM sales WHERE shift_id=s.id) AS net
        FROM shifts s LEFT JOIN users u ON u.id=s.user_id ORDER BY s.id DESC LIMIT 100"""))


# ---------------------------------------------------------------- sales
def _load_sale(sid, con=None):
    own = con is None
    con = con or db.connect()
    s = con.execute("SELECT s.*, u.name AS cashier FROM sales s LEFT JOIN users u ON u.id=s.user_id WHERE s.id=?",
                    (sid,)).fetchone()
    if not s:
        if own:
            con.close()
        return None
    s = dict(s)
    s["items"] = [dict(r) for r in con.execute("SELECT * FROM sale_items WHERE sale_id=? ORDER BY id", (sid,))]
    if own:
        con.close()
    return s


def fiscalize_sale(sid):
    """Фискализирует чек. Безопасно вызывать повторно: при наличии TXID ФМ не создаст дубликат."""
    s = db.get_settings()
    if s.get("fiscal_mode") == "off":
        return None
    cl = fiscal.get_client(s)
    con = db.connect()
    sale = _load_sale(sid, con)
    if not sale or sale["fiscal_status"] == "ok":
        con.close()
        return sale
    refund_of = None
    if sale["operation"] == "refund" and sale["refund_of"]:
        refund_of = dict(con.execute("SELECT * FROM sales WHERE id=?", (sale["refund_of"],)).fetchone())
        if refund_of["fiscal_status"] != "ok":
            con.execute("UPDATE sales SET fiscal_status='error', fiscal_error=? WHERE id=?",
                        ("Исходный чек не фискализирован", sid))
            con.commit()
            con.close()
            return _load_sale(sid)
    try:
        with FISCAL_LOCK:
            txid = sale["fiscal_txid"]
            if not txid:
                rec = fiscal.build_receipt_json(sale, sale["items"], s, refund_of)
                import json as _j
                txid = cl.get_txid(rec)
                con.execute("UPDATE sales SET fiscal_txid=?, fiscal_json=?, fiscal_status='pending' WHERE id=?",
                            (txid, _j.dumps(rec, ensure_ascii=False), sid))
                con.commit()
            res = cl.register_txid(txid)
        con.execute("""UPDATE sales SET fiscal_status='ok', terminal_id=?, receipt_seq=?, fiscal_datetime=?,
                       fiscal_sign=?, qr_url=?, fiscal_error=NULL WHERE id=?""",
                    (res.get("TerminalID"), res.get("ReceiptSeq"), res.get("DateTime"), res.get("FiscalSign"),
                     res.get("QRCodeURL"), sid))
    except fiscal.FiscalError as e:
        con.execute("UPDATE sales SET fiscal_status='error', fiscal_error=? WHERE id=?", (str(e), sid))
        if sale["fiscal_status"] != "error":   # уведомляем один раз на чек
            con.commit()
            extras.audit("Чек не фискализирован", f"№{sid}: {e}", 2)
    con.commit()
    con.close()
    return _load_sale(sid)


@app.post("/api/sales")
@login_required
def sale_create():
    d = request.json or {}
    items = d.get("items") or []
    if not items:
        return err("Корзина пуста")
    con = db.connect()
    sh = open_shift(con)
    if not sh:
        con.close()
        return err("Откройте смену", 409)
    s = db.get_settings(con)
    allow_neg = s.get("allow_negative_stock") == "1"

    lines = []
    for it in items:
        p = con.execute("SELECT * FROM products WHERE id=?", (it.get("product_id"),)).fetchone()
        if not p:
            con.close()
            return err("Товар не найден")
        qty = num(it.get("qty"), 1)
        if qty <= 0:
            continue
        if not allow_neg and p["stock"] < qty:
            con.close()
            return err(f"Недостаточно остатка: {p['name']} (есть {p['stock']:g})")
        price = money(it.get("price", p["price"]))
        disc = min(money(it.get("discount")), round(qty * price, 2))
        lines.append({"p": dict(p), "qty": qty, "price": price, "discount": disc,
                      "marking": [str(c).strip() for c in (it.get("marking") or []) if str(c).strip()]})
    if not lines:
        con.close()
        return err("Корзина пуста")

    subtotal = round(sum(l["qty"] * l["price"] for l in lines), 2)
    me = current_user()
    rule = grocery.check_sale_rules(con, lines, s, me)
    if rule:
        con.close()
        return err(rule, 422)

    # --- клиент, бонусы, насия
    cust = None
    if d.get("customer_id"):
        cust = con.execute("SELECT * FROM customers WHERE id=?", (d["customer_id"],)).fetchone()
        if not cust:
            con.close()
            return err("Клиент не найден")
    manual_disc = money(d.get("discount")) + sum(l["discount"] for l in lines)
    # контроль скидок кассира
    limit = num(s.get("cashier_max_discount"), 100)
    pers = cust["discount_pct"] if cust else 0
    if me["role"] != "admin" and subtotal and manual_disc / subtotal * 100 > max(limit, pers) + 0.01:
        if not extras.is_admin_pin(d.get("approve_pin")):
            con.close()
            return jsonify(error=f"Скидка больше {max(limit, pers):g}% — нужно подтверждение администратора",
                           need_admin=True), 403
        extras.audit("Скидка выше лимита подтверждена", f"{manual_disc:g} из {subtotal:g}", 1, con=con)
    # ручная цена (не розница и не опт) — только с подтверждением
    odd = [l for l in lines if not pricing.price_allowed(l["p"], l["price"])]
    if odd and me["role"] != "admin":
        if not extras.is_admin_pin(d.get("approve_pin")):
            con.close()
            return jsonify(error=f"Цена «{odd[0]['p']['name']}» изменена вручную — нужно подтверждение администратора",
                           need_admin=True), 403
        extras.audit("Ручная цена подтверждена", ", ".join(f"{l['p']['name']}: {l['p']['price']:g}→{l['price']:g}" for l in odd), 1, con=con)
    # акции считаются на сервере и не входят в лимит скидки кассира
    pricing.apply_promos(con, lines)
    bonus_used = money(d.get("bonus"))
    if bonus_used:
        if not cust:
            con.close()
            return err("Бонусы списываются только с клиента")
        max_b = (subtotal - manual_disc) * num(s.get("bonus_max_pay_pct"), 50) / 100
        if bonus_used > cust["bonus"] + 0.001:
            con.close()
            return err(f"На счёте клиента только {cust['bonus']:g} бонусов")
        if bonus_used > max_b + 0.001:
            con.close()
            return err(f"Бонусами можно оплатить не больше {num(s.get('bonus_max_pay_pct'), 50):g}% чека")

    # скидка на чек (+ бонусы) распределяется пропорционально по позициям
    check_disc = money(d.get("discount")) + bonus_used
    if check_disc:
        base = sum(l["qty"] * l["price"] - l["discount"] for l in lines)
        check_disc = min(check_disc, base)
        left = check_disc
        for i, l in enumerate(lines):
            part = left if i == len(lines) - 1 else round(check_disc * (l["qty"] * l["price"] - l["discount"]) / base)
            part = min(part, l["qty"] * l["price"] - l["discount"])
            l["discount"] = round(l["discount"] + part, 2)
            left = round(left - part, 2)
    discount = round(sum(l["discount"] for l in lines), 2)
    total = round(subtotal - discount, 2)

    debt = min(money(d.get("debt")), total)
    if debt:
        if not cust:
            con.close()
            return err("Насия оформляется только на клиента")
        if cust["debt_limit"] and cust["debt"] + debt > cust["debt_limit"] + 0.001:
            con.close()
            return err(f"Превышен лимит долга клиента ({cust['debt_limit']:g}). Текущий долг {cust['debt']:g}")
    card = min(money(d.get("card")), round(total - debt, 2))
    qr = min(money(d.get("qr")), round(total - debt - card, 2))
    cash = round(total - card - qr - debt, 2)
    given = money(d.get("given")) if d.get("given") not in (None, "") else cash
    if given < cash - 0.001:
        con.close()
        return err("Получено меньше суммы к оплате")
    change = round(given - cash, 2)
    bonus_pct = num(s.get("bonus_pct"), 0)
    bonus_earned = round((total - debt) * bonus_pct / 100) if cust and bonus_pct > 0 else 0

    t = db.now()
    phone = (d.get("customer_phone") or (cust["phone"] if cust else "") or "").strip()
    sid = con.execute("""INSERT INTO sales(shift_id,user_id,created_at,operation,subtotal,discount,total,cash,card,
                         given,change,customer_phone,fiscal_status,customer_id,bonus_used,bonus_earned,debt,qr)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (sh["id"], session["uid"], t, "sale", subtotal, discount, total, cash, card, given, change,
                       phone, "none" if s.get("fiscal_mode") == "off" else "pending",
                       cust["id"] if cust else None, bonus_used, bonus_earned, debt, qr)).lastrowid
    if cust:
        if bonus_used:
            con.execute("UPDATE customers SET bonus=bonus-? WHERE id=?", (bonus_used, cust["id"]))
            con.execute("INSERT INTO customer_ops(customer_id,created_at,type,amount,sale_id,user_id) VALUES(?,?,?,?,?,?)",
                        (cust["id"], t, "bonus_use", bonus_used, sid, session["uid"]))
        if bonus_earned:
            con.execute("UPDATE customers SET bonus=bonus+? WHERE id=?", (bonus_earned, cust["id"]))
            con.execute("INSERT INTO customer_ops(customer_id,created_at,type,amount,sale_id,user_id) VALUES(?,?,?,?,?,?)",
                        (cust["id"], t, "bonus_add", bonus_earned, sid, session["uid"]))
        if debt:
            con.execute("UPDATE customers SET debt=debt+? WHERE id=?", (debt, cust["id"]))
            con.execute("INSERT INTO customer_ops(customer_id,created_at,type,amount,sale_id,user_id) VALUES(?,?,?,?,?,?)",
                        (cust["id"], t, "debt_add", debt, sid, session["uid"]))
    for l in lines:
        p = l["p"]
        con.execute("""INSERT INTO sale_items(sale_id,product_id,name,barcode,qty,price,discount,total,vat_percent,
                       cost_price,spic,package_code,units_code,owner_type,marking,promo) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (sid, p["id"], p["name"], p["barcode"], l["qty"], l["price"], l["discount"],
                     round(l["qty"] * l["price"] - l["discount"], 2), p["vat_percent"], p["cost_price"], p["spic"],
                     p["package_code"], p["units_code"], p["owner_type"], ",".join(l["marking"]) or None, l.get("promo")))
        con.execute("UPDATE products SET stock=stock-? WHERE id=?", (l["qty"], p["id"]))
        con.execute("INSERT INTO stock_moves(product_id,created_at,type,qty,ref_id,user_id) VALUES(?,?,?,?,?,?)",
                    (p["id"], t, "sale", -l["qty"], sid, session["uid"]))
    con.commit()
    con.close()

    sale = fiscalize_sale(sid) or _load_sale(sid)
    network.kick()
    return jsonify(sale)


@app.get("/api/sales")
@login_required
def sales_list():
    dfrom = request.args.get("from") or datetime.now().strftime("%Y-%m-%d")
    dto = request.args.get("to") or dfrom
    args = [dfrom + " 00:00:00", dto + " 23:59:59"]
    extra = ""
    if request.args.get("fiscal") == "problem":
        extra = " AND s.fiscal_status IN ('pending','error')"
    if request.args.get("q"):
        extra += " AND (CAST(s.id AS TEXT)=? OR CAST(s.receipt_seq AS TEXT)=?)"
        args += [request.args["q"]] * 2
    return jsonify(q(f"""SELECT s.id,s.created_at,s.operation,s.total,s.cash,s.card,s.fiscal_status,s.receipt_seq,
                         s.fiscal_error,s.refund_of,u.name AS cashier,
                         (SELECT COUNT(*) FROM sale_items WHERE sale_id=s.id) AS lines
                         FROM sales s LEFT JOIN users u ON u.id=s.user_id
                         WHERE s.created_at BETWEEN ? AND ? {extra} ORDER BY s.id DESC LIMIT 1000""", args))


@app.get("/api/sales/<int:sid>")
@login_required
def sale_get(sid):
    s = _load_sale(sid)
    if not s:
        return err("Чек не найден", 404)
    refunded = q("""SELECT si.product_id, SUM(si.qty) qty FROM sales s JOIN sale_items si ON si.sale_id=s.id
                    WHERE s.refund_of=? GROUP BY si.product_id""", (sid,))
    s["refunded"] = {str(r["product_id"]): r["qty"] for r in refunded}
    return jsonify(s)


@app.post("/api/sales/<int:sid>/fiscalize")
@login_required
def sale_refiscalize(sid):
    return jsonify(fiscalize_sale(sid))


@app.post("/api/sales/<int:sid>/refund")
@login_required
def sale_refund(sid):
    d = request.json or {}
    con = db.connect()
    sh = open_shift(con)
    if not sh:
        con.close()
        return err("Откройте смену", 409)
    orig = _load_sale(sid, con)
    if not orig or orig["operation"] != "sale":
        con.close()
        return err("Чек продажи не найден")
    s = db.get_settings(con)
    if s.get("fiscal_mode") != "off" and orig["fiscal_status"] != "ok":
        con.close()
        return err("Исходный чек не фискализирован — сначала повторите его фискализацию")
    if current_user()["role"] != "admin" and s.get("refund_needs_admin") == "1":
        if not extras.is_admin_pin(d.get("approve_pin")):
            con.close()
            return jsonify(error="Возврат подтверждает администратор", need_admin=True), 403
    already, already_disc = {}, {}
    for r in con.execute("""SELECT si.product_id, SUM(si.qty) qty, SUM(si.discount) disc FROM sales s
                            JOIN sale_items si ON si.sale_id=s.id WHERE s.refund_of=? GROUP BY si.product_id""", (sid,)):
        already[r["product_id"]] = r["qty"]
        already_disc[r["product_id"]] = r["disc"] or 0
    req = {int(r["item_id"]): num(r["qty"]) for r in (d.get("items") or []) if num(r.get("qty")) > 0}
    lines = []
    for it in orig["items"]:
        if it["id"] not in req:
            continue
        qty = req[it["id"]]
        avail = it["qty"] - already.get(it["product_id"], 0)
        if qty > avail + 1e-9:
            con.close()
            return err(f"Нельзя вернуть больше, чем продано: {it['name']} (доступно {avail:g})")
        if abs(qty - avail) < 1e-9:   # возвращается весь остаток строки — берём остаток скидки целиком
            disc = round(it["discount"] - already_disc.get(it["product_id"], 0), 2)
        else:
            disc = round(it["discount"] * qty / it["qty"])
        codes = [c for c in (it.get("marking") or "").split(",") if c]
        lines.append({**it, "qty": qty, "discount": disc, "total": round(qty * it["price"] - disc, 2),
                      "marking": ",".join(codes[:int(round(qty))]) if codes else None})
    if not lines:
        con.close()
        return err("Не выбраны позиции для возврата")
    total = round(sum(l["total"] for l in lines), 2)
    subtotal = round(sum(l["qty"] * l["price"] for l in lines), 2)
    ratio = total / orig["total"] if orig["total"] else 1
    cust = con.execute("SELECT * FROM customers WHERE id=?", (orig["customer_id"],)).fetchone() if orig.get("customer_id") else None
    # сначала гасим долг по этому чеку (деньги клиенту не выдаются)
    debt_back = 0
    if cust and orig.get("debt"):
        debt_back = round(min(orig["debt"] * ratio, cust["debt"], total), 2)
    rest = round(total - debt_back, 2)
    card = min(money(d.get("card", 0)), rest)
    if "card" not in d and (orig["card"] or orig.get("qr")):
        card = min(rest, orig["card"] + (orig.get("qr") or 0))
    cash = round(rest - card, 2)
    t = db.now()
    rid = con.execute("""INSERT INTO sales(shift_id,user_id,created_at,operation,refund_of,subtotal,discount,total,cash,card,
                         given,change,fiscal_status,customer_id,debt,bonus_used,bonus_earned)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (sh["id"], session["uid"], t, "refund", sid, subtotal, round(subtotal - total, 2), total, cash,
                       card, cash, 0, "none" if s.get("fiscal_mode") == "off" else "pending",
                       orig.get("customer_id"), debt_back,
                       round((orig.get("bonus_used") or 0) * ratio), round((orig.get("bonus_earned") or 0) * ratio))).lastrowid
    if cust:
        b_back = round((orig.get("bonus_used") or 0) * ratio)
        b_rev = round((orig.get("bonus_earned") or 0) * ratio)
        if debt_back:
            con.execute("UPDATE customers SET debt=debt-? WHERE id=?", (debt_back, cust["id"]))
            con.execute("INSERT INTO customer_ops(customer_id,created_at,type,amount,sale_id,user_id,note) VALUES(?,?,?,?,?,?,?)",
                        (cust["id"], t, "debt_back", debt_back, rid, session["uid"], "Возврат товара"))
        if b_back or b_rev:
            con.execute("UPDATE customers SET bonus=MAX(0,bonus+?-?) WHERE id=?", (b_back, b_rev, cust["id"]))
            con.execute("INSERT INTO customer_ops(customer_id,created_at,type,amount,sale_id,user_id,note) VALUES(?,?,?,?,?,?,?)",
                        (cust["id"], t, "bonus_back", b_back - b_rev, rid, session["uid"], "Возврат товара"))
    for l in lines:
        con.execute("""INSERT INTO sale_items(sale_id,product_id,name,barcode,qty,price,discount,total,vat_percent,
                       cost_price,spic,package_code,units_code,owner_type,marking) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (rid, l["product_id"], l["name"], l["barcode"], l["qty"], l["price"], l["discount"], l["total"],
                     l["vat_percent"], l["cost_price"], l["spic"], l["package_code"], l["units_code"], l["owner_type"],
                     l["marking"]))
        if d.get("return_to_stock", True):
            con.execute("UPDATE products SET stock=stock+? WHERE id=?", (l["qty"], l["product_id"]))
            con.execute("INSERT INTO stock_moves(product_id,created_at,type,qty,ref_id,user_id) VALUES(?,?,?,?,?,?)",
                        (l["product_id"], t, "refund", l["qty"], rid, session["uid"]))
    con.commit()
    con.close()
    big = num(s.get("big_refund_alert"), 0)
    extras.audit("Возврат по чеку №%d" % sid, f"сумма {total:g}", 2 if big and total >= big else 1)
    return jsonify(fiscalize_sale(rid) or _load_sale(rid))


# ---------------------------------------------------------------- purchases / writeoff / inventory
@app.post("/api/purchases")
@admin_required
def purchase_create():
    d = request.json or {}
    items = [i for i in (d.get("items") or []) if num(i.get("qty")) > 0]
    if not items:
        return err("Нет позиций")
    con = db.connect()
    t = db.now()
    total = round(sum(num(i["qty"]) * num(i.get("cost")) for i in items), 2)
    sup_id = None
    sup_name = (d.get("supplier") or "").strip()
    if d.get("supplier_id"):
        sup_id = int(d["supplier_id"])
        sup_name = con.execute("SELECT name FROM suppliers WHERE id=?", (sup_id,)).fetchone()["name"]
    elif sup_name:
        ex = con.execute("SELECT id FROM suppliers WHERE name=?", (sup_name,)).fetchone()
        sup_id = ex["id"] if ex else con.execute("INSERT INTO suppliers(name,created_at) VALUES(?,?)", (sup_name, t)).lastrowid
    paid = min(money(d.get("paid", total if not sup_id else 0)), total)
    pid_doc = con.execute("""INSERT INTO purchases(created_at,user_id,supplier,note,total,supplier_id,paid,due_date)
                             VALUES(?,?,?,?,?,?,?,?)""",
                          (t, session["uid"], sup_name, d.get("note", ""), total, sup_id, paid, d.get("due_date") or None)).lastrowid
    if sup_id:
        grocery.supplier_purchase(con, sup_id, total, paid, pid_doc, d.get("pay_method") or "cash")
    for i in items:
        pid, qty, cost = int(i["product_id"]), num(i["qty"]), num(i.get("cost"))
        p = con.execute("SELECT stock,cost_price FROM products WHERE id=?", (pid,)).fetchone()
        if not p:
            continue
        # средневзвешенная себестоимость
        old_q = max(p["stock"], 0)
        new_cost = cost
        if cost and old_q > 0 and p["cost_price"]:
            new_cost = round((old_q * p["cost_price"] + qty * cost) / (old_q + qty), 2)
        con.execute("INSERT INTO purchase_items(purchase_id,product_id,qty,cost) VALUES(?,?,?,?)", (pid_doc, pid, qty, cost))
        con.execute("UPDATE products SET stock=stock+?, cost_price=CASE WHEN ?>0 THEN ? ELSE cost_price END, updated_at=? WHERE id=?",
                    (qty, cost, new_cost, t, pid))
        if num(i.get("price")) > 0:
            con.execute("UPDATE products SET price=? WHERE id=?", (num(i["price"]), pid))
        con.execute("INSERT INTO stock_moves(product_id,created_at,type,qty,ref_id,user_id,note) VALUES(?,?,?,?,?,?,?)",
                    (pid, t, "purchase", qty, pid_doc, session["uid"], sup_name))
        grocery.add_batch(con, pid, qty, i.get("expiry") or None, f"Приход №{pid_doc}")
    con.commit()
    con.close()
    return jsonify(id=pid_doc, total=total)


@app.get("/api/purchases")
@admin_required
def purchases_list():
    return jsonify(q("""SELECT p.*, u.name AS user, (SELECT COUNT(*) FROM purchase_items WHERE purchase_id=p.id) lines
                        FROM purchases p LEFT JOIN users u ON u.id=p.user_id ORDER BY p.id DESC LIMIT 200"""))


@app.post("/api/writeoff")
@admin_required
def writeoff():
    d = request.json or {}
    con = db.connect()
    t = db.now()
    n = 0
    for i in d.get("items") or []:
        qty = num(i.get("qty"))
        if qty <= 0:
            continue
        con.execute("UPDATE products SET stock=stock-? WHERE id=?", (qty, i["product_id"]))
        con.execute("INSERT INTO stock_moves(product_id,created_at,type,qty,user_id,note) VALUES(?,?,?,?,?,?)",
                    (i["product_id"], t, "writeoff", -qty, session["uid"], d.get("note") or "Списание"))
        n += 1
    con.commit()
    con.close()
    return jsonify(lines=n)


@app.post("/api/inventory")
@admin_required
def inventory():
    """Принимает фактические остатки, проводит разницу."""
    d = request.json or {}
    con = db.connect()
    t = db.now()
    diffs = []
    for i in d.get("items") or []:
        p = con.execute("SELECT id,name,stock,cost_price FROM products WHERE id=?", (i["product_id"],)).fetchone()
        if not p:
            continue
        fact = num(i.get("counted"))
        diff = round(fact - p["stock"], 3)
        if diff:
            con.execute("UPDATE products SET stock=? WHERE id=?", (fact, p["id"]))
            con.execute("INSERT INTO stock_moves(product_id,created_at,type,qty,user_id,note) VALUES(?,?,?,?,?,?)",
                        (p["id"], t, "inventory", diff, session["uid"], d.get("note") or "Инвентаризация"))
        diffs.append({"name": p["name"], "was": p["stock"], "fact": fact, "diff": diff,
                      "sum": round(diff * (p["cost_price"] or 0), 2)})
    con.commit()
    con.close()
    return jsonify(diffs=diffs)


# ---------------------------------------------------------------- reports
@app.get("/api/reports/summary")
@admin_required
def report_summary():
    dfrom = request.args.get("from") or datetime.now().strftime("%Y-%m-%d")
    dto = request.args.get("to") or dfrom
    a = (dfrom + " 00:00:00", dto + " 23:59:59")

    tot = q("""SELECT
        COALESCE(SUM(CASE WHEN operation='sale' THEN total END),0) sales,
        COALESCE(SUM(CASE WHEN operation='refund' THEN total END),0) refunds,
        COALESCE(SUM(CASE WHEN operation='sale' THEN cash ELSE -cash END),0) cash,
        COALESCE(SUM(CASE WHEN operation='sale' THEN card ELSE -card END),0) card,
        COALESCE(SUM(CASE WHEN operation='sale' THEN discount END),0) discount,
        COUNT(CASE WHEN operation='sale' THEN 1 END) checks
        FROM sales WHERE created_at BETWEEN ? AND ?""", a, one=True)
    cost = q("""SELECT COALESCE(SUM(CASE WHEN s.operation='sale' THEN si.qty*si.cost_price ELSE -si.qty*si.cost_price END),0) c
                FROM sale_items si JOIN sales s ON s.id=si.sale_id WHERE s.created_at BETWEEN ? AND ?""", a, one=True)["c"]
    tot["net"] = round(tot["sales"] - tot["refunds"], 2)
    tot["cost"] = round(cost, 2)
    tot["profit"] = round(tot["net"] - cost, 2)
    tot["avg"] = round(tot["sales"] / tot["checks"], 2) if tot["checks"] else 0
    tot["margin"] = round(tot["profit"] / tot["net"] * 100, 1) if tot["net"] else 0

    by_day = q("""SELECT substr(created_at,1,10) d,
                  SUM(CASE WHEN operation='sale' THEN total ELSE -total END) net,
                  COUNT(CASE WHEN operation='sale' THEN 1 END) checks
                  FROM sales WHERE created_at BETWEEN ? AND ? GROUP BY d ORDER BY d""", a)
    by_hour = q("""SELECT CAST(substr(created_at,12,2) AS INTEGER) h, COUNT(*) checks, SUM(total) total
                   FROM sales WHERE operation='sale' AND created_at BETWEEN ? AND ? GROUP BY h ORDER BY h""", a)
    top = q("""SELECT si.name, SUM(CASE WHEN s.operation='sale' THEN si.qty ELSE -si.qty END) qty,
               SUM(CASE WHEN s.operation='sale' THEN si.total ELSE -si.total END) total,
               SUM(CASE WHEN s.operation='sale' THEN si.total-si.qty*si.cost_price ELSE -(si.total-si.qty*si.cost_price) END) profit
               FROM sale_items si JOIN sales s ON s.id=si.sale_id
               WHERE s.created_at BETWEEN ? AND ? GROUP BY si.product_id ORDER BY total DESC LIMIT 20""", a)
    by_cat = q("""SELECT COALESCE(c.name,'Без категории') AS cat_name,
                  SUM(CASE WHEN s.operation='sale' THEN si.total ELSE -si.total END) total
                  FROM sale_items si JOIN sales s ON s.id=si.sale_id
                  LEFT JOIN products p ON p.id=si.product_id LEFT JOIN categories c ON c.id=p.category_id
                  WHERE s.created_at BETWEEN ? AND ? GROUP BY cat_name ORDER BY total DESC""", a)
    for r in by_cat:
        r["name"] = r.pop("cat_name")
    by_cashier = q("""SELECT u.name, COUNT(CASE WHEN s.operation='sale' THEN 1 END) checks,
                      SUM(CASE WHEN s.operation='sale' THEN s.total ELSE -s.total END) total
                      FROM sales s LEFT JOIN users u ON u.id=s.user_id
                      WHERE s.created_at BETWEEN ? AND ? GROUP BY s.user_id ORDER BY total DESC""", a)
    low = q("""SELECT name, stock, min_stock, unit_name FROM products
               WHERE active=1 AND min_stock>0 AND stock<=min_stock ORDER BY stock-min_stock LIMIT 50""")
    stock_val = q("""SELECT COALESCE(SUM(CASE WHEN stock>0 THEN stock*cost_price END),0) cost,
                     COALESCE(SUM(CASE WHEN stock>0 THEN stock*price END),0) retail, COUNT(*) skus
                     FROM products WHERE active=1""", one=True)
    # товары без продаж 30 дней (неликвид)
    dead = q("""SELECT p.name, p.stock, p.stock*p.cost_price AS sum FROM products p
                WHERE p.active=1 AND p.stock>0 AND NOT EXISTS (
                  SELECT 1 FROM stock_moves m WHERE m.product_id=p.id AND m.type='sale' AND m.created_at>=?)
                ORDER BY sum DESC LIMIT 30""", ((datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"),))
    return jsonify(totals=tot, by_day=by_day, by_hour=by_hour, top=top, by_category=by_cat,
                   by_cashier=by_cashier, low_stock=low, stock_value=stock_val, dead_stock=dead)


# ---------------------------------------------------------------- fiscal panel
@app.get("/api/fiscal/status")
@login_required
def fiscal_status():
    s = db.get_settings()
    out = {"mode": s.get("fiscal_mode"),
           "problem_count": q("SELECT COUNT(*) n FROM sales WHERE fiscal_status IN ('pending','error')", one=True)["n"],
           "last_sync": LAST_SYNC}
    if s.get("fiscal_mode") == "off":
        return jsonify(out)
    try:
        cl = fiscal.get_client(s)
        with FISCAL_LOCK:
            out["memory"] = cl.memory_info()
        out["online"] = True
    except fiscal.FiscalError as e:
        out["online"] = False
        out["error"] = str(e)
    return jsonify(out)


@app.post("/api/fiscal/action")
@admin_required
def fiscal_action():
    action = (request.json or {}).get("action")
    s = db.get_settings()
    cl = fiscal.get_client(s)
    fn = {"list": cl.list_drives, "info": cl.info, "memory": cl.memory_info, "sync_state": cl.sync_state,
          "z_open": cl.zreport_open, "z_close": cl.zreport_close, "send": lambda: {
              "receipts": cl.sync_receipts(), "zreports": cl.sync_zreports()}, "pos_lock": cl.pos_lock,
          "retry": lambda: {"retried": retry_pending_fiscal()}}.get(action)
    if not fn:
        return err("Неизвестное действие")
    try:
        with FISCAL_LOCK if action != "retry" else _nolock():
            return jsonify(result=fn())
    except fiscal.FiscalError as e:
        return err(str(e))


class _nolock:
    def __enter__(self): return self
    def __exit__(self, *a): return False


# ---------------------------------------------------------------- receipt print
def qr_svg(data):
    try:
        import qrcode
        import qrcode.image.svg
        img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=6, border=1)
        buf = io.BytesIO()
        img.save(buf)
        svg = buf.getvalue().decode("utf-8")
        return svg[svg.find("<svg"):]
    except Exception:
        return ""


@app.get("/receipt/<int:sid>")
def receipt(sid):
    if not current_user():
        abort(401)
    sale = _load_sale(sid)
    if not sale:
        abort(404)
    s = db.get_settings()
    qr = qr_svg(sale["qr_url"]) if sale.get("qr_url") else ""
    for it in sale["items"]:
        vp = it["vat_percent"] or 0
        it["vat"] = round(it["total"] * vp / (100 + vp), 2) if vp else 0
    vat_total = round(sum(i["vat"] for i in sale["items"]), 2)
    cust = q("SELECT name, phone, bonus, debt FROM customers WHERE id=?", (sale["customer_id"],), one=True) \
        if sale.get("customer_id") else None
    return render_template("receipt.html", sale=sale, s=s, qr=qr, vat_total=vat_total, cust=cust,
                           auto=request.args.get("print") == "1")


@app.get("/shift-report/<int:sid>")
def shift_report_print(sid):
    if not current_user():
        abort(401)
    r = shift_report(sid)
    if not r:
        abort(404)
    return render_template("shift_report.html", r=r, s=db.get_settings(), auto=request.args.get("print") == "1")


# ---------------------------------------------------------------- backup
@app.post("/api/backup")
@admin_required
def backup_now():
    p = db.backup_db()
    return jsonify(path=p)


@app.get("/api/backup/download")
@admin_required
def backup_download():
    p = db.backup_db()
    return send_from_directory(os.path.dirname(p), os.path.basename(p), as_attachment=True)


# ---------------------------------------------------------------- background sync
LAST_SYNC = {"at": None, "result": None}


def retry_pending_fiscal():
    ids = [r["id"] for r in q("""SELECT id FROM sales WHERE fiscal_status IN ('pending','error')
                                 AND (fiscal_error IS NULL OR fiscal_error NOT LIKE 'Исходный чек%')
                                 ORDER BY id LIMIT 50""")]
    for i in ids:
        fiscalize_sale(i)
    return len(ids)


def background_worker():
    while True:
        try:
            s = db.get_settings()
            interval = max(1, int(num(s.get("sync_interval_min"), 5)))
            if s.get("fiscal_mode") != "off":
                retry_pending_fiscal()
                cl = fiscal.get_client(s)
                with FISCAL_LOCK:
                    r1 = cl.sync_receipts()
                    r2 = cl.sync_zreports()
                LAST_SYNC.update(at=db.now(), result={"receipts": r1, "zreports": r2}, error=None)
        except Exception as e:
            LAST_SYNC.update(at=db.now(), error=str(e))
            interval = 5
        time.sleep(interval * 60)


import extras  # noqa: E402
import reports2  # noqa: E402
import dash  # noqa: E402
import stock3  # noqa: E402
import network  # noqa: E402
import grocery  # noqa: E402
import updater  # noqa: E402
import pricing  # noqa: E402
extras.register(sys.modules[__name__])
pricing.register(sys.modules[__name__])
reports2.register(sys.modules[__name__])
dash.register(sys.modules[__name__])
stock3.register(sys.modules[__name__])
updater.register(sys.modules[__name__])
network.register(sys.modules[__name__])
grocery.register(sys.modules[__name__])


def start():
    updater.startup()          # применить скачанное обновление до запуска
    db.init_db()
    db.backup_db()
    threading.Thread(target=background_worker, daemon=True).start()
    threading.Thread(target=extras.tg_worker, daemon=True).start()
    threading.Thread(target=network.sync_worker, daemon=True).start()
    threading.Thread(target=updater.worker, daemon=True).start()


if __name__ == "__main__":
    start()
    port = int(os.environ.get("PORT", 5050))
    print(f"\n  Касса запущена: http://127.0.0.1:{port}  (версия {updater.local_version()})\n  PIN администратора по умолчанию: 0000\n")
    try:
        from waitress import serve
        serve(app, host="127.0.0.1", port=port, threads=8)
    except ImportError:
        app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
