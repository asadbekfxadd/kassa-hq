"""
Пользователи Центра, 8 ролей, права и доступ по магазинам, журнал действий.

Вход: логин (телефон/почта) + пароль. Старый вход по одному паролю HQ_PASSWORD остаётся —
это учётная запись Super Admin (на случай, если все пароли забыты).
Права проверяются централизованно в before_request по таблице RULES; данные чужих магазинов
отсекаются в запросах через store_ids()/sf().
"""
import re

from flask import Blueprint, g, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

bp = Blueprint("rbac", __name__)
H = None

ROLES = {
    "superadmin": ("Super Admin", "Полный доступ, включая управление владельцами"),
    "owner": ("Владелец", "Управление всей сетью"),
    "director": ("Директор", "Управление операциями сети"),
    "regional": ("Региональный менеджер", "Группа магазинов"),
    "store_manager": ("Управляющий магазином", "Свой магазин"),
    "warehouse": ("Заведующий складом", "Склад, поставки и перемещения своих магазинов"),
    "cashier": ("Кассир", "Продажи своего магазина"),
    "analyst": ("Аналитик", "Аналитика и отчёты всей сети, без изменений"),
}
PERMS = {
    "dash": "Обзор сети", "stores.view": "Магазины: просмотр", "stores.edit": "Магазины: создание и изменение",
    "plans.edit": "Планы продаж", "users": "Пользователи и роли", "catalog.view": "Каталог: просмотр",
    "catalog.edit": "Каталог: изменение цен и товаров", "stock": "Остатки", "docs.view": "Поставки и перемещения: просмотр",
    "docs.create": "Поставки и перемещения: создание", "sales": "Продажи и отчёты", "finance": "Прибыль, себестоимость, рентабельность",
    "migrate": "Переезд данных", "audit": "Журнал действий",
}
_ALL = set(PERMS)
ROLE_PERMS = {
    "superadmin": _ALL,
    "owner": _ALL,
    "director": _ALL - {"users", "migrate"},
    "regional": {"dash", "stores.view", "plans.edit", "catalog.view", "stock", "docs.view", "docs.create", "sales", "finance"},
    "store_manager": {"dash", "stores.view", "catalog.view", "stock", "docs.view", "docs.create", "sales", "finance"},
    "warehouse": {"catalog.view", "stock", "docs.view", "docs.create"},
    "cashier": {"dash", "sales"},
    "analyst": {"dash", "stores.view", "catalog.view", "stock", "docs.view", "sales", "finance", "audit"},
}
ALL_STORES = {"superadmin", "owner", "director", "analyst"}   # видят всю сеть без привязки

# (методы, путь-регулярка, право). Первое совпадение решает. Путь /api/... без правила — нужен только вход.
RULES = [
    ("GET", r"^/api/(dash2|dashboard|alerts)$", "dash"),
    ("GET", r"^/api/stores(/\d+/card)?$", "stores.view"),
    ("GET", r"^/api/stores/compare$", "stores.view"),
    ("POST", r"^/api/stores/\d+/plan$", "plans.edit"),
    ("POST", r"^/api/stores(/\d+/token)?$", "stores.edit"),
    ("*", r"^/api/(users|roles)", "users"),
    ("GET", r"^/api/audit$", "audit"),
    ("GET", r"^/api/products", "catalog.view"),
    ("*", r"^/api/products", "catalog.edit"),
    ("GET", r"^/api/stock", "stock"),
    ("GET", r"^/api/docs", "docs.view"),
    ("*", r"^/api/docs", "docs.create"),
    ("GET", r"^/api/sales_report$", "sales"),
    ("*", r"^/api/migrate", "migrate"),
    ("GET", r"^/api/update/info$", "stores.view"),
]
OPEN = {"/api/login", "/api/logout", "/api/me"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT, login TEXT UNIQUE NOT NULL, name TEXT NOT NULL, phone TEXT, position TEXT,
  role TEXT NOT NULL, pw_hash TEXT NOT NULL, active INTEGER DEFAULT 1, created_at TEXT, last_login TEXT);
CREATE TABLE IF NOT EXISTS user_stores (user_id INTEGER NOT NULL, store_id INTEGER NOT NULL, PRIMARY KEY (user_id, store_id));
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, user_id INTEGER, user_name TEXT, action TEXT NOT NULL,
  details TEXT, store_id INTEGER, ip TEXT);
CREATE INDEX IF NOT EXISTS ix_audit_at ON audit(created_at);
"""


# ---------------------------------------------------------------- текущий пользователь
def load_user():
    if not session.get("owner"):
        return None
    uid = session.get("uid", 0)
    if not uid:     # вход по HQ_PASSWORD
        return {"id": 0, "name": "Super Admin", "login": "", "role": "superadmin", "perms": sorted(_ALL), "stores": None}
    u = H.q("SELECT id, name, login, role, active FROM users WHERE id=?", (uid,), one=True)
    if not u or not u["active"] or u["role"] not in ROLES:
        return None
    u["perms"] = sorted(ROLE_PERMS[u["role"]])
    u["stores"] = None if u["role"] in ALL_STORES else [r["store_id"] for r in H.q("SELECT store_id FROM user_stores WHERE user_id=?", (uid,))]
    return u


def user():
    return getattr(g, "user", None) or load_user()


def can(perm):
    u = user()
    return bool(u) and perm in u["perms"]


def store_ids():
    """None — все магазины, иначе список разрешённых id."""
    u = user()
    return None if not u else u["stores"]


def sf(col):
    """SQL-фрагмент фильтра по разрешённым магазинам: (' AND col IN (?,?)', (1,2))."""
    ids = store_ids()
    if ids is None:
        return "", ()
    if not ids:
        return " AND 1=0", ()
    return f" AND {col} IN ({','.join('?' * len(ids))})", tuple(ids)


def store_allowed(sid):
    ids = store_ids()
    return ids is None or (sid is not None and int(sid) in ids)


def audit(action, details="", store_id=None, c=None):
    u = user() or {"id": None, "name": "система"}
    own = c is None
    c = c or H.con()
    c.execute("INSERT INTO audit(created_at,user_id,user_name,action,details,store_id,ip) VALUES(?,?,?,?,?,?,?)",
              (H.now(), u["id"], u["name"], action, str(details)[:1000], store_id,
               request.headers.get("X-Forwarded-For", request.remote_addr or "")[:60] if request else ""))
    if own:
        c.commit()
        c.close()


def guard():
    p = request.path
    if not p.startswith("/api/") or p in OPEN:
        return None
    u = load_user()
    if not u:
        return H.err("Требуется вход", 401)
    g.user = u
    for meth, rx, perm in RULES:
        if (meth == "*" or meth == request.method) and re.search(rx, p):
            if perm not in u["perms"]:
                return H.err(f"Недостаточно прав: «{PERMS[perm]}». Обратитесь к владельцу сети.", 403)
            break
    return None


# ---------------------------------------------------------------- вход
@bp.post("/api/login")
def login():
    import os
    d = request.json or {}
    lg, pw = (d.get("login") or "").strip().lower(), d.get("password") or ""
    if not lg:
        hq_pw = os.environ.get("HQ_PASSWORD", "admin")
        if pw != hq_pw:
            return H.err("Неверный пароль", 401)
        session.clear()
        session.permanent = True
        session.update(owner=1, uid=0)
        g.user = load_user()
        audit("Вход", "Super Admin (общий пароль)")
        return jsonify(ok=True)
    u = H.q("SELECT * FROM users WHERE login=?", (lg,), one=True)
    if not u or not u["active"] or not check_password_hash(u["pw_hash"], pw):
        return H.err("Неверный логин или пароль", 401)
    session.clear()
    session.permanent = True
    session.update(owner=1, uid=u["id"])
    c = H.con()
    c.execute("UPDATE users SET last_login=? WHERE id=?", (H.now(), u["id"]))
    c.commit()
    c.close()
    g.user = load_user()
    audit("Вход", u["login"])
    return jsonify(ok=True)


@bp.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@bp.get("/api/me")
def me():
    import os
    u = load_user()
    if not u:
        return H.err("Требуется вход", 401)
    allowed = None if u["stores"] is None else H.q(
        f"SELECT id, name FROM stores WHERE id IN ({','.join('?' * len(u['stores']))})", tuple(u["stores"])) if u["stores"] else []
    return jsonify(ok=True, user={**u, "role_title": ROLES[u["role"]][0], "store_names": allowed},
                   default_password=os.environ.get("HQ_PASSWORD", "admin") == "admin" and u["role"] == "superadmin")


@bp.post("/api/me/password")
def change_password():
    u = load_user()
    if not u or not u["id"]:
        return H.err("Пароль Super Admin меняется переменной HQ_PASSWORD на сервере")
    d = request.json or {}
    row = H.q("SELECT pw_hash FROM users WHERE id=?", (u["id"],), one=True)
    if not check_password_hash(row["pw_hash"], d.get("old") or ""):
        return H.err("Текущий пароль неверный")
    if len(d.get("new") or "") < 6:
        return H.err("Новый пароль — не короче 6 символов")
    c = H.con()
    c.execute("UPDATE users SET pw_hash=? WHERE id=?", (generate_password_hash(d["new"]), u["id"]))
    c.commit()
    c.close()
    audit("Смена пароля", u["login"])
    return jsonify(ok=True)


# ---------------------------------------------------------------- пользователи
@bp.get("/api/roles")
def roles():
    return jsonify(roles=[{"key": k, "title": v[0], "desc": v[1], "perms": sorted(ROLE_PERMS[k]), "all_stores": k in ALL_STORES}
                          for k, v in ROLES.items()], perms=PERMS)


@bp.get("/api/users")
def users_list():
    rows = H.q("SELECT id, login, name, phone, position, role, active, created_at, last_login FROM users ORDER BY active DESC, name")
    links = {}
    for r in H.q("SELECT us.user_id, s.id, s.name FROM user_stores us JOIN stores s ON s.id=us.store_id"):
        links.setdefault(r["user_id"], []).append({"id": r["id"], "name": r["name"]})
    for r in rows:
        r["stores"] = links.get(r["id"], [])
        r["role_title"] = ROLES.get(r["role"], (r["role"],))[0]
    return jsonify(rows)


@bp.post("/api/users")
def user_save():
    me_ = user()
    d = request.json or {}
    role = d.get("role")
    if role not in ROLES:
        return H.err("Выберите роль")
    if role in ("superadmin", "owner") and me_["role"] not in ("superadmin", "owner"):
        return H.err("Назначать владельцев может только владелец", 403)
    if role == "superadmin" and me_["role"] != "superadmin":
        return H.err("Роль Super Admin назначает только Super Admin", 403)
    name, lg = (d.get("name") or "").strip(), (d.get("login") or "").strip().lower()
    if not name or not lg:
        return H.err("Укажите имя и логин (телефон или почта)")
    stores_ = [int(x) for x in d.get("stores") or []]
    if role not in ALL_STORES and not stores_:
        return H.err("Выберите магазин(ы), к которым у сотрудника будет доступ")
    if role in ("store_manager", "cashier") and len(stores_) > 1:
        return H.err("Управляющий и кассир привязываются к одному магазину")
    c = H.con()
    dup = c.execute("SELECT id FROM users WHERE login=? AND id<>?", (lg, int(d.get("id") or 0))).fetchone()
    if dup:
        c.close()
        return H.err("Такой логин уже есть")
    if d.get("id"):
        uid = int(d["id"])
        old = c.execute("SELECT role FROM users WHERE id=?", (uid,)).fetchone()
        if old and dict(old)["role"] == "superadmin" and me_["role"] != "superadmin":
            c.close()
            return H.err("Изменять Super Admin может только Super Admin", 403)
        c.execute("UPDATE users SET login=?, name=?, phone=?, position=?, role=?, active=? WHERE id=?",
                  (lg, name, d.get("phone", ""), d.get("position", ""), role, int(d.get("active", 1)), uid))
        if d.get("password"):
            if len(d["password"]) < 6:
                c.close()
                return H.err("Пароль — не короче 6 символов")
            c.execute("UPDATE users SET pw_hash=? WHERE id=?", (generate_password_hash(d["password"]), uid))
        act = "Изменён пользователь"
    else:
        if len(d.get("password") or "") < 6:
            c.close()
            return H.err("Пароль — не короче 6 символов")
        c.execute("INSERT INTO users(login,name,phone,position,role,pw_hash,active,created_at) VALUES(?,?,?,?,?,?,1,?)",
                  (lg, name, d.get("phone", ""), d.get("position", ""), role, generate_password_hash(d["password"]), H.now()))
        uid = dict(c.execute("SELECT id FROM users WHERE login=?", (lg,)).fetchone())["id"]
        act = "Создан пользователь"
    c.execute("DELETE FROM user_stores WHERE user_id=?", (uid,))
    if role not in ALL_STORES:
        for s in stores_:
            c.execute("INSERT INTO user_stores(user_id,store_id) VALUES(?,?)", (uid, s))
    audit(act, f"{name} ({lg}) — {ROLES[role][0]}" + (f", магазины: {stores_}" if role not in ALL_STORES else ""), c=c)
    c.commit()
    c.close()
    return jsonify(ok=True, id=uid)


@bp.get("/api/audit")
def audit_list():
    sid = request.args.get("store")
    f, a = sf("store_id")
    where, args = "1=1" + f, list(a)
    if sid:
        where += " AND store_id=?"
        args.append(int(sid))
    return jsonify(H.q(f"SELECT * FROM audit WHERE {where} ORDER BY id DESC LIMIT 500", args))


def register(hq_module):
    global H
    H = hq_module
    c = H.con()
    c.executescript(SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY") if H.PG else SCHEMA)
    c.commit()
    c.close()
    H.app.before_request(guard)
    H.app.register_blueprint(bp)
