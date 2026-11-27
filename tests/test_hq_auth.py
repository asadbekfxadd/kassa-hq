"""Вход в Центр: общий пароль, сессии, смена пароля. Запуск: pytest tests"""
import importlib
import os
import sys

import pytest

HQ = os.path.join(os.path.dirname(__file__), "..", "hq")


@pytest.fixture()
def hq(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("FORCE_HTTPS", raising=False)
    monkeypatch.setenv("HQ_PASSWORD", "owner-pass-1")
    monkeypatch.syspath_prepend(HQ)
    for m in [m for m in sys.modules if m in ("hq", "rbac", "security", "stockhq", "transfers", "analytics", "people",
                                              "integrations", "assistant", "backup", "apidoc", "stores2", "hqdash",
                                              "salescache", "migrate")]:
        del sys.modules[m]
    mod = importlib.import_module("hq")
    mod.app.config["TESTING"] = True
    return mod


def login(c, pw, lg=""):
    return c.post("/api/login", json={"login": lg, "password": pw})


def test_shared_password_login_and_me(hq):
    c = hq.app.test_client()
    assert c.get("/api/me").status_code == 401
    assert login(c, "wrong").status_code == 401
    assert login(c, "owner-pass-1").status_code == 200
    assert c.get("/api/me").status_code == 200


def test_session_dies_when_shared_password_rotated(hq, monkeypatch):
    c = hq.app.test_client()
    assert login(c, "owner-pass-1").status_code == 200
    monkeypatch.setenv("HQ_PASSWORD", "owner-pass-2")
    assert c.get("/api/me").status_code == 401


def test_no_default_admin_password_on_server(hq, monkeypatch):
    monkeypatch.delenv("HQ_PASSWORD")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    c = hq.app.test_client()
    r = login(c, "admin")
    assert r.status_code == 503
    assert c.get("/api/me").status_code == 401


def test_local_default_still_works_without_env(hq, monkeypatch):
    monkeypatch.delenv("HQ_PASSWORD")
    c = hq.app.test_client()
    assert login(c, "admin").status_code == 200


def _make_user(hq, c):
    assert login(c, "owner-pass-1").status_code == 200
    r = c.post("/api/users", json={"login": "boss@example.com", "name": "Boss", "role": "owner", "password": "firstpass1"})
    assert r.status_code == 200, r.get_data(as_text=True)


def test_password_change_revokes_other_sessions(hq):
    owner = hq.app.test_client()
    _make_user(hq, owner)
    a, b = hq.app.test_client(), hq.app.test_client()
    assert login(a, "firstpass1", "boss@example.com").status_code == 200
    assert login(b, "firstpass1", "boss@example.com").status_code == 200
    assert a.get("/api/me").status_code == 200 and b.get("/api/me").status_code == 200
    r = a.post("/api/me/password", json={"old": "firstpass1", "new": "secondpass2"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert a.get("/api/me").status_code == 200   # текущая остаётся
    assert b.get("/api/me").status_code == 401   # чужая завершена
