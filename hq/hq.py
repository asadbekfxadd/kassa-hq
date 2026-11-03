"""
ЦЕНТР — облачный кабинет владельца сети магазинов.

Магазины (касса app.py) работают локально и раз в минуту синхронизируются сюда:
  вниз  -> каталог товаров и цены, накладные (поставки и перемещения) для приёмки, список магазинов
  вверх -> продажи, остатки, новые товары, принятые/отправленные накладные

Запуск локально:  HQ_PASSWORD=секрет python hq.py      -> http://127.0.0.1:8080
Railway:          переменные HQ_PASSWORD, DATA_DIR=/data (подключите Volume на /data)
"""
import csv
import io
import json
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, jsonify, request, session, send_from_directory, Response

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE, "data"))
DB = os.path.join(DATA_DIR, "hq.db")
os.makedirs(DATA_DIR, exist_ok=True)

app = Flask(__name__, static_folder=None)
_kf = os.path.join(DATA_DIR, "secret.key")
if not os.environ.get("SECRET_KEY") and not os.path.exists(_kf):
    open(_kf, "w").write(secrets.token_hex(32))
app.secret_key = os.environ.get("SECRET_KEY") or open(_kf).read().strip()
app.config.update(PERMANENT_SESSION_LIFETIME=timedelta(days=30), SESSION_COOKIE_SAMESITE="Lax")

SCHEMA = """
CREATE TABLE IF NOT EXISTS stores (
  id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT UNIQUE NOT NULL, name TEXT NOT NULL, address TEXT,
  token TEXT UNIQUE NOT NULL, created_at TEXT, last_seen TEXT, info TEXT, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT UNIQUE NOT NULL, name TEXT NOT NULL, barcode TEXT, sku TEXT,
  category TEXT, unit_name TEXT DEFAULT 'шт', units_code INTEGER DEFAULT 0, spic TEXT DEFAULT '',
  package_code TEXT DEFAULT '', owner_type INTEGER DEFAULT 0, vat_percent INTEGER DEFAULT 12,
  cost_price REAL DEFAULT 0, price REAL DEFAULT 0, min_stock REAL DEFAULT 0, active INTEGER DEFAULT 1,
  updated_at TEXT NOT NULL, created_by TEXT, requires_marking INTEGER DEFAULT 0, restrict_kind INTEGER DEFAULT 0,
  shelf_days INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_p_barcode ON products(barcode);
CREATE INDEX IF NOT EXISTS ix_p_upd ON products(updated_at);
CREATE TABLE IF NOT EXISTS stock (
  store_id INTEGER NOT NULL, product_uid TEXT NOT NULL, qty REAL NOT NULL, updated_at TEXT,
  PRIMARY KEY (store_id, product_uid));
CREATE TABLE IF NOT EXISTS sales (
  id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT UNIQUE NOT NULL, store_id INTEGER NOT NULL, local_id INTEGER,
  created_at TEXT, operation TEXT, total REAL, cash REAL, card REAL, debt REAL DEFAULT 0, discount REAL DEFAULT 0,
  cashier TEXT, fiscal_status TEXT, receipt_seq INTEGER);
CREATE INDEX IF NOT EXISTS ix_s_store_date ON sales(store_id, created_at);
CREATE TABLE IF NOT EXISTS sale_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT, sale_uid TEXT NOT NULL, product_uid TEXT, name TEXT, qty REAL,
  price REAL, total REAL, cost_price REAL);
CREATE INDEX IF NOT EXISTS ix_si_sale ON sale_items(sale_uid);
CREATE TABLE IF NOT EXISTS docs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT UNIQUE NOT NULL, number TEXT, type TEXT NOT NULL,
  status TEXT NOT NULL, from_store_id INTEGER, to_store_id INTEGER NOT NULL, supplier TEXT, note TEXT,
  created_at TEXT, created_by TEXT, sent_at TEXT, received_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS doc_lines (
  id INTEGER PRIMARY KEY AUTOINCREMENT, doc_uid TEXT NOT NULL, product_uid TEXT NOT NULL,
  qty REAL NOT NULL DEFAULT 0, qty_fact REAL, cost REAL DEFAULT 0, price REAL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_dl_doc ON doc_lines(doc_uid);
CREATE TABLE IF NOT EXISTS price_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT, product_uid TEXT NOT NULL, created_at TEXT, old_price REAL, new_price REAL,
  old_cost REAL, new_cost REAL, source TEXT, user_name TEXT);
CREATE INDEX IF NOT EXISTS ix_ph_uid ON price_history(product_uid);
CREATE TABLE IF NOT EXISTS money_ops (
  id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT UNIQUE NOT NULL, store_id INTEGER, created_at TEXT NOT NULL,
  kind TEXT NOT NULL, type TEXT, method TEXT, amount REAL NOT NULL DEFAULT 0, category TEXT, note TEXT,
  user_name TEXT, source TEXT);
CREATE INDEX IF NOT EXISTS ix_mo_date ON money_ops(created_at);
CREATE TABLE IF NOT EXISTS store_plans (
  store_id INTEGER NOT NULL, month TEXT NOT NULL, amount REAL NOT NULL DEFAULT 0, updated_at TEXT, PRIMARY KEY (store_id, month));
"""


try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo(os.environ.get("APP_TZ", "Asia/Tashkent"))
except Exception:      # нет базы часовых поясов — считаем UTC+5
    from datetime import timezone
    _TZ = timezone(timedelta(hours=5))


def tnow():
    """Текущее время Ташкента (сервер Railway работает в UTC)."""
    return datetime.now(_TZ).replace(tzinfo=None)


def now():
    return tnow().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- база: PostgreSQL (Railway) или SQLite (локально)
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
PG = DATABASE_URL.startswith("postgres")
if PG:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    POOL = ConnectionPool(DATABASE_URL, min_size=1, max_size=10, kwargs={"row_factory": dict_row}, open=True)


def _pg_sql(sql):
    return sql.replace("%", "%%").replace("?", "%s").replace(" LIKE ", " ILIKE ")


class _PgConn:
    """Тонкая обёртка: тот же интерфейс, что у sqlite3 (execute/commit/close), но поверх пула PostgreSQL."""
    def __init__(self):
        self._ctx = POOL.connection()
        self.c = self._ctx.__enter__()

    def execute(self, sql, args=()):
        return self.c.execute(_pg_sql(sql), tuple(args))

    def executescript(self, script):
        for stmt in script.split(";"):
            if stmt.strip():
                self.c.execute(stmt)

    def commit(self):
        self.c.commit()

    def rollback(self):
        self.c.rollback()

    def close(self):
        try:
            self.c.rollback()
        except Exception:
            pass
        self._ctx.__exit__(None, None, None)


def con():
    if PG:
        return _PgConn()
    c = sqlite3.connect(DB, timeout=20)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def q(sql, args=(), one=False):
    c = con()
    rows = [dict(r) for r in c.execute(sql, args).fetchall()]
    c.close()
    return (rows[0] if rows else None) if one else rows


def err(m, code=400):
    return jsonify(error=m), code


def num(v, d=0.0):
    try:
        return float(str(v).replace(" ", "").replace(",", ".")) if v not in (None, "") else d
    except ValueError:
        return d


def init():
    c = con()
    c.executescript(SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY").replace(" REAL", " DOUBLE PRECISION")
                    if PG else SCHEMA)
    c.commit()
    # новые колонки для уже существующей базы
    for col, typ in (("requires_marking", "INTEGER DEFAULT 0"), ("restrict_kind", "INTEGER DEFAULT 0"),
                     ("shelf_days", "INTEGER DEFAULT 0"), ("wholesale_price", "DOUBLE PRECISION DEFAULT 0" if PG else "REAL DEFAULT 0"),
                     ("stores.phone", "TEXT"), ("stores.manager_id", "INTEGER"), ("stores.registers", "INTEGER DEFAULT 1"),
                     ("stores.area", "DOUBLE PRECISION DEFAULT 0" if PG else "REAL DEFAULT 0"), ("stores.opened_on", "TEXT"),
                     ("stores.city", "TEXT"), ("brand", "TEXT"),
                     ("sales.qr", "DOUBLE PRECISION DEFAULT 0" if PG else "REAL DEFAULT 0"), ("sales.qr_provider", "TEXT"),
                     ("sales.promo_disc", "DOUBLE PRECISION DEFAULT 0" if PG else "REAL DEFAULT 0"),
                     ("sales.cashier_uid", "TEXT"), ("sales.customer_uid", "TEXT")):
        tbl, col = col.split(".") if "." in col else ("products", col)
        try:
            c.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {typ}")
            c.commit()
        except Exception:
            c.rollback() if hasattr(c, "rollback") else None
    c.close()


init()


# ================================================================ owner auth
def owner(fn):
    @wraps(fn)
    def w(*a, **k):
        if not session.get("owner"):
            return err("Требуется вход", 401)
        return fn(*a, **k)
    return w


@app.get("/")
def index():
    return send_from_directory(os.path.join(BASE, "templates"), "hq.html")


@app.get("/<any('hqdash.js','hqadmin.js','hqtransfers.js','hqanalytics.js','hqpeople.js'):name>")
def hq_js(name):
    return send_from_directory(os.path.join(BASE, "templates"), name, mimetype="application/javascript")


# ================================================================ stores
@app.get("/api/stores")
@owner
def stores():
    f, a = rbac.sf("s.id")
    rows = q(f"""SELECT s.*, u.name AS manager_name,
                 (SELECT COUNT(*) FROM user_stores us JOIN users uu ON uu.id=us.user_id WHERE us.store_id=s.id AND uu.active=1) employees
                 FROM stores s LEFT JOIN users u ON u.id=s.manager_id WHERE 1=1{f} ORDER BY s.id""", a)
    if not rbac.can("stores.edit"):
        for r in rows:
            r.pop("token", None)
    t = tnow()
    for r in rows:
        r["online"] = bool(r["last_seen"]) and (t - datetime.strptime(r["last_seen"], "%Y-%m-%d %H:%M:%S")).total_seconds() < 180
        r["info"] = json.loads(r["info"] or "{}")
    return jsonify(rows)


@app.post("/api/stores")
@owner
def store_save():
    d = request.json or {}
    name = (d.get("name") or "").strip()
    if not name:
        return err("Укажите название")
    if d.get("id") and not rbac.store_allowed(d["id"]):
        return err("Нет доступа к этому магазину", 403)
    extra = (d.get("phone", ""), int(num(d.get("manager_id"))) or None, max(1, int(num(d.get("registers"), 1))),
             num(d.get("area")), d.get("opened_on") or None, d.get("city", ""))
    c = con()
    if d.get("id"):
        old = dict(c.execute("SELECT active FROM stores WHERE id=?", (d["id"],)).fetchone() or {})
        c.execute("""UPDATE stores SET name=?, address=?, active=?, phone=?, manager_id=?, registers=?, area=?, opened_on=?, city=?
                     WHERE id=?""", (name, d.get("address", ""), int(d.get("active", 1))) + extra + (d["id"],))
        act = "Магазин деактивирован" if old.get("active") and not int(d.get("active", 1)) else "Изменён магазин"
        rbac.audit(act, name, d["id"], c=c)
    else:
        c.execute("""INSERT INTO stores(uid,name,address,token,created_at,phone,manager_id,registers,area,opened_on,city)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (uuid.uuid4().hex, name, d.get("address", ""), secrets.token_hex(20), now()) + extra)
        rbac.audit("Создан магазин", name, c=c)
    c.commit()
    c.close()
    return jsonify(ok=True)


@app.post("/api/stores/<int:sid>/token")
@owner
def store_new_token(sid):
    if not rbac.store_allowed(sid):
        return err("Нет доступа к этому магазину", 403)
    rbac.audit("Выпущен новый токен кассы", "", sid)
    c = con()
    c.execute("UPDATE stores SET token=? WHERE id=?", (secrets.token_hex(20), sid))
    c.commit()
    c.close()
    return jsonify(ok=True)


# ================================================================ catalog
PFIELDS = ["name", "barcode", "sku", "category", "unit_name", "units_code", "spic", "package_code", "owner_type",
           "vat_percent", "cost_price", "price", "min_stock", "active", "requires_marking", "restrict_kind", "shelf_days",
           "wholesale_price", "brand"]


def clean_p(d):
    out = {}
    for k in PFIELDS:
        if k in d:
            v = d[k]
            if k in ("cost_price", "price", "min_stock", "wholesale_price"):
                v = num(v)
            elif k in ("units_code", "owner_type", "vat_percent", "active", "requires_marking", "restrict_kind", "shelf_days"):
                v = int(num(v, 1 if k == "active" else 0))
            else:
                v = str(v or "").strip()
            out[k] = v
    if "barcode" in out and not out["barcode"]:
        out["barcode"] = None
    return out


@app.get("/api/products")
@owner
def products():
    t = (request.args.get("q") or "").strip()
    where, args = ["active=1"], []
    if t:
        where.append("(name LIKE ? OR barcode LIKE ? OR sku LIKE ?)")
        args += [f"%{t}%"] * 3
    sfx, sa = rbac.sf("s.store_id")
    rows = q(f"""SELECT p.*, (SELECT COALESCE(SUM(qty),0) FROM stock s WHERE s.product_uid=p.uid{sfx}) AS total_stock
                 FROM products p WHERE {' AND '.join(where)} ORDER BY name LIMIT 3000""", sa + tuple(args))
    if not rbac.can("finance"):        # себестоимость видят только роли с правом «Прибыль»
        for r in rows:
            r["cost_price"] = None
    return jsonify(rows)


def log_price(c, uid, old, new, source):
    """История цен Центра: пишем, если изменилась цена или себестоимость."""
    op, np_ = old.get("price"), new.get("price", old.get("price"))
    oc, nc = old.get("cost_price"), new.get("cost_price", old.get("cost_price"))
    if (op is None and np_ is None) or (abs((op or 0) - (np_ or 0)) < 0.001 and abs((oc or 0) - (nc or 0)) < 0.001 and old):
        return
    c.execute("""INSERT INTO price_history(product_uid,created_at,old_price,new_price,old_cost,new_cost,source,user_name)
                 VALUES(?,?,?,?,?,?,?,?)""", (uid, now(), op, np_, oc, nc, source, (rbac.user() or {}).get("name", "")))


@app.get("/api/products/<uid>/prices")
@owner
def product_prices(uid):
    rows = q("SELECT * FROM price_history WHERE product_uid=? ORDER BY id DESC LIMIT 200", (uid,))
    if not rbac.can("finance"):
        for r in rows:
            r["old_cost"] = r["new_cost"] = None
    return jsonify(rows)


@app.get("/api/products/gen_barcode")
@owner
def gen_barcode():
    """Внутренний EAN-13 с префиксом 200 (зона внутренней нумерации GS1) и контрольной цифрой."""
    while True:
        base = "200" + "".join(str(secrets.randbelow(10)) for _ in range(9))
        sm = sum(int(ch) * (3 if i % 2 else 1) for i, ch in enumerate(base))
        code = base + str((10 - sm % 10) % 10)
        if not q("SELECT 1 FROM products WHERE barcode=?", (code,), one=True):
            return jsonify(barcode=code)


@app.post("/api/products")
@owner
def product_save():
    d = request.json or {}
    p = clean_p(d)
    if not p.get("name"):
        return err("Укажите название")
    c = con()
    if p.get("barcode"):
        dup = c.execute("SELECT uid FROM products WHERE barcode=? AND active=1 AND uid<>?",
                        (p["barcode"], d.get("uid") or "")).fetchone()
        if dup:
            c.close()
            return err("Товар с таким штрихкодом уже есть")
    p["updated_at"] = now()
    if d.get("uid"):
        old = dict(c.execute("SELECT price, cost_price FROM products WHERE uid=?", (d["uid"],)).fetchone() or {})
        if not rbac.can("finance"):
            p.pop("cost_price", None)
        c.execute(f"UPDATE products SET {','.join(k + '=?' for k in p)} WHERE uid=?", list(p.values()) + [d["uid"]])
        uid = d["uid"]
        log_price(c, uid, old, p, "Карточка товара")
        ch = [f"{k}: {old.get(k):g} → {p[k]:g}" for k in ("price", "cost_price") if k in p and old.get(k) is not None and abs((old.get(k) or 0) - (p[k] or 0)) > 0.001]
        rbac.audit("Изменён товар", p["name"] + (" · " + ", ".join(ch) if ch else ""), c=c)
    else:
        uid = uuid.uuid4().hex
        p["uid"] = uid
        p["created_by"] = (rbac.user() or {}).get("name") or "Центр"
        c.execute(f"INSERT INTO products({','.join(p)}) VALUES({','.join('?' * len(p))})", list(p.values()))
        log_price(c, uid, {}, p, "Новый товар")
        rbac.audit("Создан товар", p["name"], c=c)
    c.commit()
    c.close()
    return jsonify(q("SELECT * FROM products WHERE uid=?", (uid,), one=True))


@app.delete("/api/products/<uid>")
@owner
def product_del(uid):
    c = con()
    c.execute("UPDATE products SET active=0, updated_at=? WHERE uid=?", (now(), uid))
    rbac.audit("Товар удалён из каталога", uid, c=c)
    c.commit()
    c.close()
    return jsonify(ok=True)


IMPORT_MAP = {
    "name": ["name", "наименование", "название", "товар", "номенклатура"],
    "barcode": ["barcode", "штрихкод", "штрих-код", "шк", "ean"],
    "sku": ["sku", "артикул"],
    "price": ["price", "цена", "цена продажи", "розничная цена"],
    "cost_price": ["cost_price", "себестоимость", "закупочная цена", "закупка"],
    "category": ["category", "категория", "группа"],
    "spic": ["spic", "икпу", "mxik"],
    "package_code": ["package_code", "код упаковки"],
    "units_code": ["units_code", "код единицы"],
    "vat_percent": ["vat_percent", "ндс", "ндс %"],
    "unit_name": ["unit_name", "ед", "ед. изм"],
    "min_stock": ["min_stock", "мин. остаток"],
    "qty": ["qty", "количество", "кол-во", "остаток"],
}


def read_table(f):
    raw = f.read()
    if (f.filename or "").lower().endswith((".xlsx", ".xlsm")):
        import openpyxl
        ws = openpyxl.load_workbook(io.BytesIO(raw), data_only=True, read_only=True).active
        it = ws.iter_rows(values_only=True)
        head = [str(h or "").strip().lower() for h in next(it)]
        rows = [dict(zip(head, r)) for r in it]
    else:
        text = raw.decode("utf-8-sig", errors="ignore")
        delim = ";" if text.count(";") > text.count(",") else ","
        rows = [{(k or "").strip().lower(): v for k, v in r.items()} for r in csv.DictReader(io.StringIO(text), delimiter=delim)]
    out = []
    for r in rows:
        rec = {}
        for f_, al in IMPORT_MAP.items():
            for a in al:
                if a in r and r[a] not in (None, ""):
                    rec[f_] = r[a]
                    break
        if rec.get("barcode") is not None:
            rec["barcode"] = str(rec["barcode"]).strip().split(".")[0]
        for k in ("spic", "package_code"):
            if rec.get(k) is not None:
                rec[k] = str(rec[k]).strip().split(".")[0]
        out.append(rec)
    return out


@app.post("/api/products/import")
@owner
def product_import():
    f = request.files.get("file")
    if not f:
        return err("Файл не выбран")
    try:
        rows = read_table(f)
    except Exception as e:
        return err("Не удалось прочитать файл: " + str(e))
    c = con()
    cr = up = 0
    for rec in rows:
        if not rec.get("name") and not rec.get("barcode"):
            continue
        rec.pop("qty", None)
        p = clean_p(rec)
        p["updated_at"] = now()
        ex = c.execute("SELECT uid, price, cost_price FROM products WHERE barcode=? AND active=1", (p.get("barcode"),)).fetchone() if p.get("barcode") else None
        if ex:
            c.execute(f"UPDATE products SET {','.join(k + '=?' for k in p)} WHERE uid=?", list(p.values()) + [ex["uid"]])
            log_price(c, ex["uid"], dict(ex), p, "Импорт")
            up += 1
        elif p.get("name"):
            p["uid"] = uuid.uuid4().hex
            p["created_by"] = "Импорт"
            c.execute(f"INSERT INTO products({','.join(p)}) VALUES({','.join('?' * len(p))})", list(p.values()))
            cr += 1
    c.commit()
    c.close()
    return jsonify(created=cr, updated=up)


@app.get("/api/products/export.csv")
@owner
def product_export():
    rows = q("SELECT * FROM products WHERE active=1 ORDER BY name")
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["Наименование", "Штрихкод", "Артикул", "Категория", "Цена", "Себестоимость", "ИКПУ", "Код упаковки",
                "Код единицы", "НДС", "Ед", "Мин. остаток"])
    for p in rows:
        w.writerow([p["name"], p["barcode"] or "", p["sku"] or "", p["category"] or "", p["price"], p["cost_price"],
                    p["spic"], p["package_code"], p["units_code"], p["vat_percent"], p["unit_name"], p["min_stock"]])
    return Response("﻿" + out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=catalog.csv"})


# ================================================================ stock matrix
@app.get("/api/stock")
@owner
def stock_matrix():
    t = (request.args.get("q") or "").strip()
    args, where = [], "p.active=1"
    if t:
        where += " AND (p.name LIKE ? OR p.barcode LIKE ?)"
        args += [f"%{t}%"] * 2
    sfx, sa = rbac.sf("id")
    st = q(f"SELECT id, name FROM stores WHERE active=1{sfx} ORDER BY id", sa)
    prods = q(f"SELECT uid, name, barcode, price, cost_price, min_stock FROM products p WHERE {where} ORDER BY name LIMIT 1000", args)
    m = {}
    sfx2, sa2 = rbac.sf("store_id")
    for r in q(f"SELECT store_id, product_uid, qty FROM stock WHERE 1=1{sfx2}", sa2):
        m.setdefault(r["product_uid"], {})[str(r["store_id"])] = r["qty"]
    for p in prods:
        p["by_store"] = m.get(p["uid"], {})
        p["total"] = sum(p["by_store"].values())
    if request.args.get("low"):
        prods = [p for p in prods if p["min_stock"] and any(p["by_store"].get(str(s["id"]), 0) <= p["min_stock"] for s in st)]
    if not rbac.can("finance"):
        for p in prods:
            p["cost_price"] = None
    return jsonify(stores=st, items=prods)


# ================================================================ docs (supply / transfer)
def doc_number(c, typ):
    n = c.execute("SELECT COUNT(*) n FROM docs WHERE type=?", (typ,)).fetchone()["n"] + 1
    return ("П-" if typ == "supply" else "ПМ-") + str(n).zfill(5)


def load_doc(uid, c=None):
    own = c is None
    c = c or con()
    d = c.execute("""SELECT d.*, fs.name AS from_name, ts.name AS to_name FROM docs d
                     LEFT JOIN stores fs ON fs.id=d.from_store_id LEFT JOIN stores ts ON ts.id=d.to_store_id
                     WHERE d.uid=?""", (uid,)).fetchone()
    if not d:
        if own:
            c.close()
        return None
    d = dict(d)
    d["lines"] = [dict(r) for r in c.execute("""SELECT l.*, p.name, p.barcode, p.unit_name FROM doc_lines l
                    LEFT JOIN products p ON p.uid=l.product_uid WHERE doc_uid=? ORDER BY l.id""", (uid,))]
    if own:
        c.close()
    return d


def _doc_scope():
    ids = rbac.store_ids()
    if ids is None:
        return "", ()
    if not ids:
        return " AND 1=0", ()
    ph = ",".join("?" * len(ids))
    return f" AND (d.to_store_id IN ({ph}) OR d.from_store_id IN ({ph}))", tuple(ids) * 2


@app.get("/api/docs")
@owner
def docs_list():
    rows = q("""SELECT d.*, fs.name AS from_name, ts.name AS to_name,
                (SELECT COUNT(*) FROM doc_lines WHERE doc_uid=d.uid) lines,
                (SELECT COALESCE(SUM(qty),0) FROM doc_lines WHERE doc_uid=d.uid) qty,
                (SELECT COALESCE(SUM(qty_fact),0) FROM doc_lines WHERE doc_uid=d.uid) qty_fact,
                (SELECT COALESCE(SUM(qty*cost),0) FROM doc_lines WHERE doc_uid=d.uid) sum
                FROM docs d LEFT JOIN stores fs ON fs.id=d.from_store_id LEFT JOIN stores ts ON ts.id=d.to_store_id
                WHERE 1=1{f} ORDER BY d.id DESC LIMIT 300""".replace("{f}", _doc_scope()[0]), _doc_scope()[1])
    return jsonify(rows)


@app.get("/api/docs/<uid>")
@owner
def doc_get(uid):
    d = load_doc(uid)
    if d and not (rbac.store_allowed(d["to_store_id"]) or rbac.store_allowed(d["from_store_id"])):
        return err("Нет доступа", 403)
    return jsonify(d) if d else err("Не найдено", 404)


@app.post("/api/docs")
@owner
def doc_create():
    """Поставка от поставщика в магазин или перемещение между магазинами (создаёт владелец)."""
    d = request.json or {}
    typ = "transfer" if d.get("type") == "transfer" else "supply"
    to_id = int(num(d.get("to_store_id")))
    from_id = int(num(d.get("from_store_id"))) or None
    if not to_id:
        return err("Выберите магазин-получатель")
    if typ == "transfer" and (not from_id or from_id == to_id):
        return err("Выберите магазин-отправитель (отличный от получателя)")
    if not (rbac.store_allowed(to_id) or (from_id and rbac.store_allowed(from_id))):
        return err("Нет доступа к выбранным магазинам", 403)
    lines = [l for l in (d.get("lines") or []) if num(l.get("qty")) > 0 and l.get("product_uid")]
    if not lines:
        return err("Добавьте товары")
    c = con()
    uid = uuid.uuid4().hex
    t = now()
    # поставка уходит сразу получателю; перемещение — черновик / на согласование / сразу отправителю, если есть право одобрять
    mode = d.get("mode") or "approve"
    if typ == "supply":
        status = "sent"
    elif mode == "draft":
        status = "draft"
    elif mode == "approve" and rbac.can("docs.approve") and rbac.store_allowed(from_id):
        status = "to_ship"
    else:
        status = "pending"
    c.execute("""INSERT INTO docs(uid,number,type,status,from_store_id,to_store_id,supplier,note,created_at,created_by,
                 sent_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
              (uid, doc_number(c, typ), typ, status, from_id, to_id, d.get("supplier", ""), d.get("note", ""), t,
               (rbac.user() or {}).get("name") or "Центр", t if status == "sent" else None, t))
    rbac.audit("Создана накладная" if typ == "supply" else "Создано перемещение", f"{len(lines)} строк", to_id, c=c)
    transfers.event(c, uid, "draft" if typ == "transfer" else "sent", "Создан документ")
    if typ == "transfer" and status != "draft":
        transfers.event(c, uid, status, "")
    if status == "to_ship":
        c.execute("UPDATE docs SET approved_at=?, approved_by=? WHERE uid=?", (t, (rbac.user() or {}).get("name", ""), uid))
    for l in lines:
        c.execute("INSERT INTO doc_lines(doc_uid,product_uid,qty,cost,price) VALUES(?,?,?,?,?)",
                  (uid, l["product_uid"], num(l["qty"]), num(l.get("cost")), num(l.get("price"))))
    c.commit()
    c.close()
    return jsonify(load_doc(uid))


@app.post("/api/docs/parse")
@owner
def doc_parse():
    """Excel/CSV накладной поставщика (штрихкод, кол-во, цена) -> строки документа."""
    f = request.files.get("file")
    if not f:
        return err("Файл не выбран")
    try:
        rows = read_table(f)
    except Exception as e:
        return err("Не удалось прочитать файл: " + str(e))
    lines, missing = [], []
    for r in rows:
        p = None
        if r.get("barcode"):
            p = q("SELECT uid,name,barcode,cost_price,price FROM products WHERE barcode=? AND active=1", (r["barcode"],), one=True)
        if not p and r.get("name"):
            p = q("SELECT uid,name,barcode,cost_price,price FROM products WHERE name=? AND active=1", (str(r["name"]).strip(),), one=True)
        if not p:
            missing.append(r.get("name") or r.get("barcode"))
            continue
        lines.append({"product_uid": p["uid"], "name": p["name"], "barcode": p["barcode"], "qty": num(r.get("qty"), 1),
                      "cost": num(r.get("cost_price"), p["cost_price"]), "price": num(r.get("price"), 0)})
    return jsonify(lines=lines, missing=missing)


@app.get("/api/products/barcode/<code>")
@owner
def product_by_code(code):
    p = q("SELECT * FROM products WHERE active=1 AND (barcode=? OR sku=?)", (code.strip(), code.strip()), one=True)
    return jsonify(p) if p else err("Товар не найден", 404)


@app.delete("/api/docs/<uid>")
@owner
def doc_cancel(uid):
    c = con()
    d = c.execute("SELECT status FROM docs WHERE uid=?", (uid,)).fetchone()
    if not d or d["status"] in ("received",):
        c.close()
        return err("Принятую накладную отменить нельзя")
    c.execute("UPDATE docs SET status='cancelled', updated_at=? WHERE uid=?", (now(), uid))
    c.commit()
    c.close()
    return jsonify(ok=True)


# ================================================================ dashboard / reports
@app.get("/api/dashboard")
@owner
def dashboard():
    day = request.args.get("day") or tnow().strftime("%Y-%m-%d")
    a = (day + " 00:00:00", day + " 23:59:59")
    sfx, sa = rbac.sf("id")
    st = q(f"SELECT id, name, last_seen FROM stores WHERE active=1{sfx} ORDER BY id", sa)
    per = {r["store_id"]: r for r in q("""SELECT store_id,
            SUM(CASE WHEN operation='sale' THEN total ELSE -total END) net,
            COUNT(CASE WHEN operation='sale' THEN 1 END) checks,
            SUM(CASE WHEN operation='sale' THEN cash ELSE -cash END) cash,
            SUM(CASE WHEN operation='sale' THEN card ELSE -card END) card,
            SUM(CASE WHEN operation='sale' THEN debt ELSE -debt END) debt
            FROM sales WHERE created_at BETWEEN ? AND ? GROUP BY store_id""", a)}
    profit = {r["store_id"]: r["p"] for r in q("""SELECT s.store_id,
            SUM(CASE WHEN s.operation='sale' THEN si.total-si.qty*si.cost_price ELSE -(si.total-si.qty*si.cost_price) END) p
            FROM sale_items si JOIN sales s ON s.uid=si.sale_uid WHERE s.created_at BETWEEN ? AND ? GROUP BY s.store_id""", a)}
    t = tnow()
    for s in st:
        r = per.get(s["id"], {})
        s.update(net=r.get("net") or 0, checks=r.get("checks") or 0, cash=r.get("cash") or 0, card=r.get("card") or 0,
                 debt=r.get("debt") or 0, profit=profit.get(s["id"]) or 0)
        s["avg"] = s["net"] / s["checks"] if s["checks"] else 0
        s["online"] = bool(s["last_seen"]) and (t - datetime.strptime(s["last_seen"], "%Y-%m-%d %H:%M:%S")).total_seconds() < 180
    since = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=13)).strftime("%Y-%m-%d")
    trend = q("""SELECT substr(created_at,1,10) d, store_id, SUM(CASE WHEN operation='sale' THEN total ELSE -total END) net
                 FROM sales WHERE created_at BETWEEN ? AND ? GROUP BY substr(created_at,1,10), store_id ORDER BY d""", (since + " 00:00:00", a[1]))
    top = q("""SELECT MAX(si.name) AS name, SUM(si.qty) q, SUM(si.total) t FROM sale_items si JOIN sales s ON s.uid=si.sale_uid
               WHERE s.operation='sale' AND s.created_at BETWEEN ? AND ? GROUP BY si.product_uid ORDER BY t DESC LIMIT 10""", a)
    pending = q("""SELECT COUNT(*) n FROM docs WHERE status IN ('sent','to_ship','in_transit')""", one=True)["n"]
    disc = q("""SELECT d.number, ts.name to_name, d.received_at,
                (SELECT COALESCE(SUM(CASE WHEN qty>COALESCE(qty_fact,0) THEN qty-COALESCE(qty_fact,0) ELSE 0 END),0) FROM doc_lines WHERE doc_uid=d.uid) short,
                (SELECT COALESCE(SUM(CASE WHEN COALESCE(qty_fact,0)>qty THEN COALESCE(qty_fact,0)-qty ELSE 0 END),0) FROM doc_lines WHERE doc_uid=d.uid) extra, d.note
                FROM docs d LEFT JOIN stores ts ON ts.id=d.to_store_id
                WHERE d.status='received' AND d.received_at>=? AND EXISTS(SELECT 1 FROM doc_lines l WHERE l.doc_uid=d.uid AND ABS(COALESCE(l.qty_fact,0)-l.qty)>0.0001)
                ORDER BY d.received_at DESC LIMIT 10""", ((t - timedelta(days=14)).strftime("%Y-%m-%d"),))
    return jsonify(stores=st, trend=trend, top=top, pending_docs=pending, discrepancies=disc)


@app.get("/api/sales_report")
@owner
def sales_report():
    dfrom = request.args.get("from") or tnow().strftime("%Y-%m-01")
    dto = request.args.get("to") or tnow().strftime("%Y-%m-%d")
    a = (dfrom + " 00:00:00", dto + " 23:59:59")
    sid = request.args.get("store")
    if sid and not rbac.store_allowed(sid):
        return err("Нет доступа к магазину", 403)
    f, args = ("AND s.store_id=?", a + (int(sid),)) if sid else ("", a)
    sfx, sa = rbac.sf("s.store_id")
    f, args = f + sfx, args + sa
    by_store = q(f"""SELECT MAX(st.name) AS name, SUM(CASE WHEN s.operation='sale' THEN s.total ELSE -s.total END) net,
                     COUNT(CASE WHEN s.operation='sale' THEN 1 END) checks FROM sales s JOIN stores st ON st.id=s.store_id
                     WHERE s.created_at BETWEEN ? AND ? {f} GROUP BY s.store_id ORDER BY net DESC""", args)
    top = q(f"""SELECT MAX(si.name) AS name, SUM(CASE WHEN s.operation='sale' THEN si.qty ELSE -si.qty END) qty,
                SUM(CASE WHEN s.operation='sale' THEN si.total ELSE -si.total END) total,
                SUM(CASE WHEN s.operation='sale' THEN si.total-si.qty*si.cost_price ELSE -(si.total-si.qty*si.cost_price) END) profit
                FROM sale_items si JOIN sales s ON s.uid=si.sale_uid WHERE s.created_at BETWEEN ? AND ? {f}
                GROUP BY si.product_uid ORDER BY total DESC LIMIT 50""", args)
    return jsonify(by_store=by_store, top=top)


# ================================================================ SYNC API (для касс)
def store_auth():
    tok = request.headers.get("X-Store-Token", "")
    if not tok:
        return None
    return q("SELECT * FROM stores WHERE token=? AND active=1", (tok,), one=True)


@app.post("/sync")
def sync():
    st = store_auth()
    if not st:
        return err("Неверный токен магазина", 401)
    d = request.json or {}
    c = con()
    t = now()
    acks = {"sales": [], "docs": [], "products": [], "money": [], "customers": []}

    # ---- новые товары, созданные на кассе
    for p in d.get("new_products") or []:
        if not p.get("uid") or not p.get("name"):
            continue
        ex = c.execute("SELECT uid FROM products WHERE uid=?", (p["uid"],)).fetchone()
        if not ex and p.get("barcode"):
            ex = c.execute("SELECT uid FROM products WHERE barcode=? AND active=1", (p["barcode"],)).fetchone()
        if ex:
            acks["products"].append({"uid": p["uid"], "hq_uid": ex["uid"]})
            continue
        v = clean_p(p)
        v.update(uid=p["uid"], updated_at=t, created_by=st["name"])
        c.execute(f"INSERT INTO products({','.join(v)}) VALUES({','.join('?' * len(v))})", list(v.values()))
        acks["products"].append({"uid": p["uid"], "hq_uid": p["uid"]})

    # ---- продажи
    for s in d.get("sales") or []:
        if not s.get("uid"):
            continue
        if not c.execute("SELECT 1 FROM sales WHERE uid=?", (s["uid"],)).fetchone():
            c.execute("""INSERT INTO sales(uid,store_id,local_id,created_at,operation,total,cash,card,debt,discount,cashier,
                         fiscal_status,receipt_seq,qr,qr_provider,promo_disc,cashier_uid,customer_uid) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (s["uid"], st["id"], s.get("id"), s.get("created_at"), s.get("operation"), num(s.get("total")),
                       num(s.get("cash")), num(s.get("card")), num(s.get("debt")), num(s.get("discount")),
                       s.get("cashier"), s.get("fiscal_status"), s.get("receipt_seq"), num(s.get("qr")), s.get("qr_provider"),
                       num(s.get("promo_disc")), s.get("cashier_uid"), s.get("customer_uid")))
            for it in s.get("items") or []:
                c.execute("INSERT INTO sale_items(sale_uid,product_uid,name,qty,price,total,cost_price) VALUES(?,?,?,?,?,?,?)",
                          (s["uid"], it.get("product_uid"), it.get("name"), num(it.get("qty")), num(it.get("price")),
                           num(it.get("total")), num(it.get("cost_price"))))
        else:   # обновление статуса фискализации
            c.execute("UPDATE sales SET fiscal_status=?, receipt_seq=? WHERE uid=?",
                      (s.get("fiscal_status"), s.get("receipt_seq"), s["uid"]))
        acks["sales"].append(s["uid"])

    # ---- деньги магазина (расходы, внесения/изъятия, погашения долгов, оплаты поставщикам) — идемпотентно по uid
    for m in d.get("money") or []:
        if not m.get("uid"):
            continue
        if not c.execute("SELECT 1 FROM money_ops WHERE uid=?", (m["uid"],)).fetchone():
            c.execute("""INSERT INTO money_ops(uid,store_id,created_at,kind,type,method,amount,category,note,user_name,source)
                         VALUES(?,?,?,?,?,?,?,?,?,?,'till')""",
                      (m["uid"], st["id"], m.get("created_at") or t, m.get("kind"), m.get("type"), m.get("method") or "cash",
                       num(m.get("amount")), m.get("category") or "", m.get("note") or "", m.get("user") or ""))
        acks["money"].append(m["uid"])

    # ---- остатки
    for r in d.get("stock") or []:
        if r.get("uid"):
            c.execute("""INSERT INTO stock(store_id,product_uid,qty,updated_at) VALUES(?,?,?,?)
                         ON CONFLICT(store_id,product_uid) DO UPDATE SET qty=excluded.qty, updated_at=excluded.updated_at""",
                      (st["id"], r["uid"], num(r.get("qty")), t))

    # ---- накладные от кассы: принятые и отправленные
    for dc in d.get("docs") or []:
        uid = dc.get("uid")
        if not uid:
            continue
        if transfers.from_till(c, st, dc, t):
            acks["docs"].append(uid)
            continue
        ex = c.execute("SELECT * FROM docs WHERE uid=?", (uid,)).fetchone()
        if dc.get("status") == "received":
            if ex and ex["to_store_id"] == st["id"] and ex["status"] == "sent":
                old_note, cm = (ex["note"] or "").strip(), (dc.get("comment") or "").strip()
                # касса присылает свою заметку целиком (исходная + комментарий приёмки) — не дублируем
                note = old_note if not cm or cm == old_note else (cm if not old_note or cm.startswith(old_note) else old_note + " | " + cm)
                c.execute("UPDATE docs SET status='received', received_at=?, updated_at=?, note=? WHERE uid=?",
                          (dc.get("received_at") or t, t, note, uid))
                fact = {l["product_uid"]: num(l.get("qty_fact")) for l in dc.get("lines") or []}
                known = set()
                for l in c.execute("SELECT id, product_uid FROM doc_lines WHERE doc_uid=?", (uid,)).fetchall():
                    c.execute("UPDATE doc_lines SET qty_fact=? WHERE id=?", (fact.get(l["product_uid"], 0), l["id"]))
                    known.add(l["product_uid"])
                for pu, qf in fact.items():          # лишний товар, которого не было в накладной
                    if pu not in known and qf:
                        c.execute("INSERT INTO doc_lines(doc_uid,product_uid,qty,qty_fact) VALUES(?,?,0,?)", (uid, pu, qf))
                transfers.after_received(c, uid, st)
            elif not ex:                              # приход без накладной Центра (импорт/свой поставщик)
                c.execute("""INSERT INTO docs(uid,number,type,status,to_store_id,supplier,note,created_at,created_by,
                             received_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                          (uid, dc.get("number") or doc_number(c, "supply"), "supply", "received", st["id"],
                           dc.get("supplier", ""), dc.get("comment", ""), dc.get("created_at") or t, st["name"],
                           dc.get("received_at") or t, t))
                for l in dc.get("lines") or []:
                    c.execute("INSERT INTO doc_lines(doc_uid,product_uid,qty,qty_fact,cost) VALUES(?,?,?,?,?)",
                              (uid, l["product_uid"], num(l.get("qty")), num(l.get("qty_fact")), num(l.get("cost"))))
        elif dc.get("status") == "shipped":          # магазин отгрузил перемещение
            to = c.execute("SELECT id FROM stores WHERE uid=?", (dc.get("to_store_uid"),)).fetchone()
            if ex:
                c.execute("UPDATE docs SET status='sent', sent_at=?, updated_at=? WHERE uid=?", (t, t, uid))
                fact = {l["product_uid"]: num(l.get("qty")) for l in dc.get("lines") or []}
                c.execute("DELETE FROM doc_lines WHERE doc_uid=?", (uid,))
                for pu, qv in fact.items():
                    c.execute("INSERT INTO doc_lines(doc_uid,product_uid,qty,cost) VALUES(?,?,?,?)", (uid, pu, qv,
                              next((num(l.get("cost")) for l in dc["lines"] if l["product_uid"] == pu), 0)))
            elif to:
                c.execute("""INSERT INTO docs(uid,number,type,status,from_store_id,to_store_id,note,created_at,created_by,
                             sent_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                          (uid, dc.get("number") or doc_number(c, "transfer"), "transfer", "sent", st["id"], to["id"],
                           dc.get("comment", ""), dc.get("created_at") or t, st["name"], t, t))
                for l in dc.get("lines") or []:
                    c.execute("INSERT INTO doc_lines(doc_uid,product_uid,qty,cost) VALUES(?,?,?,?)",
                              (uid, l["product_uid"], num(l.get("qty")), num(l.get("cost"))))
        acks["docs"].append(uid)

    # ---- сотрудники и клиенты кассы (персонал, единая база клиентов, уровни лояльности)
    people.from_till(c, st, d, t, acks)

    c.execute("UPDATE stores SET last_seen=?, info=? WHERE id=?",
              (t, json.dumps(d.get("info") or {}, ensure_ascii=False), st["id"]))
    c.commit()

    # ---- что отдать вниз
    since = d.get("since") or "1970-01-01 00:00:00"
    products = [dict(r) for r in c.execute("SELECT * FROM products WHERE updated_at>=? ORDER BY updated_at LIMIT 5000", (since,))]
    incoming = []
    for r in c.execute("""SELECT uid FROM docs WHERE (to_store_id=? AND status='sent') OR (from_store_id=? AND status='to_ship')""",
                       (st["id"], st["id"])).fetchall():
        incoming.append(load_doc(r["uid"], c))
    stores_list = [dict(r) for r in c.execute("SELECT uid, name FROM stores WHERE active=1 AND id<>?", (st["id"],))]
    cancelled = [dict(r)["uid"] for r in c.execute("""SELECT uid FROM docs WHERE status='cancelled' AND (from_store_id=? OR to_store_id=?)
                                                     AND updated_at>=?""", (st["id"], st["id"], (tnow() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")))]
    pending = migrate.pending_for(c, st["id"])
    down = people.to_till(c, st, d, t)
    c.close()
    return jsonify(**down, acks=acks, products=products, docs=incoming, stores=stores_list, server_time=t, pending=pending, cancelled=cancelled,
                   store={"uid": st["uid"], "name": st["name"]})


# ================================================================ обновления касс
REPO_ROOT = os.path.dirname(BASE)          # касса лежит в корне репозитория, Центр — в hq/
_UPD = {"version": None, "zip": None, "sha256": None}
UPD_SKIP_DIRS = {"hq", ".git", ".github", "data", "__pycache__", "node_modules"}
UPD_SKIP_FILES = {"VERSION", "railway.json", "Procfile", ".gitignore"}


def build_update():
    """Собирает zip кассы из файлов репозитория (один раз на запуск Центра)."""
    if _UPD["zip"] is not None:
        return _UPD
    import hashlib
    import zipfile
    files = []
    if os.path.exists(os.path.join(REPO_ROOT, "app.py")):
        for root, dirs, fns in os.walk(REPO_ROOT):
            dirs[:] = sorted(d for d in dirs if d not in UPD_SKIP_DIRS and not d.startswith("."))
            for fn in sorted(fns):
                if fn in UPD_SKIP_FILES or fn.endswith((".pyc", ".db", ".log")):
                    continue
                full = os.path.join(root, fn)
                files.append((os.path.relpath(full, REPO_ROOT).replace(os.sep, "/"), full))
    h = hashlib.sha1()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, full in files:
            data = open(full, "rb").read()
            h.update(rel.encode() + b"\0" + data)
            zf.writestr(rel, data)
    raw = buf.getvalue()
    _UPD.update(version=(h.hexdigest()[:10] if files else None), zip=raw, sha256=hashlib.sha256(raw).hexdigest(),
                files=len(files))
    return _UPD


@app.get("/update/version")
def update_version():
    if not store_auth():
        return err("Неверный токен магазина", 401)
    u = build_update()
    return jsonify(version=u["version"], sha256=u["sha256"], files=u.get("files", 0),
                   notes=(os.environ.get("RAILWAY_GIT_COMMIT_MESSAGE") or "").split("\n")[0][:200])


@app.get("/update/kassa.zip")
def update_zip():
    if not store_auth():
        return err("Неверный токен магазина", 401)
    u = build_update()
    if not u["version"]:
        return err("Сборка кассы не найдена на сервере", 404)
    return Response(u["zip"], mimetype="application/zip",
                    headers={"Content-Disposition": f"attachment; filename=kassa-{u['version']}.zip"})


@app.get("/api/update/info")
@owner
def update_info():
    u = build_update()
    stores = q("SELECT name, info FROM stores WHERE active=1")
    for st in stores:
        st["version"] = json.loads(st["info"] or "{}").get("app_version")
        del st["info"]
    return jsonify(version=u["version"], files=u.get("files", 0), stores=stores)


@app.get("/health")
def health():
    return jsonify(ok=True, time=now())


import sys  # noqa: E402
import rbac  # noqa: E402
rbac.register(sys.modules[__name__])
import migrate  # noqa: E402
migrate.register(sys.modules[__name__])
import hqdash  # noqa: E402
hqdash.register(sys.modules[__name__])
import stores2  # noqa: E402
stores2.register(sys.modules[__name__])
import stockhq  # noqa: E402
stockhq.register(sys.modules[__name__])
import transfers  # noqa: E402
transfers.register(sys.modules[__name__])
import analytics  # noqa: E402
analytics.register(sys.modules[__name__])
import people  # noqa: E402
people.register(sys.modules[__name__])

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"\n  ЦЕНТР запущен: http://127.0.0.1:{port}  (пароль: переменная HQ_PASSWORD, по умолчанию admin)\n")
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port, threads=8)
    except ImportError:
        app.run(host="0.0.0.0", port=port)
