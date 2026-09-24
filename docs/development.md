# Разработка

## Весь проект через Docker

Из корня: `cp .env.example .env`, затем `docker compose up --build`.
После изменения исходников пересоберите: `docker compose up --build`.
Это простой запуск без hot reload. `.env` не коммитим.

## Backend отдельно

Нужны Python 3.13 и uv. Из папки `backend`:

```sh
uv sync --frozen
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Проверки из той же папки:

```sh
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

При намеренном изменении зависимостей обновляйте `pyproject.toml` и `uv.lock`
в одном PR. Зафиксированный lock применяется в CI и Docker.

## Доступ к API случаев локально

Маршруты `/api/cases*` требуют личность, которую проверил сервер. В bot-only MVP
такая личность приходит только из событий MAX, поэтому HTTP API по умолчанию
отвечает `401`. Для локальных проверок включите заголовок разработчика:

```sh
export ALLOW_DEMO_IDENTITY=true
curl -H "X-Demo-User-Id: demo-user-001" http://127.0.0.1:8000/api/cases
```

Переменная предназначена только для разработки: заголовок принимается как есть.
В продакшене она не выставляется, поэтому публичный запуск закрыт по умолчанию.

## Бот MAX отдельно

Нужны поднятая БД с применёнными миграциями, токен бота и CA-бандл с
Russian Trusted Root CA (сертификат API MAX выпущен Минцифры, стандартные
хранилища его не знают). Подробности и команды сборки бандла —
`backend/app/bot/README.md`. Из папки `backend`:

```sh
export MAX_BOT_TOKEN=...            # только окружение, не в коде и не в Git
export MAX_API_CA_BUNDLE=/path/to/max-ca-bundle.pem
uv run python -m app.bot.polling --once
```

`--once` обрабатывает один цикл long polling и выходит; без флага бот работает
постоянно. Токен в репозиторий не коммитим: локально он живёт в `.env`.

## Frontend отдельно

Нужны Node.js 22.12+ и npm. Из папки `frontend`:

```sh
npm ci
npm run dev
```

Backend должен работать на `127.0.0.1:8000`. Vite перенаправляет `/api` к нему.
Для другого backend задайте `API_PROXY_TARGET` перед запуском Vite.

```sh
npm run build
```

Сборка проверяет TypeScript. `npm run preview` показывает только сборку: proxy
для API настроен для dev-сервера. Production reverse proxy — отдельная задача.

## PostgreSQL

В Docker backend обращается к хосту `db`; контейнер сам применяет миграции
(`alembic upgrade head`) перед запуском `uvicorn`. Для запуска backend вне
Docker: `docker compose up -d db`, затем из `backend/` — `uv run alembic
upgrade head`. Настройки подключения — переменные `POSTGRES_*` (см.
`.env.example`); без них используются значения по умолчанию оттуда же.

Для просмотра БД: `docker compose exec db psql -U company_memory -d company_memory`
(если изменили пользователя или БД, подставьте свои значения).

Новая миграция: `uv run alembic revision -m "описание"` из `backend/`,
затем заполнить `upgrade()`/`downgrade()` вручную (autogenerate требует живой
БД с текущей схемой — см. `backend/app/db/README.md`).

## Что проверяет CI

Ruff, pytest (с реальным Postgres-сервисом и применёнными миграциями —
`backend/tests/test_db_case_repository.py` не пропускается в CI), сборку
frontend и запуск Compose с проверкой маршрута API через frontend proxy. Это
проверки каркаса; продуктовые сценарии добавляются вместе с реализацией, а не
считаются проверенными заранее.
