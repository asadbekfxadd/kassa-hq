import os
import shutil
import sqlite3
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "kassa.db")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    pin TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL DEFAULT 'cashier',      -- admin | cashier
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    barcode TEXT UNIQUE,
    sku TEXT,
    category_id INTEGER REFERENCES categories(id),
    unit_name TEXT DEFAULT 'шт',
    units_code INTEGER DEFAULT 0,              -- код ед. измерения (tasnif.soliq.uz)
    spic TEXT DEFAULT '',                      -- ИКПУ
    package_code TEXT DEFAULT '',              -- код упаковки
    owner_type INTEGER DEFAULT 0,
    vat_percent INTEGER DEFAULT 12,
    cost_price REAL DEFAULT 0,
    price REAL NOT NULL DEFAULT 0,
    stock REAL NOT NULL DEFAULT 0,
    min_stock REAL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_products_name ON products(name);

CREATE TABLE IF NOT EXISTS shifts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    opening_cash REAL DEFAULT 0,
    closing_cash REAL,
    zreport_status TEXT DEFAULT '',            -- ok | error:... | off
    status TEXT NOT NULL DEFAULT 'open'
);

CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_id INTEGER REFERENCES shifts(id),
    user_id INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    operation TEXT NOT NULL DEFAULT 'sale',    -- sale | refund
    refund_of INTEGER REFERENCES sales(id),
    subtotal REAL NOT NULL DEFAULT 0,
    discount REAL NOT NULL DEFAULT 0,
    total REAL NOT NULL DEFAULT 0,
    cash REAL NOT NULL DEFAULT 0,              -- наличными (нетто, без сдачи)
    card REAL NOT NULL DEFAULT 0,
    given REAL NOT NULL DEFAULT 0,             -- сколько дал клиент наличными
    change REAL NOT NULL DEFAULT 0,
    customer_phone TEXT DEFAULT '',
    fiscal_status TEXT NOT NULL DEFAULT 'none', -- none | pending | ok | error
    fiscal_txid TEXT,
    fiscal_json TEXT,
    terminal_id TEXT,
    receipt_seq INTEGER,
    fiscal_datetime TEXT,
    fiscal_sign TEXT,
    qr_url TEXT,
    fiscal_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_sales_created ON sales(created_at);

CREATE TABLE IF NOT EXISTS sale_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id INTEGER NOT NULL REFERENCES sales(id),
    product_id INTEGER REFERENCES products(id),
    name TEXT NOT NULL,
    barcode TEXT,
    qty REAL NOT NULL,
    price REAL NOT NULL,
    discount REAL NOT NULL DEFAULT 0,
    total REAL NOT NULL,
    vat_percent INTEGER DEFAULT 0,
    cost_price REAL DEFAULT 0,
    spic TEXT, package_code TEXT, units_code INTEGER, owner_type INTEGER
);

CREATE TABLE IF NOT EXISTS purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    user_id INTEGER,
    supplier TEXT,
    note TEXT,
    total REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS purchase_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_id INTEGER NOT NULL REFERENCES purchases(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    qty REAL NOT NULL,
    cost REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS stock_moves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    created_at TEXT NOT NULL,
    type TEXT NOT NULL,                        -- sale|refund|purchase|writeoff|inventory
    qty REAL NOT NULL,                         -- +приход / -расход
    ref_id INTEGER,
    user_id INTEGER,
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_moves_product ON stock_moves(product_id);

CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT UNIQUE,
    card TEXT,
    discount_pct REAL DEFAULT 0,               -- персональная скидка
    bonus REAL NOT NULL DEFAULT 0,             -- бонусный баланс
    debt REAL NOT NULL DEFAULT 0,              -- долг (насия)
    debt_limit REAL DEFAULT 0,                 -- 0 = без лимита
    birthday TEXT,
    note TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS customer_ops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    created_at TEXT NOT NULL,
    type TEXT NOT NULL,                        -- bonus_add|bonus_use|bonus_back|debt_add|debt_pay|debt_back
    amount REAL NOT NULL,
    sale_id INTEGER,
    user_id INTEGER,
    method TEXT,                               -- cash|card (для погашения долга)
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_cops_customer ON customer_ops(customer_id);

CREATE TABLE IF NOT EXISTS cash_ops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_id INTEGER REFERENCES shifts(id),
    created_at TEXT NOT NULL,
    type TEXT NOT NULL,                        -- in|out|expense|debt_in
    method TEXT NOT NULL DEFAULT 'cash',
    amount REAL NOT NULL,
    category TEXT,
    note TEXT,
    user_id INTEGER
);

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    user_id INTEGER,
    action TEXT NOT NULL,
    details TEXT,
    severity INTEGER DEFAULT 0                 -- 0 инфо, 1 внимание, 2 важно
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit(created_at);

CREATE TABLE IF NOT EXISTS docs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT UNIQUE,
    number TEXT,
    type TEXT NOT NULL,                        -- in_supply|in_transfer|local_supply|out_transfer
    status TEXT NOT NULL,                      -- expected|receiving|received|to_ship|shipped|cancelled
    partner TEXT,                              -- поставщик или магазин
    partner_uid TEXT,                          -- uid магазина-получателя (для отгрузки)
    note TEXT,
    created_at TEXT NOT NULL,
    done_at TEXT,
    user_id INTEGER,
    synced INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS doc_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL REFERENCES docs(id),
    product_id INTEGER REFERENCES products(id),
    product_uid TEXT,
    name TEXT,
    barcode TEXT,
    qty_expected REAL NOT NULL DEFAULT 0,
    qty_fact REAL NOT NULL DEFAULT 0,
    cost REAL DEFAULT 0,
    price REAL DEFAULT 0,
    scanned_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_dl_doc ON doc_lines(doc_id);

CREATE TABLE IF NOT EXISTS product_barcodes (       -- упаковки: блок, ящик, второй штрихкод
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    barcode TEXT NOT NULL UNIQUE,
    qty REAL NOT NULL DEFAULT 1,
    name TEXT
);
CREATE TABLE IF NOT EXISTS batches (                 -- партии со сроком годности
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    qty REAL NOT NULL,
    expiry TEXT,
    created_at TEXT NOT NULL,
    source TEXT
);
CREATE INDEX IF NOT EXISTS idx_batches_product ON batches(product_id);
CREATE TABLE IF NOT EXISTS suppliers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    phone TEXT, inn TEXT, note TEXT,
    balance REAL NOT NULL DEFAULT 0,             -- > 0 — мы должны поставщику
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS supplier_ops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    created_at TEXT NOT NULL,
    type TEXT NOT NULL,                          -- purchase|payment|correction
    amount REAL NOT NULL,
    method TEXT, ref_id INTEGER, note TEXT, user_id INTEGER
);

CREATE TABLE IF NOT EXISTS promos (                  -- акции
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,                          -- percent | nplus
    value REAL DEFAULT 0,                        -- % скидки
    n INTEGER DEFAULT 0, m INTEGER DEFAULT 0,    -- купи N, M в подарок
    products TEXT DEFAULT '[]', categories TEXT DEFAULT '[]',   -- пусто в обоих = на всё
    date_from TEXT, date_to TEXT, time_from TEXT, time_to TEXT,
    weekdays TEXT DEFAULT '',                    -- "1,2,3" (1=пн), пусто = все дни
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS revaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL, user_id INTEGER, note TEXT, lines INTEGER, diff_sum REAL
);
CREATE TABLE IF NOT EXISTS revaluation_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    revaluation_id INTEGER NOT NULL, product_id INTEGER NOT NULL,
    old_price REAL, new_price REAL, stock REAL
);

CREATE TABLE IF NOT EXISTS parked (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    user_id INTEGER,
    title TEXT,
    data TEXT NOT NULL
);
"""

MIGRATIONS = [
    ("sales", "customer_id", "INTEGER"),
    ("sales", "bonus_used", "REAL NOT NULL DEFAULT 0"),
    ("sales", "bonus_earned", "REAL NOT NULL DEFAULT 0"),
    ("sales", "debt", "REAL NOT NULL DEFAULT 0"),
    ("sales", "uid", "TEXT"),
    ("sales", "synced", "INTEGER NOT NULL DEFAULT 0"),
    ("products", "uid", "TEXT"),
    ("products", "stock_at", "TEXT"),
    ("products", "hq", "INTEGER NOT NULL DEFAULT 0"),
    ("products", "requires_marking", "INTEGER NOT NULL DEFAULT 0"),
    ("products", "restrict_kind", "INTEGER NOT NULL DEFAULT 0"),   # 1 — 18+, 2 — алкоголь (18+ и часы продажи)
    ("products", "shelf_days", "INTEGER NOT NULL DEFAULT 0"),      # срок годности по умолчанию, дней
    ("products", "quick", "INTEGER NOT NULL DEFAULT 0"),           # быстрая кнопка на кассе
    ("products", "supplier_id", "INTEGER"),
    ("sale_items", "marking", "TEXT"),
    ("purchases", "supplier_id", "INTEGER"),
    ("purchases", "paid", "REAL NOT NULL DEFAULT 0"),
    ("purchases", "due_date", "TEXT"),
    ("doc_lines", "expiry", "TEXT"),
    ("products", "wholesale_price", "REAL NOT NULL DEFAULT 0"),
    ("products", "brand", "TEXT"),
    ("docs", "picking", "INTEGER NOT NULL DEFAULT 0"),
    ("sales", "promo_code", "TEXT"),
    ("sales", "promo_disc", "REAL NOT NULL DEFAULT 0"),
    ("sales", "qr_provider", "TEXT"),
    ("customers", "price_type", "TEXT NOT NULL DEFAULT 'retail'"),   # retail | wholesale
    ("sales", "qr", "REAL NOT NULL DEFAULT 0"),
    ("sale_items", "promo", "TEXT"),
]

# uid для синхронизации, отметка изменения остатка, повторная отправка при смене статуса ФМ
TRIGGERS = """
UPDATE products SET uid=lower(hex(randomblob(16))) WHERE uid IS NULL;
UPDATE sales SET uid=lower(hex(randomblob(16))) WHERE uid IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_products_uid ON products(uid);
CREATE UNIQUE INDEX IF NOT EXISTS ux_sales_uid ON sales(uid);
CREATE INDEX IF NOT EXISTS ix_sales_synced ON sales(synced);
CREATE TABLE IF NOT EXISTS price_history (      -- любая смена цены/себестоимости: карточка, переоценка, уценка, Центр
    id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL, created_at TEXT NOT NULL,
    old_price REAL, new_price REAL, old_cost REAL, new_cost REAL, source TEXT, user_id INTEGER);
CREATE INDEX IF NOT EXISTS ix_ph_product ON price_history(product_id, id);
CREATE TRIGGER IF NOT EXISTS tr_products_price AFTER UPDATE OF price, cost_price ON products
WHEN abs(COALESCE(OLD.price,0)-COALESCE(NEW.price,0))>0.001 OR abs(COALESCE(OLD.cost_price,0)-COALESCE(NEW.cost_price,0))>0.001
BEGIN INSERT INTO price_history(product_id,created_at,old_price,new_price,old_cost,new_cost,source,user_id)
      VALUES(NEW.id, datetime('now','localtime'), OLD.price, NEW.price, OLD.cost_price, NEW.cost_price, ctx_src(), ctx_user()); END;
CREATE TABLE IF NOT EXISTS supplier_returns (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, supplier_id INTEGER REFERENCES suppliers(id),
    user_id INTEGER, reason TEXT, note TEXT, lines INTEGER DEFAULT 0, qty REAL DEFAULT 0, total REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS supplier_return_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT, return_id INTEGER NOT NULL REFERENCES supplier_returns(id),
    product_id INTEGER NOT NULL, name TEXT, qty REAL NOT NULL, cost REAL NOT NULL DEFAULT 0);
CREATE TRIGGER IF NOT EXISTS tr_products_uid AFTER INSERT ON products WHEN NEW.uid IS NULL
BEGIN UPDATE products SET uid=lower(hex(randomblob(16))), stock_at=datetime('now','localtime') WHERE id=NEW.id; END;
CREATE TRIGGER IF NOT EXISTS tr_products_stock AFTER UPDATE OF stock ON products
BEGIN UPDATE products SET stock_at=datetime('now','localtime') WHERE id=NEW.id; END;
CREATE TRIGGER IF NOT EXISTS tr_sales_uid AFTER INSERT ON sales WHEN NEW.uid IS NULL
BEGIN UPDATE sales SET uid=lower(hex(randomblob(16))) WHERE id=NEW.id; END;
CREATE TRIGGER IF NOT EXISTS tr_sales_fiscal AFTER UPDATE OF fiscal_status ON sales
BEGIN UPDATE sales SET synced=0 WHERE id=NEW.id; END;
"""

DEFAULT_SETTINGS = {
    "store_name": "Мой магазин",
    "legal_name": "",
    "inn": "",
    "address": "",
    "phone": "",
    "receipt_footer": "Спасибо за покупку!",
    "receipt_width": "80",
    "currency": "сум",
    "default_vat": "12",
    "fiscal_mode": "mock",           # off | mock | service
    "fiscal_url": "http://127.0.0.1:3449",
    "fiscal_factory_id": "",
    "fiscal_pos_secret": "",
    "qr_providers": "payme,click,uzum",
    "payme_merchant_id": "", "payme_key": "",
    "click_service_id": "", "click_merchant_id": "", "click_secret": "",
    "uzum_merchant_id": "", "uzum_secret": "",
    "geo_lat": "",
    "geo_lng": "",
    "auto_print": "1",
    "allow_negative_stock": "1",
    "sync_interval_min": "5",
    "theme_color": "#1d4ed8",
    # лояльность
    "bonus_pct": "2",                 # % кэшбэка бонусами с покупки
    "bonus_max_pay_pct": "50",        # сколько % чека можно оплатить бонусами
    # контроль кассиров
    "cashier_max_discount": "10",     # макс. скидка кассира без подтверждения, %
    "refund_needs_admin": "1",
    "delete_line_needs_admin": "0",
    # весовой товар: штрихкод весов  PP CCCCC WWWWW K
    "weight_prefixes": "20,21,22,23,24,25,26,27,28,29",
    # Telegram владельцу
    "tg_token": "",
    "tg_chat_id": "",
    "tg_daily_hour": "22",
    "tg_notify_shift": "1",
    "tg_notify_alerts": "1",
    "big_refund_alert": "500000",
    # автозаказ
    "reorder_cover_days": "14",
    # продукты
    "marking_strict": "1",           # не продавать маркируемый товар без скана DataMatrix
    "alcohol_from": "",              # часы продажи алкоголя, напр. 10:00 (пусто — без ограничений)
    "alcohol_to": "",                # напр. 22:00
    "expiry_warn_days": "3",
    # сеть магазинов
    "hq_url": "",
    "hq_token": "",
    "hq_since": "",
    "hq_stock_push": "",
    "hq_last_ok": "",
    "hq_error": "",
    "hq_store_name": "",
    "hq_stores": "[]",
}


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


PRICE_SOURCES = [("/api/revalue", "Переоценка"), ("/api/expiry/markdown", "Уценка"), ("/api/purchases", "Приход"),
                 ("/api/products/import", "Импорт"), ("/api/products", "Карточка товара"), ("/api/docs", "Приёмка")]


def _ctx_user():
    try:
        from flask import has_request_context, session
        return session.get("uid") if has_request_context() else None
    except Exception:
        return None


def _ctx_src():
    try:
        from flask import has_request_context, request, g
        if not has_request_context():
            return "Центр сети"
        if g.get("price_src"):
            return g.price_src
        return next((n for p, n in PRICE_SOURCES if request.path.startswith(p)), "Касса")
    except Exception:
        return "Касса"


def connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    # LIKE в SQLite не различает регистр только для латиницы — для кириллицы ищем через ulower()
    con.create_function("ulower", 1, lambda v: v.lower() if isinstance(v, str) else v, deterministic=True)
    # кто и откуда меняет цену — для триггера истории цен
    con.create_function("ctx_user", 0, _ctx_user)
    con.create_function("ctx_src", 0, _ctx_src)
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init_db():
    con = connect()
    con.executescript(SCHEMA)
    for table, col, typ in MIGRATIONS:
        cols = [r["name"] for r in con.execute(f"PRAGMA table_info({table})")]
        if col not in cols:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    con.executescript(TRIGGERS)
    for k, v in DEFAULT_SETTINGS.items():
        con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
    if not con.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        con.execute("INSERT INTO users(name,pin,role) VALUES('Администратор','0000','admin')")
    con.commit()
    con.close()


def backup_db(keep=30):
    """Ежедневная копия базы. Хранится 30 последних."""
    if not os.path.exists(DB_PATH):
        return None
    os.makedirs(BACKUP_DIR, exist_ok=True)
    name = "kassa_" + datetime.now().strftime("%Y%m%d") + ".db"
    dst = os.path.join(BACKUP_DIR, name)
    if not os.path.exists(dst):
        src = connect()
        out = sqlite3.connect(dst)
        src.backup(out)
        out.close()
        src.close()
    files = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith("kassa_"))
    for f in files[:-keep]:
        try:
            os.remove(os.path.join(BACKUP_DIR, f))
        except OSError:
            pass
    return dst


def get_settings(con=None):
    own = con is None
    con = con or connect()
    s = {r["key"]: r["value"] for r in con.execute("SELECT key,value FROM settings")}
    if own:
        con.close()
    return s
