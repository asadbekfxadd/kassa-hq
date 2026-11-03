"""
Центр (этап 9): резервные копии базы.

  • автоматически каждый день в 03:00 (Asia/Tashkent) и вручную — кнопкой в «Резервные копии»;
  • копия = zip: manifest.json + по файлу на таблицу (JSON Lines) — переносится между PostgreSQL и SQLite;
  • хранится 14 последних копий в самой базе (таблица hq_backups — переживает пересборку сервера); при включённом Telegram — копия уходит и в чат (вне сервера);
  • восстановление (только из консоли, чтобы его нельзя было запустить случайно из браузера):
        python hq/backup.py restore путь/к/копии.zip            — в пустую базу
        python hq/backup.py restore путь/к/копии.zip --force    — с заменой текущих данных
"""
import io
import json
import os
import sys
import zipfile
from datetime import datetime

from flask import Blueprint, Response, jsonify

bp = Blueprint("backup", __name__)
H = None
KEEP = 14
VERSION = 1


def bdir():
    d = os.path.join(H.DATA_DIR, "backups")
    os.makedirs(d, exist_ok=True)
    return d


def tables(c):
    if H.PG:
        rows = c.execute("SELECT table_name AS n FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'").fetchall()
    else:
        rows = c.execute("SELECT name AS n FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    return sorted(dict(r)["n"] for r in rows)


def _default(v):
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).hex()
    return str(v)


STORE_SQL = """CREATE TABLE IF NOT EXISTS hq_backups (name TEXT PRIMARY KEY, created_at TEXT, reason TEXT, rows_n INTEGER, size INTEGER, data BLOB)"""


def make(reason="manual"):
    """Создаёт копию, возвращает (имя, сводка). Копия хранится в самой базе (таблица hq_backups, переживает пересборку сервера)."""
    t = H.tnow()
    name = f"hq-backup-{t.strftime('%Y%m%d-%H%M%S')}.zip"
    c = H.con()
    n = 1
    while c.execute("SELECT 1 FROM hq_backups WHERE name=?", (name,)).fetchone():     # несколько копий в одну секунду
        n += 1
        name = f"hq-backup-{t.strftime('%Y%m%d-%H%M%S')}-{n}.zip"
    counts = {}
    buf = io.BytesIO()
    try:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for tb in tables(c):
                if tb == "hq_backups":
                    continue
                rows = c.execute(f'SELECT * FROM "{tb}"').fetchall()
                counts[tb] = len(rows)
                z.writestr(f"{tb}.jsonl", "\n".join(json.dumps(dict(r), ensure_ascii=False, default=_default) for r in rows))
            z.writestr("manifest.json", json.dumps({"version": VERSION, "created_at": t.strftime("%Y-%m-%d %H:%M:%S"), "reason": reason,
                                                     "engine": "postgresql" if H.PG else "sqlite", "tables": counts}, ensure_ascii=False, indent=1))
        data = buf.getvalue()
        c.execute("INSERT INTO hq_backups(name,created_at,reason,rows_n,size,data) VALUES(?,?,?,?,?,?)",
                  (name, t.strftime("%Y-%m-%d %H:%M:%S"), reason, sum(counts.values()), len(data), data))
        old = [r["name"] for r in (dict(x) for x in c.execute("SELECT name FROM hq_backups ORDER BY name DESC").fetchall())][KEEP:]
        for n in old:
            c.execute("DELETE FROM hq_backups WHERE name=?", (n,))
        c.commit()
    finally:
        c.close()
    # копия вне сервера — в Telegram владельцу (если подключён и файл не больше лимита Telegram)
    sent = None
    cfg = H.integrations.active("telegram")
    if cfg and len(data) < 45 * 1024 * 1024:
        path = os.path.join(bdir(), name)
        try:
            open(path, "wb").write(data)
            H.integrations.Telegram().send_file(cfg, path, f"💾 Резервная копия Центра {t.strftime('%d.%m.%Y %H:%M')} · {sum(counts.values())} записей")
            sent = "telegram"
        except Exception as e:
            sent = f"ошибка Telegram: {e}"
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
    return name, {"size": len(data), "rows": sum(counts.values()), "tables": len(counts), "sent": sent}


def tick():
    """Ежедневная копия в 03:00 (по времени Ташкента), один раз в сутки."""
    now = H.tnow()
    if now.hour < 3:
        return
    if H.q("SELECT 1 FROM hq_backups WHERE name LIKE ?", (f"hq-backup-{now.strftime('%Y%m%d')}%",), one=True):
        return
    name, info = make("auto")
    print("backup:", name, info)


@bp.get("/api/backups")
def b_list():
    rows = H.q("SELECT name, created_at, reason, rows_n AS rows, size FROM hq_backups ORDER BY name DESC")
    return jsonify(items=rows, keep=KEEP, telegram=bool(H.integrations.active("telegram")))


@bp.post("/api/backups")
def b_make():
    name, info = make("manual")
    H.rbac.audit("Резервная копия создана", f"{name}: {info['rows']} записей" + (f", {info['sent']}" if info["sent"] else ""))
    return jsonify(ok=True, name=name, **info)


@bp.get("/api/backups/<name>")
def b_get(name):
    r = H.q("SELECT data FROM hq_backups WHERE name=?", (name,), one=True)
    if not r:
        return H.err("Нет такой копии", 404)
    H.rbac.audit("Резервная копия скачана", name)
    return Response(bytes(r["data"]), mimetype="application/zip", headers={"Content-Disposition": f"attachment; filename={name}"})


# ---------------------------------------------------------------- восстановление (консоль)
def restore(path, force=False):
    with zipfile.ZipFile(path) as z:
        man = json.loads(z.read("manifest.json"))
        c = H.con()
        have = set(tables(c))
        if not force and "stores" in have and c.execute("SELECT COUNT(*) n FROM stores").fetchone()["n"]:
            c.close()
            raise SystemExit("В базе уже есть данные. Чтобы заменить их копией, добавьте --force")
        total = 0
        for tb in man["tables"]:
            if tb == "hq_backups":
                continue
            if tb not in have:
                print(f"  пропуск {tb}: таблицы нет в этой версии")
                continue
            cols_now = {getattr(d, "name", None) or d[0] for d in c.execute(f'SELECT * FROM "{tb}" LIMIT 0').description}
            c.execute(f'DELETE FROM "{tb}"')
            raw = z.read(f"{tb}.jsonl").decode()
            n = 0
            for line in raw.splitlines():
                if not line.strip():
                    continue
                r = {k: v for k, v in json.loads(line).items() if k in cols_now}
                c.execute(f'INSERT INTO "{tb}"({",".join(chr(34) + k + chr(34) for k in r)}) VALUES({",".join("?" * len(r))})', list(r.values()))
                n += 1
            total += n
            if H.PG and "id" in cols_now:
                c.execute(f"SELECT setval(pg_get_serial_sequence('{tb}','id'), COALESCE((SELECT MAX(id) FROM \"{tb}\"),0)+1, false)")
            print(f"  {tb}: {n}")
        c.commit()
        c.close()
    print(f"Восстановлено {total} записей из копии от {man['created_at']}")


def register(hq_module):
    global H
    H = hq_module
    c = H.con()
    c.execute(STORE_SQL.replace("BLOB", "BYTEA") if H.PG else STORE_SQL)
    c.commit()
    c.close()
    H.app.register_blueprint(bp)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "restore":
        os.environ["INTEGRATIONS_WORKER"] = "0"
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import hq as _hq    # noqa: E402  — инициализирует базу и модули
        _hq.backup.restore(sys.argv[2], "--force" in sys.argv)
    else:
        print(__doc__)
