"""
Центр (этап 9): безопасность.
  • защита от подбора пароля: 5 неудачных попыток за 15 минут с одного адреса или на один логин → пауза 15 минут;
  • заголовки безопасности (запрет встраивания во фрейм, nosniff, referrer, HSTS на HTTPS);
  • cookie сессии только по HTTPS на сервере, HttpOnly, SameSite=Lax;
  • ограничение размера запроса; сравнение паролей и токенов за постоянное время.
"""
import hmac
import os
import threading
import time

from flask import jsonify, request

H = None
WINDOW, LIMIT, LOCK_FOR = 15 * 60, 5, 15 * 60
_FAILS = {}
_LOCK = threading.Lock()


def client_ip():
    # адрес клиента: waitress за прокси Railway подставляет его из X-Forwarded-For (см. запуск в hq.py), подделать нельзя
    return (request.remote_addr or "")[:60]


def _keys(login):
    return [f"ip:{client_ip()}"] + ([f"login:{login}"] if login else [])


def _limit(key):
    # на один логин — 5 ошибок; с одного адреса — 20 (за одним адресом может быть весь офис или магазин)
    return LIMIT if key.startswith("login:") else LIMIT * 4


def locked(login):
    now = time.time()
    with _LOCK:
        for k in _keys(login):
            v = _FAILS.get(k)
            if v and v.get("until", 0) > now:
                return int((v["until"] - now) // 60) + 1
    return 0


def fail(login):
    now = time.time()
    with _LOCK:
        for k in _keys(login):
            v = _FAILS.setdefault(k, {"t": []})
            v["t"] = [x for x in v["t"] if now - x < WINDOW] + [now]
            if len(v["t"]) >= _limit(k):
                v["until"] = now + LOCK_FOR
                v["t"] = []
        if len(_FAILS) > 5000:          # не даём словарю расти бесконечно
            for k in [k for k, v in _FAILS.items() if v.get("until", 0) < now and not v["t"]][:2000]:
                _FAILS.pop(k, None)


def ok(login):
    with _LOCK:
        for k in _keys(login):
            _FAILS.pop(k, None)


def same(a, b):
    return hmac.compare_digest(str(a or "").encode(), str(b or "").encode())


def register(hq_module):
    global H
    H = hq_module
    app = H.app
    on_server = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("FORCE_HTTPS"))
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SECURE=on_server, MAX_CONTENT_LENGTH=60 * 1024 * 1024)

    @app.before_request
    def _throttle():
        if request.path == "/api/login" and request.method == "POST":
            d = request.get_json(silent=True) or {}
            m = locked((d.get("login") or "").strip().lower() or "@owner")
            if m:
                return jsonify(error=f"Слишком много неверных попыток входа. Подождите {m} мин."), 429

    @app.after_request
    def _headers(resp):
        if request.path == "/api/login" and request.method == "POST":
            d = request.get_json(silent=True) or {}
            lg = (d.get("login") or "").strip().lower() or "@owner"
            if resp.status_code == 401:
                fail(lg)
                try:
                    H.rbac.audit("Неудачная попытка входа", f"логин «{lg if lg != '@owner' else 'общий пароль'}», адрес {client_ip()}")
                except Exception:
                    pass
            elif resp.status_code == 200:
                ok(lg)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        resp.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        if request.headers.get("X-Forwarded-Proto") == "https" or request.is_secure:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        if request.path.startswith("/api/") and "Cache-Control" not in resp.headers:
            resp.headers["Cache-Control"] = "no-store"
        return resp
