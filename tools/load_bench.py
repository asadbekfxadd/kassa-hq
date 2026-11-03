"""Замер времени ответа ключевых экранов Центра на нагрузочных данных (по 3 замера, берём медиану)."""
import requests, time, statistics, json, sys, uuid
H = 'http://127.0.0.1:8091'
s = requests.Session(); s.post(H + '/api/login', json={'password': 'boss'}).raise_for_status()
M = '2026-11-01'; T = '2026-11-03'; M3 = '2026-08-05'
EP = [('Обзор сети: день', 'GET', '/api/dash2?period=day'), ('Обзор сети: месяц', 'GET', '/api/dash2?period=month'), ('Тревоги', 'GET', '/api/alerts'),
      ('Магазины', 'GET', '/api/stores'), ('Сравнение филиалов', 'GET', f'/api/stores/compare?from={M}&to={T}'),
      ('Каталог (поиск)', 'GET', '/api/products?q=Товар 12'), ('Остатки: статусы', 'GET', '/api/stock/status?store=1'),
      ('Рекомендации перемещений', 'GET', '/api/transfers/recommend'),
      ('Аналитика продаж: месяц', 'GET', f'/api/an/sales?from={M}&to={T}'), ('Аналитика продаж: 90 дней', 'GET', f'/api/an/sales?from={M3}&to={T}'),
      ('ABC/XYZ: 90 дней', 'GET', f'/api/an/products?from={M3}&to={T}'), ('Сезонность', 'GET', f'/api/an/season?from={M}&to={T}'),
      ('Прибыль и убытки', 'GET', f'/api/fin/pnl?from={M}&to={T}'), ('Денежный поток', 'GET', f'/api/fin/cashflow?from={M}&to={T}'),
      ('Персонал + KPI', 'GET', f'/api/staff?from={M}&to={T}'), ('Клиенты (50 000)', 'GET', '/api/crm/customers'),
      ('AI: сводка', 'POST', '/api/ai/ask'), ('Excel: ABC', 'GET', f'/api/an/export/products.xlsx?from={M}&to={T}')]
out = []
for name, m, url in EP:
    ts = []
    for _ in range(int(__import__("os").environ.get("REP", "3"))):
        t0 = time.time()
        r = s.request(m, H + url, json={'q': 'Сводка по сети'} if m == 'POST' else None, timeout=300)
        ts.append(time.time() - t0)
        assert r.status_code == 200, (name, r.status_code, r.text[:200])
    out.append((name, statistics.median(ts), len(r.content)))
    print(f'{name:32s} {statistics.median(ts) * 1000:7.0f} мс  ({len(r.content) // 1024} КБ)', flush=True)
# синхронизация: касса присылает 300 чеков по 3 позиции
tok = s.get(H + '/api/stores').json()[0]['token']
sales = [{'uid': uuid.uuid4().hex, 'id': i, 'created_at': '2026-11-03 12:00:00', 'operation': 'sale', 'total': 30000, 'cash': 30000, 'card': 0, 'debt': 0,
          'discount': 0, 'cashier': 'Кассир', 'fiscal_status': 'sent', 'items': [{'product_uid': '', 'name': 'x', 'qty': 1, 'price': 10000, 'total': 10000, 'cost_price': 8000}] * 3}
         for i in range(300)]
t0 = time.time(); r = requests.post(H + '/sync', json={'since': '2026-11-03 00:00:00', 'cust_since': '2026-11-03 00:00:00', 'sales': sales, 'stock': [], 'info': {}},
                                    headers={'X-Store-Token': tok}, timeout=300)
assert r.status_code == 200 and len(r.json()['acks']['sales']) == 300
print(f"{'Синхронизация: 300 чеков':32s} {(time.time() - t0) * 1000:7.0f} мс")
t0 = time.time(); requests.post(H + '/sync', json={'since': '2026-11-03 00:00:00', 'cust_since': '2026-11-03 00:00:00', 'sales': [], 'info': {}}, headers={'X-Store-Token': tok}).raise_for_status()
print(f"{'Синхронизация: пустой обмен':32s} {(time.time() - t0) * 1000:7.0f} мс")
