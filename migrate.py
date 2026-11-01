"""
Переезд из другой учётной программы (1С, BILLZ, МойСклад, Regos, Excel...).

Шаги: загрузка файла -> автоопределение строки заголовков и колонок -> проверка (без записи)
-> применение. Остатки, клиенты и история продаж доставляются в кассы магазинов при синхронизации.
"""
import csv
import io
import json
import re
import uuid
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request

bp = Blueprint("migrate", __name__)
H = None  # модуль hq

# ---------------------------------------------------------------- поля и синонимы колонок
F = {
    "name": ["наименование", "номенклатура", "название", "название товара", "товар", "наименование товара", "product", "name", "nomi", "mahsulot"],
    "barcode": ["штрихкод", "штрих код", "штрих-код", "шк", "баркод", "barcode", "ean", "штрихкоды", "shtrix kod"],
    "sku": ["артикул", "sku", "код товара", "код", "article", "artikul"],
    "category": ["категория", "группа", "группа товаров", "вид номенклатуры", "category", "kategoriya"],
    "price": ["цена", "розничная цена", "цена продажи", "цена розничная", "retail price", "price", "narx", "сотув нархи"],
    "cost_price": ["себестоимость", "закупочная цена", "цена закупки", "закупка", "цена поступления", "cost", "tannarx"],
    "spic": ["икпу", "mxik", "spic", "код икпу"],
    "package_code": ["код упаковки", "package code", "упаковка код"],
    "units_code": ["код единицы", "код ед изм"],
    "vat_percent": ["ндс", "ставка ндс", "ндс %", "vat"],
    "unit_name": ["ед", "ед изм", "единица", "единица измерения", "unit"],
    "min_stock": ["мин остаток", "минимальный остаток", "min stock"],
    "qty": ["остаток", "количество", "кол во", "колво", "конечный остаток", "остаток на конец", "в наличии", "qty", "quantity", "miqdor", "qoldiq"],
    "store": ["магазин", "склад", "филиал", "точка", "торговая точка", "store", "shop", "filial", "dokon"],
    "phone": ["телефон", "тел", "номер телефона", "мобильный", "phone", "telefon"],
    "customer": ["клиент", "фио", "покупатель", "контрагент", "имя клиента", "customer", "mijoz", "имя"],
    "card": ["карта", "номер карты", "дисконтная карта", "card"],
    "bonus": ["бонусы", "бонус", "баллы", "бонусный баланс", "bonus", "cashback", "кешбэк", "кэшбэк"],
    "debt": ["долг", "задолженность", "насия", "nasiya", "qarz", "debt", "сумма долга"],
    "discount_pct": ["скидка", "скидка %", "процент скидки", "discount"],
    "birthday": ["день рождения", "дата рождения", "birthday", "tugilgan kun"],
    "date": ["дата", "период", "дата продажи", "date", "sana", "день"],
    "total": ["сумма", "выручка", "сумма продажи", "сумма продаж", "total", "summa"],
    "receipt": ["чек", "номер чека", "№ чека", "документ", "receipt"],
    "shelf_days": ["срок годности", "срок годности дней", "срок хранения", "годность"],
}
KINDS = {
    "products": {"title": "Товары (каталог)",
                 "fields": ["name", "barcode", "sku", "category", "price", "cost_price", "spic", "package_code", "units_code",
                            "vat_percent", "unit_name", "min_stock", "shelf_days"], "required": ["name"]},
    "stock": {"title": "Остатки по магазинам", "fields": ["barcode", "sku", "name", "qty", "store", "price", "cost_price", "category"],
              "required": ["qty"], "one_of": ["barcode", "sku", "name"]},
    "customers": {"title": "Клиенты, бонусы и долги", "fields": ["customer", "phone", "card", "bonus", "debt", "discount_pct", "birthday", "store"],
                  "required": [], "one_of": ["customer", "phone"]},
    "sales": {"title": "История продаж", "fields": ["date", "barcode", "sku", "name", "qty", "total", "price", "store", "receipt"],
              "required": ["date", "qty"], "one_of": ["barcode", "sku", "name"]},
}
LABELS = {"name": "Наименование", "barcode": "Штрихкод", "sku": "Артикул", "category": "Категория", "price": "Цена продажи",
          "cost_price": "Себестоимость", "spic": "ИКПУ", "package_code": "Код упаковки", "units_code": "Код ед. изм.",
          "vat_percent": "НДС %", "unit_name": "Ед. изм.", "min_stock": "Мин. остаток", "qty": "Количество / остаток",
          "store": "Магазин / склад", "phone": "Телефон", "customer": "Имя клиента", "card": "№ карты", "bonus": "Бонусы",
          "debt": "Долг (насия)", "discount_pct": "Скидка %", "birthday": "День рождения", "date": "Дата", "total": "Сумма",
          "receipt": "№ чека", "shelf_days": "Срок годности, дней"}

MIG_SCHEMA = """
CREATE TABLE IF NOT EXISTS imports (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, filename TEXT, created_at TEXT, status TEXT,
  header_row INTEGER, headers TEXT, rows TEXT, result TEXT);
CREATE TABLE IF NOT EXISTS pending_stock (
  id INTEGER PRIMARY KEY AUTOINCREMENT, store_id INTEGER, product_uid TEXT, qty REAL, mode TEXT, import_id INTEGER,
  created_at TEXT, applied_at TEXT);
CREATE TABLE IF NOT EXISTS pending_customers (
  id INTEGER PRIMARY KEY AUTOINCREMENT, store_id INTEGER, data TEXT, import_id INTEGER, created_at TEXT, applied_at TEXT);
CREATE TABLE IF NOT EXISTS pending_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT, store_id INTEGER, product_uid TEXT, day TEXT, qty REAL, import_id INTEGER,
  created_at TEXT, applied_at TEXT)
"""


# ---------------------------------------------------------------- парсинг файла
def norm(s):
    s = str(s or "").lower().replace("ё", "е").replace("\n", " ")
    s = re.sub(r"[^\w%№ ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюяўқғҳ",
              ["a", "b", "v", "g", "d", "e", "e", "j", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u", "f",
               "h", "ts", "ch", "sh", "sh", "", "i", "", "e", "yu", "ya", "o", "q", "g", "h"]))
TOTAL_WORDS = ("итого", "всего", "итог", "total", "jami", "жами")


def lat(s):
    """Кириллица -> латиница для сравнения названий магазинов: «Атлас Чимган» == «Atlas Chimgan»."""
    s = norm(s)
    s = "".join(TR.get(ch, ch) for ch in s)
    s = s.replace("kh", "h").replace("x", "h").replace("'", "").replace("`", "")
    s = s.replace("ch", "#").replace("sh", "%").replace("c", "s").replace("y", "i").replace("#", "ch").replace("%", "sh")
    return re.sub(r"(.)\1", r"\1", s)        # двойные буквы: «Сити»/«City», «Shota»/«Shotta»


def is_total(v):
    n = norm(v)
    return any(n == w or n.startswith(w + " ") for w in TOTAL_WORDS)


def read_any(f):
    raw = f.read()
    name = (f.filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        import openpyxl
        ws = openpyxl.load_workbook(io.BytesIO(raw), data_only=True, read_only=True).active
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
    elif name.endswith(".xls"):
        raise ValueError("Старый формат .xls — откройте файл в Excel и сохраните как .xlsx")
    else:
        text = None
        for enc in ("utf-8-sig", "cp1251"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        text = text if text is not None else raw.decode("utf-8", errors="ignore")
        sample = text[:5000]
        delim = max([";", ",", "\t"], key=sample.count)
        rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim)]
    rows = [r for r in rows if r and any(c not in (None, "") for c in r)]
    return rows[:200000]


def match_field(header, fields):
    h = norm(header)
    if not h:
        return None, 0
    best, score = None, 0
    for f in fields:
        for a in F[f]:
            a = norm(a)
            sc = 3 if h == a else 2 if h.startswith(a + " ") or h.endswith(" " + a) else 1 if (len(a) > 3 and a in h) else 0
            if sc > score:
                best, score = f, sc
    return best, score


def detect(rows, kind):
    fields = KINDS[kind]["fields"]
    best_i, best_n = 0, -1
    for i, r in enumerate(rows[:25]):
        n = sum(1 for c in r if match_field(c, fields)[0])
        if n > best_n:
            best_i, best_n = i, n
    headers = [str(c if c is not None else "").strip() for c in rows[best_i]] if rows else []
    mapping, used = {}, set()
    cands = []
    for ci, h in enumerate(headers):
        f, sc = match_field(h, fields)
        if f:
            cands.append((sc, f, ci))
    for sc, f, ci in sorted(cands, key=lambda x: -x[0]):
        if f not in mapping and ci not in used:
            mapping[f] = ci
            used.add(ci)
    return best_i, headers, mapping


def num(v, d=0.0):
    if v is None or v == "":
        return d
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace("\xa0", "").replace(" ", "").replace("сум", "").replace("so'm", "").strip()
    if s.count(",") == 1 and s.count(".") == 0:
        s = s.replace(",", ".")
    elif s.count(",") and s.count("."):
        s = s.replace(",", "")
    s = re.sub(r"[^0-9.\-]", "", s)
    try:
        return float(s) if s not in ("", "-", ".") else d
    except ValueError:
        return d


def code(v):
    """Штрихкод/артикул: Excel любит превращать их в 4.60123E+12 или '123.0'."""
    if v is None:
        return ""
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else str(v)
    s = str(v).strip()
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".")[0]
    if re.fullmatch(r"\d+(\.\d+)?[eE]\+\d+", s):
        s = str(int(float(s)))
    return s


def day(v):
    if v is None or v == "":
        return None
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, (int, float)) and 20000 < v < 80000:   # дата Excel числом
        return (datetime(1899, 12, 30) + timedelta(days=float(v))).strftime("%Y-%m-%d")
    s = str(v).strip()
    m = re.match(r"(\d{4})[./-](\d{1,2})[./-](\d{1,2})", s)
    if m:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
    else:
        m = re.match(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})", s)
        if not m:
            return None
        d, mo, y = int(m[1]), int(m[2]), int(m[3])
        if y < 100:
            y += 2000
    try:
        return datetime(y, mo, d).strftime("%Y-%m-%d")
    except ValueError:
        return None


def phone(v):
    d = re.sub(r"\D", "", code(v))
    if len(d) == 9:
        d = "998" + d
    return d or None


# ---------------------------------------------------------------- обработка
class Ctx:
    def __init__(self, c, opts):
        self.c = c
        self.opts = opts
        self.by_bc, self.by_sku, self.by_name = {}, {}, {}
        for p in c.execute("SELECT uid, barcode, sku, name FROM products WHERE active=1").fetchall():
            if p["barcode"]:
                self.by_bc[p["barcode"]] = p["uid"]
            if p["sku"]:
                self.by_sku.setdefault(p["sku"], p["uid"])
            self.by_name.setdefault(norm(p["name"]), p["uid"])
        rows = c.execute("SELECT id, name FROM stores WHERE active=1").fetchall()
        self.stores = {lat(s["name"]): s["id"] for s in rows}
        self.store_names = {s["id"]: s["name"] for s in rows}

    def find_product(self, bc, sku, name):
        if bc and bc in self.by_bc:
            return self.by_bc[bc]
        if sku and sku in self.by_sku:
            return self.by_sku[sku]
        if name and norm(name) in self.by_name:
            return self.by_name[norm(name)]
        return None

    def find_store(self, val, stats):
        if val in (None, ""):
            sid = int(num(self.opts.get("store_id")))
            return sid or None
        n = lat(val)
        if n in self.stores:
            return self.stores[n]
        for k, v in self.stores.items():
            if k and n and (k in n or n in k):
                return v
        words = set(n.split()) - {"tts", "ts", "tc", "magazin", "sklad", "filial", "dokon", "store", "shop"}
        for k, v in self.stores.items():   # «Склад Tashkent City» / «ТЦ Ташкент Сити» и т.п.
            if words and words <= set(k.split()) | {"tts", "ts"}:
                return v
        if self.opts.get("create_stores"):
            import secrets
            t = H.now()
            self.c.execute("INSERT INTO stores(uid,name,address,token,created_at) VALUES(?,?,?,?,?)",
                           (uuid.uuid4().hex, str(val).strip(), "", secrets.token_hex(20), t))
            sid = self.c.execute("SELECT id FROM stores WHERE name=? ORDER BY id DESC", (str(val).strip(),)).fetchone()["id"]
            self.stores[n] = sid
            self.store_names[sid] = str(val).strip()
            stats["stores_created"].append(str(val).strip())
            return sid
        stats["stores_missing"].add(str(val).strip())
        return None

    def create_product(self, bc, sku, name, price=0, cost=0, category=""):
        uid = uuid.uuid4().hex
        self.c.execute("""INSERT INTO products(uid,name,barcode,sku,category,price,cost_price,updated_at,created_by,vat_percent)
                          VALUES(?,?,?,?,?,?,?,?,?,?)""",
                       (uid, name, bc or None, sku or "", category or "", price, cost, H.now(), "Переезд", 12))
        if bc:
            self.by_bc[bc] = uid
        if sku:
            self.by_sku[sku] = uid
        self.by_name[norm(name)] = uid
        return uid


def get(row, mapping, f):
    i = mapping.get(f)
    if i is None or i == "" or int(i) >= len(row):
        return None
    v = row[int(i)]
    return v.strip() if isinstance(v, str) else v


def process(kind, rows, mapping, opts, c, import_id):
    st = {"rows": 0, "created": 0, "updated": 0, "skipped": 0, "errors": [], "stores_missing": set(), "stores_created": [],
          "per_store": {}, "products_created": 0, "sum": 0.0}
    ctx = Ctx(c, opts)
    t = H.now()
    mapping = {k: int(v) for k, v in mapping.items() if v not in (None, "", -1, "-1")}

    def err(i, msg):
        if len(st["errors"]) < 300:
            st["errors"].append({"row": i, "msg": msg})
        st["skipped"] += 1

    def per(sid):
        nm = ctx.store_names.get(sid) or str(sid)
        st["per_store"][nm] = st["per_store"].get(nm, 0) + 1

    if kind == "products":
        for i, r in enumerate(rows, 1):
            st["rows"] += 1
            name = get(r, mapping, "name")
            if not name:
                err(i, "нет наименования")
                continue
            if is_total(name):
                st["rows"] -= 1
                continue
            bc, sku = code(get(r, mapping, "barcode")), code(get(r, mapping, "sku"))
            vals = {"name": str(name).strip()[:200]}
            if bc:
                vals["barcode"] = bc
            if sku:
                vals["sku"] = sku
            for f in ("category", "unit_name"):
                v = get(r, mapping, f)
                if v not in (None, ""):
                    vals[f] = str(v).strip()
            for f in ("spic", "package_code"):
                v = code(get(r, mapping, f))
                if v:
                    vals[f] = v
            for f in ("price", "cost_price", "min_stock"):
                if f in mapping:
                    vals[f] = num(get(r, mapping, f))
            for f in ("units_code", "vat_percent", "shelf_days"):
                if f in mapping:
                    vals[f] = int(num(get(r, mapping, f)))
            if "price" in mapping and not vals.get("price"):
                err(i, f"«{vals['name']}»: нет цены продажи (товар загружен с ценой 0)")
                st["skipped"] -= 1
            uid = ctx.find_product(bc, sku, None if (bc or sku) else name)
            vals["updated_at"] = t
            if uid:
                if not opts.get("update_existing", True):
                    st["skipped"] += 1
                    continue
                c.execute(f"UPDATE products SET {','.join(k + '=?' for k in vals)} WHERE uid=?", list(vals.values()) + [uid])
                st["updated"] += 1
            else:
                uid = uuid.uuid4().hex
                vals.update(uid=uid, created_by="Переезд")
                c.execute(f"INSERT INTO products({','.join(vals)}) VALUES({','.join('?' * len(vals))})", list(vals.values()))
                if bc:
                    ctx.by_bc[bc] = uid
                if sku:
                    ctx.by_sku[sku] = uid
                ctx.by_name[norm(name)] = uid
                st["created"] += 1

    elif kind == "stock":
        mode = "add" if opts.get("mode") == "add" else "set"
        agg = {}
        for i, r in enumerate(rows, 1):
            st["rows"] += 1
            bc, sku, name = code(get(r, mapping, "barcode")), code(get(r, mapping, "sku")), get(r, mapping, "name")
            if not (bc or sku or name):
                err(i, "нет штрихкода/артикула/названия")
                continue
            if is_total(name) or is_total(get(r, mapping, "store")):
                st["rows"] -= 1
                continue
            sid = ctx.find_store(get(r, mapping, "store"), st)
            if not sid:
                err(i, f"не найден магазин «{get(r, mapping, 'store') or ''}»" if get(r, mapping, "store") else "не выбран магазин")
                continue
            uid = ctx.find_product(bc, sku, name)
            if not uid:
                if opts.get("create_products") and name:
                    uid = ctx.create_product(bc, sku, str(name).strip(), num(get(r, mapping, "price")),
                                             num(get(r, mapping, "cost_price")), get(r, mapping, "category") or "")
                    st["products_created"] += 1
                else:
                    err(i, f"товар не найден в каталоге: {name or ''} {bc or sku}")
                    continue
            q = num(get(r, mapping, "qty"))
            agg[(sid, uid)] = agg.get((sid, uid), 0) + q      # одинаковые строки складываем
        for (sid, uid), q in agg.items():
            ex = c.execute("SELECT qty FROM stock WHERE store_id=? AND product_uid=?", (sid, uid)).fetchone()
            new_q = q if mode == "set" else (ex["qty"] if ex else 0) + q
            c.execute("""INSERT INTO stock(store_id,product_uid,qty,updated_at) VALUES(?,?,?,?)
                         ON CONFLICT(store_id,product_uid) DO UPDATE SET qty=excluded.qty, updated_at=excluded.updated_at""",
                      (sid, uid, new_q, t))
            c.execute("INSERT INTO pending_stock(store_id,product_uid,qty,mode,import_id,created_at) VALUES(?,?,?,?,?,?)",
                      (sid, uid, q, mode, import_id, t))
            st["created"] += 1
            st["sum"] += q
            per(sid)

    elif kind == "customers":
        for i, r in enumerate(rows, 1):
            st["rows"] += 1
            nm, ph = get(r, mapping, "customer"), phone(get(r, mapping, "phone"))
            if is_total(nm):
                st["rows"] -= 1
                continue
            if not nm and not ph:
                err(i, "нет имени и телефона")
                continue
            sid = ctx.find_store(get(r, mapping, "store"), st)
            if not sid:
                err(i, "не выбран магазин клиента")
                continue
            data = {"name": str(nm or "").strip() or ("Клиент " + (ph or "")[-4:]), "phone": ph,
                    "card": code(get(r, mapping, "card")) or None, "bonus": max(0, num(get(r, mapping, "bonus"))),
                    "debt": max(0, num(get(r, mapping, "debt"))), "discount_pct": num(get(r, mapping, "discount_pct")),
                    "birthday": day(get(r, mapping, "birthday"))}
            c.execute("INSERT INTO pending_customers(store_id,data,import_id,created_at) VALUES(?,?,?,?)",
                      (sid, json.dumps(data, ensure_ascii=False), import_id, t))
            st["created"] += 1
            st["sum"] += data["debt"]
            per(sid)

    elif kind == "sales":
        groups = {}
        for i, r in enumerate(rows, 1):
            st["rows"] += 1
            d = day(get(r, mapping, "date"))
            if is_total(get(r, mapping, "date")) or is_total(get(r, mapping, "name")):
                st["rows"] -= 1
                continue
            if not d:
                err(i, f"не распознана дата «{get(r, mapping, 'date')}»")
                continue
            bc, sku, name = code(get(r, mapping, "barcode")), code(get(r, mapping, "sku")), get(r, mapping, "name")
            uid = ctx.find_product(bc, sku, name)
            if not uid:
                if opts.get("create_products") and name:
                    uid = ctx.create_product(bc, sku, str(name).strip(), num(get(r, mapping, "price")))
                    st["products_created"] += 1
                else:
                    err(i, f"товар не найден в каталоге: {name or ''} {bc or sku}")
                    continue
            sid = ctx.find_store(get(r, mapping, "store"), st)
            if not sid:
                err(i, "не выбран магазин")
                continue
            q = num(get(r, mapping, "qty"))
            total = num(get(r, mapping, "total")) if "total" in mapping else q * num(get(r, mapping, "price"))
            key = (sid, d, str(get(r, mapping, "receipt") or ""))
            groups.setdefault(key, []).append((uid, name, q, total))
            c.execute("INSERT INTO pending_history(store_id,product_uid,day,qty,import_id,created_at) VALUES(?,?,?,?,?,?)",
                      (sid, uid, d, q, import_id, t))
        costs = {p["uid"]: p["cost_price"] or 0 for p in c.execute("SELECT uid, cost_price FROM products").fetchall()}
        names = {p["uid"]: p["name"] for p in c.execute("SELECT uid, name FROM products").fetchall()}
        for (sid, d, rc), items in groups.items():
            suid = uuid.uuid4().hex
            tot = sum(x[3] for x in items)
            c.execute("""INSERT INTO sales(uid,store_id,created_at,operation,total,cash,card,debt,discount,cashier,fiscal_status)
                         VALUES(?,?,?,?,?,?,?,0,0,?,?)""", (suid, sid, d + " 12:00:00", "sale", tot, tot, 0, "Переезд", "import"))
            for uid, nm, q, total in items:
                c.execute("INSERT INTO sale_items(sale_uid,product_uid,name,qty,price,total,cost_price) VALUES(?,?,?,?,?,?,?)",
                          (suid, uid, names.get(uid) or nm, q, total / q if q else 0, total, costs.get(uid, 0)))
            st["created"] += len(items)
            st["sum"] += tot
            per(sid)

    st["stores_missing"] = sorted(st["stores_missing"])
    return st


# ---------------------------------------------------------------- API
def owner_only():
    from flask import session
    return bool(session.get("owner"))


@bp.get("/api/migrate/kinds")
def kinds():
    if not owner_only():
        return H.err("Требуется вход", 401)
    return jsonify(kinds={k: {"title": v["title"], "fields": [{"key": f, "label": LABELS[f], "required": f in v["required"]}
                                                                for f in v["fields"]], "one_of": v.get("one_of", [])}
                          for k, v in KINDS.items()})


@bp.post("/api/migrate/upload")
def upload():
    if not owner_only():
        return H.err("Требуется вход", 401)
    f = request.files.get("file")
    kind = request.form.get("kind")
    if not f or kind not in KINDS:
        return H.err("Выберите тип данных и файл")
    try:
        rows = read_any(f)
    except Exception as e:
        return H.err("Не удалось прочитать файл: " + str(e))
    if not rows:
        return H.err("Файл пустой")
    hi, headers, mapping = detect(rows, kind)
    data = rows[hi + 1:]

    def ser(v):
        return v.strftime("%d.%m.%Y") if hasattr(v, "strftime") else v
    data = [[ser(c) for c in r] for r in data]
    c = H.con()
    c.execute("INSERT INTO imports(kind,filename,created_at,status,header_row,headers,rows) VALUES(?,?,?,?,?,?,?)",
              (kind, f.filename, H.now(), "uploaded", hi, json.dumps(headers, ensure_ascii=False),
               json.dumps(data, ensure_ascii=False, default=str)))
    iid = c.execute("SELECT MAX(id) AS id FROM imports").fetchone()["id"]
    c.commit()
    c.close()
    return jsonify(id=iid, kind=kind, header_row=hi + 1, headers=headers, mapping=mapping, total=len(data),
                   sample=data[:8])


def _run(iid, dry):
    d = request.json or {}
    c = H.con()
    imp = c.execute("SELECT * FROM imports WHERE id=?", (iid,)).fetchone()
    if not imp:
        c.close()
        return H.err("Загрузка не найдена", 404)
    kind = imp["kind"]
    mapping = d.get("mapping") or {}
    spec = KINDS[kind]
    missing = [LABELS[f] for f in spec["required"] if mapping.get(f) in (None, "", -1, "-1")]
    if spec.get("one_of") and not any(mapping.get(f) not in (None, "", -1, "-1") for f in spec["one_of"]):
        missing.append(" или ".join(LABELS[f] for f in spec["one_of"]))
    if missing:
        c.close()
        return H.err("Укажите колонки: " + ", ".join(missing))
    if kind in ("stock", "customers", "sales") and mapping.get("store") in (None, "", -1, "-1") and not d.get("options", {}).get("store_id"):
        c.close()
        return H.err("Выберите магазин или колонку «Магазин»")
    rows = json.loads(imp["rows"])
    try:
        st = process(kind, rows, mapping, d.get("options") or {}, c, iid)
    except Exception as e:
        c.close()
        return H.err("Ошибка обработки: " + str(e)[:300])
    if dry:
        c.rollback() if hasattr(c, "rollback") else None
    else:
        c.execute("UPDATE imports SET status='applied', result=?, rows='[]' WHERE id=?",
                  (json.dumps({k: v for k, v in st.items() if k != "errors"} | {"errors_n": len(st["errors"])},
                              ensure_ascii=False), iid))
        c.commit()
    c.close()
    return jsonify(st)


@bp.post("/api/migrate/<int:iid>/check")
def check(iid):
    if not owner_only():
        return H.err("Требуется вход", 401)
    return _run(iid, True)


@bp.post("/api/migrate/<int:iid>/apply")
def apply(iid):
    if not owner_only():
        return H.err("Требуется вход", 401)
    return _run(iid, False)


@bp.get("/api/migrate")
def history():
    if not owner_only():
        return H.err("Требуется вход", 401)
    rows = H.q("SELECT id, kind, filename, created_at, status, result FROM imports WHERE status='applied' ORDER BY id DESC LIMIT 50")
    for r in rows:
        r["result"] = json.loads(r["result"] or "{}")
        pend = 0
        for t in ("pending_stock", "pending_customers", "pending_history"):
            pend += H.q(f"SELECT COUNT(*) AS n FROM {t} WHERE import_id=? AND applied_at IS NULL", (r["id"],), one=True)["n"]
        r["pending"] = pend
        r["title"] = KINDS[r["kind"]]["title"]
    return jsonify(rows)


# ---------------------------------------------------------------- доставка в кассы (через /sync)
def pending_for(c, store_id, limit=3000):
    out = {}
    out["stock"] = [dict(r) for r in c.execute(
        "SELECT id, product_uid AS uid, qty, mode FROM pending_stock WHERE store_id=? AND applied_at IS NULL ORDER BY id LIMIT ?",
        (store_id, limit))]
    out["customers"] = [dict(r, data=json.loads(r["data"])) for r in c.execute(
        "SELECT id, data FROM pending_customers WHERE store_id=? AND applied_at IS NULL ORDER BY id LIMIT ?", (store_id, limit))]
    out["history"] = [dict(r) for r in c.execute(
        "SELECT id, product_uid AS uid, day, qty FROM pending_history WHERE store_id=? AND applied_at IS NULL ORDER BY id LIMIT ?",
        (store_id, limit))]
    return out


@bp.post("/sync/ack")
def sync_ack():
    st = H.store_auth()
    if not st:
        return H.err("Неверный токен магазина", 401)
    d = request.json or {}
    c = H.con()
    t = H.now()
    for key, table in (("stock", "pending_stock"), ("customers", "pending_customers"), ("history", "pending_history")):
        ids = [int(x) for x in d.get(key) or []]
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            c.execute(f"UPDATE {table} SET applied_at=? WHERE store_id=? AND id IN ({','.join('?' * len(chunk))})",
                      [t, st["id"]] + chunk)
    c.commit()
    c.close()
    return jsonify(ok=True)


def register(hq_module):
    global H
    H = hq_module
    c = H.con()
    c.executescript(MIG_SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY").replace(" REAL", " DOUBLE PRECISION")
                    if H.PG else MIG_SCHEMA)
    c.commit()
    c.close()
    H.app.register_blueprint(bp)
