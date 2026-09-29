# Backend и бот MAX

FastAPI, Python 3.13, PostgreSQL. Вход API: `app/main.py`; бот:
`python -m app.bot.polling`. Команды запуска и проверки —
[development.md](../docs/development.md), основной пользовательский сценарий —
[корневой README](../README.md).

- `app/cases/` — модели и общий контракт, in-memory сервис для тестов.
- `app/api/` — HTTP-маршруты и зависимости.
- `app/db/` — PostgreSQL, сессии и сервис хранения.
- `migrations/` — Alembic-миграции, применяются при запуске контейнера API.
- `app/bot/` — long polling MAX, диалог, команды и отмена ожидания.
- `app/ai/` — YandexGPT, проверка извлечения, локальный поиск и оценка.
- `tests/` — поведение API, бота, AI и PostgreSQL.

Бот вызывает сервис хранения напрямую с автором из события MAX. HTTP API случаев
по умолчанию возвращает `401`, поскольку не принимает произвольный user_id как
доказательство личности. `ALLOW_DEMO_IDENTITY=true` разрешает локальный
`X-Demo-User-Id` только для разработки; production его не включает.

Рабочая схема хранится в PostgreSQL. Контракт и требования подтверждения —
[contracts/README.md](../contracts/README.md). История оборудования реализована
во внутреннем API, но не отдельной командой бота. Рабочие ключи в Git не входят.
