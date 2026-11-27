# Центр сети магазинов

Облачный кабинет владельца: кассы магазинов синхронизируются сюда (каталог и накладные вниз; продажи, остатки и приёмки вверх).

Railway: переменные `DATABASE_URL` (${{Postgres.DATABASE_URL}}), `HQ_PASSWORD`, `SECRET_KEY`, `PORT=8080`, `APP_TZ=Asia/Tashkent`.
Локально: `pip install -r requirements.txt && python hq.py` (без DATABASE_URL работает на SQLite).

## Безопасность входа (аудит)
- На сервере (Railway) без переменной `HQ_PASSWORD` вход по общему паролю отключён (раньше действовал пароль `admin`). Локально по-прежнему `admin`.
- Сессии привязаны к отпечатку пароля: смена пароля пользователя или `HQ_PASSWORD` завершает все остальные входы.
- Тесты: `pip install pytest && pytest tests`.
