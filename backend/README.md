# Backend — Андрей / AI — Макс

FastAPI, Python 3.13. Входная точка: `app/main.py`.
Команды запуска и проверок: [development.md](../docs/development.md).

- `app/cases/` — контракт случая (Pydantic) и in-memory сервис для тестов (Андрей).
- `app/api/` — HTTP-маршруты, error handlers и DI (Андрей).
- `app/db/` — модели, сессии и репозиторий PostgreSQL (Андрей).
- `migrations/` — Alembic-миграции (Андрей).
- `app/bot/` — адаптер MAX и обработка событий (Андрей).
- `app/ai/` — извлечение полей и поиск (Макс).
- `tests/` — проверки поведения API и модулей.

Реализованы `GET /api/health` и API случаев (`/api/cases`,
`/api/equipment/{id}/history`) с хранением в PostgreSQL — см.
`contracts/README.md`. Формат ещё не подтверждён Егором и Максом. Реальной
авторизации ещё нет: подтверждение случая использует временный заголовок
(`app/api/deps.py`). Не размещайте будущие маршруты с корпоративными данными
публично без задачи авторизации.
