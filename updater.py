"""
Автообновление кассы через Центр.

Центр (Railway) собирается из GitHub при каждом push и раздаёт актуальную версию кассы.
Касса раз в 30 минут спрашивает версию, скачивает обновление в data/update/ и применяет его:
  * при запуске программы,
  * при закрытии смены,
  * по кнопке «Обновить сейчас» (администратор).
Папка data/ (база, настройки, копии) никогда не перезаписывается.
После применения программа завершается с кодом 3 — start_kassa.bat ставит зависимости и запускает её снова.
"""
import hashlib
import io
import os
import shutil
import threading
import time
import zipfile

import requests
from flask import Blueprint, jsonify

import db

bp = Blueprint("updater", __name__)
M = None
BASE = db.BASE_DIR
STAGE = os.path.join(db.DATA_DIR, "update")
VERSION_FILE = os.path.join(BASE, "VERSION")
RESTART_CODE = 3
STATE = {"available": None, "staged": None, "error": "", "checked_at": None}
PROTECTED = ("data", ".git", "__pycache__")


def local_version():
    try:
        return open(VERSION_FILE, encoding="utf-8").read().strip()
    except OSError:
        return "dev"


def _hq():
    s = db.get_settings()
    url, tok = (s.get("hq_url") or "").strip().rstrip("/"), (s.get("hq_token") or "").strip()
    return (url, tok) if url and tok else (None, None)


def check():
    """Узнать версию в Центре и, если новее, скачать и подготовить."""
    url, tok = _hq()
    if not url:
        STATE.update(error="", available=None)
        return STATE
    try:
        r = requests.get(url + "/update/version", headers={"X-Store-Token": tok}, timeout=20)
        r.raise_for_status()
        info = r.json()
        STATE["checked_at"] = db.now()
        ver = info.get("version")
        if not ver or ver == local_version():
            STATE.update(available=None, error="")
            return STATE
        STATE["available"] = ver
        if STATE.get("staged") == ver:
            return STATE
        z = requests.get(url + "/update/kassa.zip", headers={"X-Store-Token": tok}, timeout=120)
        z.raise_for_status()
        if hashlib.sha256(z.content).hexdigest() != info.get("sha256"):
            raise RuntimeError("архив обновления повреждён")
        shutil.rmtree(STAGE, ignore_errors=True)
        os.makedirs(STAGE, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
            for n in zf.namelist():
                if n.startswith("/") or ".." in n.split("/"):
                    raise RuntimeError("недопустимый путь в архиве")
            zf.extractall(STAGE)
        with open(os.path.join(STAGE, "VERSION"), "w", encoding="utf-8") as f:
            f.write(ver)
        STATE.update(staged=ver, error="", notes=info.get("notes", ""))
    except Exception as e:
        STATE["error"] = str(e)[:200]
    return STATE


def staged_version():
    try:
        return open(os.path.join(STAGE, "VERSION"), encoding="utf-8").read().strip()
    except OSError:
        return None


def apply_staged():
    """Копирует подготовленные файлы поверх программы (кроме data/). True — если что-то применено."""
    ver = staged_version()
    if not ver or ver == local_version():
        return False
    for root, dirs, files in os.walk(STAGE):
        rel = os.path.relpath(root, STAGE)
        if rel.split(os.sep)[0] in PROTECTED:
            continue
        dirs[:] = [d for d in dirs if d not in PROTECTED]
        dst_dir = os.path.join(BASE, rel) if rel != "." else BASE
        os.makedirs(dst_dir, exist_ok=True)
        for fn in files:
            src = os.path.join(root, fn)
            dst = os.path.join(dst_dir, fn)
            tmp = dst + ".new"
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
    open(os.path.join(db.DATA_DIR, "update_applied.flag"), "w").write(ver)   # bat поставит зависимости
    shutil.rmtree(STAGE, ignore_errors=True)
    return True


def restart_later(delay=1.5):
    def go():
        time.sleep(delay)
        os._exit(RESTART_CODE)
    threading.Thread(target=go, daemon=True).start()


def worker():
    time.sleep(20)
    while True:
        try:
            check()
        except Exception:
            pass
        time.sleep(30 * 60)


@bp.get("/api/update/status")
def status():
    if not M.current_user():
        return M.err("Требуется вход", 401)
    return jsonify(version=local_version(), available=STATE.get("available"), staged=STATE.get("staged") or staged_version(),
                   error=STATE.get("error"), checked_at=STATE.get("checked_at"), notes=STATE.get("notes", ""))


@bp.post("/api/update/check")
def check_now():
    if not M.current_user():
        return M.err("Требуется вход", 401)
    check()
    return status()


@bp.post("/api/update/apply")
def apply_now():
    u = M.current_user()
    if not u or u["role"] != "admin":
        return M.err("Обновляет администратор", 403)
    if not staged_version():
        check()
    if not apply_staged():
        return M.err("Нет загруженного обновления")
    M.extras.audit("Касса обновлена", staged_version() or "", 1)
    restart_later()
    return jsonify(ok=True, restarting=True)


def on_shift_closed():
    """После закрытия смены касса свободна — ставим обновление, если оно скачано."""
    if staged_version() and staged_version() != local_version():
        if apply_staged():
            restart_later(3)


def startup():
    """Вызывается при старте: применяет скачанное обновление до запуска сервера."""
    if apply_staged():
        print("  Установлено обновление, перезапуск...")
        os._exit(RESTART_CODE)


def register(app_module):
    global M
    M = app_module
    M.app.register_blueprint(bp)
