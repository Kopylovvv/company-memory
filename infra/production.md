# Production на виртуальной машине

Текущий кандидат — Linux-VM в Yandex Cloud, потому что у команды может быть грант.
Конфигурация не зависит от провайдера: нужен сервер с публичным IPv4, доменом,
Docker Engine и Compose v2. Публичный запуск разрешается только после #10 — до
этого API использует небезопасный `X-Demo-User-Id`.

## Схема

Интернет → Caddy (`80/443`, автоматический TLS) → FastAPI (`8000` только внутри
Compose) → PostgreSQL (только внутренняя сеть). Mini-app frontend не входит в
bot-only P0. Caddy и PostgreSQL используют именованные volumes.

## Подготовка VM

1. Создать Ubuntu VM, зарезервировать публичный IP и направить A-запись домена.
2. В firewall/security group открыть `80/tcp`, `443/tcp`, `443/udp`; SSH открыть
   только с адресов команды. PostgreSQL и порт `8000` наружу не открывать.
3. Установить Docker Engine с Compose v2 и добавить отдельного пользователя для
   развёртывания. Не запускать приложение из-под root без необходимости.
4. Клонировать приватный репозиторий через отдельный deploy key с read-only
   доступом или получить архив выбранного commit.

Конкретные команды создания VM зависят от выбранного аккаунта и сети Yandex Cloud,
поэтому их фиксируем после выдачи проекта, IP и домена.

## Первый запуск

```sh
cp infra/.env.production.example .env.production
chmod 600 .env.production
# Заполнить DOMAIN, POSTGRES_PASSWORD, MAX_BOT_TOKEN и ключ AI на сервере.
docker compose --env-file .env.production -f compose.prod.yaml config --quiet
docker compose --env-file .env.production -f compose.prod.yaml up -d --build
docker compose --env-file .env.production -f compose.prod.yaml ps
curl --fail --show-error "https://bot.example.ru/api/health"
```

Файл `.env.production` не копируется с ноутбука в Git и не попадает в логи.
Токен бота вводится непосредственно на сервере. Перед публичным запуском проверить,
что временный demo-заголовок удалён или недоступен извне.

## Обновление

1. Зафиксировать текущий commit: `git rev-parse HEAD`.
2. Сделать резервную копию БД по инструкции ниже.
3. Получить проверенный commit из `main`.
4. Выполнить `config --quiet`, затем `up -d --build` той же командой Compose.
5. Проверить `ps`, `/api/health`, логи и основной сценарий бота.

Миграции выполняются контейнером backend перед запуском API. Не запускайте две
несовместимые версии backend одновременно во время изменения схемы.

## Резервная копия и восстановление

```sh
mkdir -p backups
docker compose --env-file .env.production -f compose.prod.yaml exec -T db \
  sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > "backups/company-memory-$(date +%Y%m%d-%H%M).dump"
```

Для восстановления сначала остановить backend, сохранить отдельную копию текущей
БД и только затем выполнить `pg_restore`. Восстановление проверяется на копии БД
до дня сдачи; непроверенный backup не считается готовым откатом.

## Откат

1. Переключить Git на записанный предыдущий commit.
2. Сверить совместимость его миграций с текущей БД.
3. Если схема совместима — пересобрать и поднять предыдущую версию.
4. Если несовместима — остановить backend и восстановить соответствующий dump.
5. Проверить health endpoint и полный bot-only сценарий.

## Проверка готовности #11

- [ ] Выбраны проект Yandex Cloud, VM, публичный IP и домен.
- [ ] Firewall пропускает только SSH команды и публичные `80/443`.
- [ ] `compose.prod.yaml config --quiet` проходит с серверным env-файлом.
- [ ] HTTPS-сертификат получен, `/api/health` доступен по домену.
- [ ] PostgreSQL и backend-порт недоступны из интернета.
- [ ] Перезапуск VM сохраняет случаи и автоматически поднимает сервисы.
- [ ] Backup восстановлен в тестовую БД; обновление и откат пройдены вручную.
- [ ] После #10 реальное событие MAX проходит через публичный endpoint.
