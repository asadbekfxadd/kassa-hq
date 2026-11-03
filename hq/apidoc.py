"""
Центр (этап 9): документация API — строится из реальных маршрутов приложения и правил доступа (RBAC),
поэтому не расходится с кодом. Страница: /api-docs (после входа), JSON: /api/reference.
"""
import re

from flask import Blueprint, jsonify, render_template_string, request

bp = Blueprint("apidoc", __name__)
H = None

GROUPS = [("Вход и пользователи", ("/api/login", "/api/logout", "/api/me", "/api/users", "/api/roles", "/api/audit")),
          ("Синхронизация касс", ("/sync", "/update/")), ("Обзор и уведомления", ("/api/dash", "/api/alerts")),
          ("Магазины", ("/api/stores", "/api/update/info")), ("Каталог", ("/api/products",)), ("Остатки", ("/api/stock",)),
          ("Поставки и перемещения", ("/api/docs", "/api/transfers")), ("Аналитика", ("/api/an/", "/api/sales_report", "/report/")),
          ("Финансы", ("/api/fin/",)), ("Персонал", ("/api/staff",)), ("Клиенты и лояльность", ("/api/crm",)),
          ("AI-помощник", ("/api/ai/",)), ("Интеграции", ("/api/integrations", "/feed/")), ("Переезд данных", ("/api/migrate",)),
          ("Резервные копии", ("/api/backups",)), ("Служебные", ("/health", "/api/reference"))]

DESC = {
    "POST /api/login": "Вход. Тело: {login, password}; пустой login — общий пароль владельца (HQ_PASSWORD). 5 ошибок за 15 мин → пауза 15 мин (429).",
    "POST /api/logout": "Выход.", "GET /api/me": "Текущий пользователь, роль, права, магазины.", "POST /api/me/password": "Смена своего пароля: {old, new}.",
    "GET /api/users": "Пользователи Центра.", "POST /api/users": "Создать/изменить пользователя: {id?, name, login, password?, role, stores[]}.",
    "GET /api/roles": "Роли и права.", "GET /api/audit": "Журнал действий (?store, ?q).",
    "POST /sync": "Обмен кассы с Центром (заголовок X-Store-Token). Вверх: sales, money, stock, docs, new_products, users, customers, info. "
                  "Вниз: acks, products, docs, cancelled, employees, customers_net, pending, server_time. Идемпотентно по uid.",
    "POST /sync/ack": "Касса подтверждает применение данных переезда.", "GET /update/version": "Версия кассы для автообновления (X-Store-Token).",
    "GET /update/kassa.zip": "Архив обновления кассы (X-Store-Token).",
    "GET /api/dash2": "Обзор сети: ?period=day|week|month|year — выручка, прибыль, планы, топы, сравнения.", "GET /api/dashboard": "Краткий обзор (старый формат).",
    "GET /api/alerts": "Тревоги сети (нулевые остатки, расхождения, кассы без связи…).",
    "GET /api/stores": "Магазины с токенами и состоянием касс.", "POST /api/stores": "Создать/изменить магазин.",
    "POST /api/stores/<int:sid>/token": "Выпустить новый токен кассы.", "GET /api/stores/<int:sid>/card": "Карточка магазина: план, KPI, 12 месяцев.",
    "POST /api/stores/<int:sid>/plan": "План продаж по месяцам: {plans:[{month, amount}]}.", "GET /api/stores/compare": "Сравнение филиалов за период (?from&to).",
    "GET /api/update/info": "Версия кассы на сервере и версии в магазинах.",
    "GET /api/products": "Каталог (?q). Себестоимость скрыта без права «Прибыль».", "POST /api/products": "Создать/изменить товар.",
    "DELETE /api/products/<uid>": "Снять товар с продажи во всех магазинах.", "GET /api/products/<uid>/prices": "История цен товара.",
    "GET /api/products/barcode/<code>": "Товар по штрихкоду.", "GET /api/products/export.csv": "Каталог в CSV.", "GET /api/products/gen_barcode": "Свободный внутренний EAN-13.",
    "POST /api/products/import": "Импорт каталога из Excel/CSV (multipart file).",
    "GET /api/stock": "Матрица остатков товар × магазин.", "GET /api/stock/status": "Статусы остатков (нет, мало, риск, без продаж, избыток).",
    "GET /api/stock/status.xlsx": "Статусы остатков в Excel.",
    "GET /api/docs": "Поставки и перемещения (?type, ?status).", "POST /api/docs": "Создать поставку/перемещение.", "GET /api/docs/<uid>": "Документ со строками.",
    "DELETE /api/docs/<uid>": "Отменить документ до отгрузки.", "POST /api/docs/<uid>/action": "Переход статуса: submit|approve|reject|cancel|complete (+comment).",
    "POST /api/docs/<uid>/lines": "Изменить строки черновика.", "GET /api/docs/<uid>/events": "История статусов документа.", "POST /api/docs/parse": "Разбор накладной поставщика из Excel.",
    "GET /api/transfers/recommend": "Рекомендации перемещений (?window=7|14|30|60&target&keep).", "POST /api/transfers/create": "Создать перемещения из рекомендаций.",
    "GET /api/an/filters": "Списки для фильтров аналитики.", "GET /api/an/sales": "Аналитика продаж (?from&to&store&category&brand).",
    "GET /api/an/products": "ABC/XYZ, топы, без продаж, нулевые, риск, избыток (?top=10|20|30|50).", "GET /api/an/season": "Сезонность: 24 месяца, дни недели, часы.",
    "GET /api/an/export/<kind>.xlsx": "Excel: sales|products|pnl|cashflow|expenses.", "GET /report/<kind>": "Страница для печати / PDF.", "GET /api/sales_report": "Продажи по магазинам и топ товаров.",
    "GET /api/fin/pnl": "Прибыль и убытки по филиалам и сети.", "GET /api/fin/cashflow": "Денежный поток по дням.", "GET /api/fin/balances": "Долги клиентов, поставщикам, наличные в кассах.",
    "GET /api/fin/expenses": "Расходы (касс и Центра).", "POST /api/fin/expenses": "Внести расход: {category, amount, store_id?, date, note}.",
    "DELETE /api/fin/expenses/<uid>": "Удалить расход, внесённый в Центре.",
    "GET /api/staff": "Персонал с KPI и рейтингом за период.", "POST /api/staff": "Принять/изменить сотрудника (ПИН и роль на кассу).", "GET /api/staff/<uid>": "Карточка сотрудника.",
    "POST /api/staff/<uid>/plan": "Личный план: {plans:[{month, amount}]}.", "POST /api/staff/plan_split": "Разделить план магазина: {store_id, month}.",
    "POST /api/staff/payroll": "Начислить оклады месяца в расходы: {month}.", "GET /api/staff/export.xlsx": "Персонал и KPI в Excel.",
    "GET /api/crm/customers": "База клиентов: сводка, сегменты, список (?q&segment&level&flag&sort&store).", "GET /api/crm/customers/<int:cid>": "Профиль клиента.",
    "POST /api/crm/customers/<int:cid>": "Изменить контакты клиента.", "GET /api/crm/levels": "Уровни лояльности.", "POST /api/crm/levels": "Сохранить уровни и пересчитать клиентов.",
    "GET /api/crm/export.xlsx": "Клиенты в Excel.",
    "GET /api/ai/info": "Подсказки и подключён ли Claude.", "POST /api/ai/ask": "Вопрос помощнику: {q, llm?} → title, text, table, tips, view, source.",
    "GET /api/integrations": "Интеграции и их состояние.", "POST /api/integrations/<key>": "Настройки интеграции: {settings, enabled}.",
    "POST /api/integrations/<key>/test": "Проверить подключение.", "GET /api/integrations/jobs": "Журнал обмена (?key&status).",
    "POST /api/integrations/jobs/<int:jid>/retry": "Повторить отправку.", "POST /api/integrations/telegram/find_chat": "Найти chat_id по сообщениям бота.",
    "POST /api/integrations/telegram/report_now": "Отправить отчёт дня сейчас.", "GET /api/integrations/1c/commerceml.zip": "Выгрузка CommerceML 2 для 1С.",
    "GET /api/integrations/tasnif/check": "Товары без ИКПУ и с неверным форматом.", "GET /api/integrations/uzum/preview": "Предпросмотр витрины Uzum (?store).",
    "GET /feed/<token>/<name>.csv": "CSV-ленты для Google Sheets (секретный ключ в адресе): sales_daily|stock|products|plans.",
    "GET /api/migrate": "История загрузок переезда.", "GET /api/migrate/kinds": "Что можно загрузить.", "POST /api/migrate/upload": "Загрузить файл (multipart).",
    "POST /api/migrate/<int:iid>/check": "Проверить сопоставление колонок.", "POST /api/migrate/<int:iid>/apply": "Применить загрузку.",
    "GET /api/backups": "Резервные копии.", "POST /api/backups": "Создать копию сейчас.", "GET /api/backups/<name>": "Скачать копию.",
    "GET /health": "Проверка работы сервера.", "GET /api/reference": "Эта документация в JSON.",
}


def perm_for(method, path):
    sample = re.sub(r"<int:[^>]+>", "1", path)
    sample = re.sub(r"<[^>]+>", "x", sample)
    if not sample.startswith("/api/") or sample in H.rbac.OPEN:
        return None if sample in H.rbac.OPEN or not sample.startswith("/api/") else "login"
    for meth, rx, perm in H.rbac.RULES:
        if (meth == "*" or meth == method) and re.search(rx, sample):
            return perm
    return "login"


def build():
    out = {g: [] for g, _ in GROUPS}
    for r in H.app.url_map.iter_rules():
        if r.rule.startswith("/<any") or r.endpoint == "static" or r.rule in ("/", "/api-docs"):
            continue
        for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
            key = f"{m} {r.rule}"
            grp = next((g for g, prefs in GROUPS if any(r.rule.startswith(p) for p in prefs)), "Служебные")
            pm = perm_for(m, r.rule)
            auth = ("Токен магазина (X-Store-Token)" if r.rule.startswith(("/sync", "/update/")) else
                    "Секретная ссылка" if r.rule.startswith("/feed/") else "Сессия + вход" if r.rule.startswith("/report/") else
                    "Открыто" if pm is None else "Вход" if pm == "login" else f"Право «{H.rbac.PERMS.get(pm, pm)}»")
            out[grp].append({"method": m, "path": r.rule, "auth": auth, "perm": pm, "desc": DESC.get(key, (H.app.view_functions[r.endpoint].__doc__ or "").strip().split("\n")[0])})
    for g in out:
        out[g].sort(key=lambda x: (x["path"], x["method"]))
    return {g: v for g, v in out.items() if v}


PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>API Центра сети</title>
<style>body{font:14px/1.5 system-ui,Segoe UI,sans-serif;margin:0;background:#f6f7f9;color:#0b0f19}main{max-width:1100px;margin:0 auto;padding:24px 16px}
h1{margin:0 0 4px}h2{margin:28px 0 8px;font-size:17px}.m{color:#6b7280}table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden}
td{padding:8px 10px;border-top:1px solid #eef0f3;vertical-align:top}code{font:12.5px ui-monospace,Consolas,monospace}.b{display:inline-block;min-width:54px;text-align:center;border-radius:6px;padding:1px 6px;font:600 11.5px ui-monospace,monospace;color:#fff}
.GET{background:#1d4ed8}.POST{background:#067647}.DELETE{background:#b42318}.a{font-size:12px;color:#475467;white-space:nowrap}pre{background:#0b0f19;color:#e5e7eb;padding:12px;border-radius:8px;overflow:auto;font-size:12.5px}</style></head>
<body><main><h1>API Центра сети</h1><div class="m">Все ответы — JSON (UTF-8), даты — «ГГГГ-ММ-ДД ЧЧ:ММ:СС» по Ташкенту, суммы — в сумах. Ошибки: <code>{"error": "текст"}</code> с кодом 400/401/403/404/429.
Доступ — по сессии после <code>POST /api/login</code> и правам роли; кассы — по заголовку <code>X-Store-Token</code>. Список строится из кода и всегда актуален.</div>
<h2>Пример</h2><pre>curl -c c.txt -H "Content-Type: application/json" -d '{"login":"owner","password":"…"}' {{host}}/api/login
curl -b c.txt "{{host}}/api/an/sales?from=2026-11-01&to=2026-11-30&store=1"</pre>
{% for g, items in groups.items() %}<h2>{{g}}</h2><table>{% for x in items %}<tr><td style="width:70px"><span class="b {{x.method}}">{{x.method}}</span></td><td><code>{{x.path}}</code><div class="m" style="font-size:13px">{{x.desc}}</div></td><td class="a">{{x.auth}}</td></tr>{% endfor %}</table>{% endfor %}
<p class="m" style="margin-top:24px">Протокол синхронизации касс подробно — в docs/API.md репозитория.</p></main></body></html>"""


@bp.get("/api/reference")
def reference():
    return jsonify(groups=build())


@bp.get("/api-docs")
def page():
    if not H.rbac.load_user():
        return "Требуется вход в Центр", 401
    return render_template_string(PAGE, groups=build(), host=request.host_url.rstrip("/"))


def register(hq_module):
    global H
    H = hq_module
    H.app.register_blueprint(bp)
