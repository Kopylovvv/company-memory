# Database — Андрей

`engine.py` — SQLAlchemy engine и session-фабрика (подключение к БД ленивое:
импорт модуля не требует доступной БД). `models.py` — ORM-таблица `cases`,
плоско отражающая контракт `app.cases.models.Case` (Issue #2): исходное
сообщение, извлечённые поля, участник и статус подтверждения в одной строке.
`repository.py` — `DbCaseService`, тот же интерфейс, что и in-memory
`app.cases.service.CaseService` (см. `app.cases.protocol.CaseServiceProtocol`),
так что HTTP-слой (`backend/app/api/cases.py`) не знает, какое хранилище за
ним стоит.

Уникальность на `source_id` и частичный уникальный индекс на
`source_external_event_id` (когда он не `null`) защищают от дублей при
повторной доставке события MAX — на уровне БД, а не только в Python.

Миграции — Alembic, в `backend/migrations/`. Не создавайте и не меняйте
таблицы вручную на сервере; конфигурация подключения берётся из тех же
переменных `POSTGRES_*`, что и `compose.yaml` (см. `settings.py`), поэтому
секретов в `alembic.ini` нет.

```sh
# из backend/, БД должна быть поднята (docker compose up -d db) и переменные
# POSTGRES_* — в окружении или совпадать со значениями по умолчанию из .env.example
uv run alembic upgrade head
```

Новая ревизия миграции: `uv run alembic revision -m "описание"` (пишется
вручную — `--autogenerate` нужен живой БД с текущей схемой).
