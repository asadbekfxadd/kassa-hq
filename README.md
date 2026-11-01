# Центр сети магазинов

Облачный кабинет владельца: кассы магазинов синхронизируются сюда (каталог и накладные вниз; продажи, остатки и приёмки вверх).

Railway: переменные `DATABASE_URL` (${{Postgres.DATABASE_URL}}), `HQ_PASSWORD`, `SECRET_KEY`, `PORT=8080`, `APP_TZ=Asia/Tashkent`.
Локально: `pip install -r requirements.txt && python hq.py` (без DATABASE_URL работает на SQLite).
