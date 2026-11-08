# Миграция данных с master на rewrite

Скрипт: [`scripts/migrate_from_master.py`](../scripts/migrate_from_master.py).
Старая БД **не изменяется** — скрипт только читает из неё. Повторный запуск
безопасен: уже перенесённые пользователи (по telegram_id) пропускаются.

## Что переносится

| master | rewrite |
|---|---|
| `users` (user_id, username) | `users` (telegram_id, username) |
| `bars_login/bars_password`, `osep_login/osep_password` (открытым текстом) | `bars_credentials`/`osep_credentials` — пароли **шифруются** при переносе |
| `used_bars`/`used_osep` (кто подключён) | `bars_states`/`osep_states` со статусом **ACTIVE** и `next_check=now` — отслеживание продолжит работать сразу после запуска |
| `global_config` | `global_settings` |
| `user_config` (poll_interval, auto_scale, bars_show_marks, osep_blacklist) | `user_overrides`, `bars_settings`, `osep_settings` |

Чего **нет** в master и переносить нечего: сессии/куки БАРС и ОСЭП (новая
версия авторизуется сама при первой проверке), счётчики папок ОСЭП (при
первом запуске база папок запоминается без уведомлений — «старые» письма
не придут), профили ФИО/группа (загрузятся при первой проверке).

## Важно до старта

1. **SECRET_KEY в `.env` должен быть финальным** — пароли шифруются ключом,
   выведенным из него. Смена ключа после миграции = пароли не расшифровать,
   придётся переносить заново (или переспрашивать пароли у пользователей).
2. **Один токен Telegram = один работающий бот.** Старый и новый бот одновременно
   с одним токеном работать не должны (дерутся за `getUpdates`). Во время
   миграции старый бот может работать — но перед запуском новой версии
   остановите старый.

## Где разворачивать: два сервера или один

### Вариант 1: два сервера (проще всего)

Старый стенд (master) продолжает работать на сервере A как есть. На сервере B —
свежая копия rewrite целиком (свой postgres и свой redis):

```bash
# сервер B
git clone -b rewrite <репозиторий> ~/BarsWatcher-v2
cd ~/BarsWatcher-v2
cp .env.example .env   # заполнить; SECRET_KEY — финальный (см. «Важно до старта»)
docker compose up -d postgres redis
docker compose run --rm migrate        # alembic upgrade head
```

Миграция — с сервера B к серверу A по LAN (старый postgres слушает 5432):

```bash
# на сервере B, из папки ~/BarsWatcher-v2
docker compose run --rm \
  bot python scripts/migrate_from_master.py \
    --old-db-url 'postgresql+asyncpg://adminn:123123@<ip-сервера-A>:5432/BarsWatcher' \
    --new-db-url 'postgresql+asyncpg://barswatcher:barswatcher@postgres:5432/barswatcher'
```

Проверили (шаг 4) → остановили бота на сервере A → `docker compose up -d --build`
на сервере B. Откат — вернуть бота на A.

### Вариант 2: один сервер, две копии проекта

**Правило: rewrite кладётся в ОТДЕЛЬНУЮ папку.** Имя папки — это имя
docker-compose-проекта; у разных папок — разные контейнеры и разные тома
(`barswatcher_postgres_data` старого проекта и `barswatcher-v2_pgdata` нового
не пересекаются). Если же сделать `git checkout rewrite` прямо в старой папке и
выполнить `docker compose up`, compose увидит ТОТ ЖЕ проект `barswatcher` и
пересоздаст сервис `postgres` под новую схему — старый стенд будет разобран.
Так делать не нужно.

```bash
cd ~
git clone -b rewrite <репозиторий> BarsWatcher-v2   # вторая копия рядом
cd BarsWatcher-v2
cp .env.example .env   # заполнить; SECRET_KEY — финальный
```

В новом `docker-compose.yml` смените публикуемые порты (старый стенд уже
занял 5432 и 6379 на хосте):

```yaml
  postgres:
    ports:
      - "5433:5432"   # было 5432:5432
  redis:
    ports:
      - "6380:6379"   # было 6379:6379
```

Дальше — шаги 2–5 ниже; обе БД видны с хоста (`localhost:5432` — старая,
`localhost:5433` — новая), поэтому миграционный скрипт можно запустить прямо
на хосте (нужен `uv`) либо контейнером (без установки чего-либо).

Для запуска из контейнера добавьте в сервис `bot` нового compose-файла
маппинг хоста. Флаг `--add-host` у `docker compose run` поддерживают не все
версии compose, а `extra_hosts` работает везде:

```yaml
  bot:
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

```bash
# из папки BarsWatcher-v2; старая БД видна с контейнера через host-gateway
docker compose run --rm bot python scripts/migrate_from_master.py \
    --old-db-url 'postgresql+asyncpg://adminn:123123@host.docker.internal:5432/BarsWatcher' \
    --new-db-url 'postgresql+asyncpg://barswatcher:barswatcher@postgres:5432/barswatcher'
```

Альтернатива без `extra_hosts` — подставить IP шлюза Docker-сети напрямую
(обе БД через опубликованные порты хоста 5432/5433):

```bash
GW=$(docker network inspect barswatcher-v2_default \
       -f '{{(index .IPAM.Config 0).Gateway}}')
docker compose run --rm bot python scripts/migrate_from_master.py \
    --old-db-url "postgresql+asyncpg://adminn:123123@$GW:5432/BarsWatcher" \
    --new-db-url "postgresql+asyncpg://barswatcher:barswatcher@$GW:5433/barswatcher"
```

Либо с хоста, без Docker-сетей вовсе (нужен `uv`):

```bash
uv run python scripts/migrate_from_master.py \
    --old-db-url 'postgresql+asyncpg://adminn:123123@localhost:5432/BarsWatcher' \
    --new-db-url 'postgresql+asyncpg://barswatcher:barswatcher@localhost:5433/barswatcher'
```

Держите оба стенда работающими до проверки; перед запуском нового бота
остановите старый (один токен = один работающий бот):

```bash
cd ~/BarsWatcher     && docker compose stop bot   # старый бот остановлен,
                                                   # postgres/redis продолжают работать
cd ~/BarsWatcher-v2  && docker compose up -d --build
```

После проверки старый стенд можно остановить целиком (`docker compose down` —
тома остаются) или удалить. **Никогда не делайте `docker compose down -v`** в
старой папке: `-v` удаляет тома вместе с данными.

## Шаг 1. Новая БД рядом со старой (старую не заменяем)

Любой из вариантов; старая БД продолжает жить в своём объёме/базе.

**Вариант А — новая база в том же Postgres:**

```bash
docker exec -it <контейнер-postgres> psql -U <DB_USER> -c "CREATE DATABASE barswatcher_v2;"
```

**Вариант Б — второй Postgres на другом порту** (если хотите полную
изоляцию, например `docker run -d --name pg-v2 -e POSTGRES_USER=barswatcher
-e POSTGRES_PASSWORD=barswatcher -e POSTGRES_DB=barswatcher_v2 -p 5433:5432
-v pgdata_v2:/var/lib/postgresql/data postgres:16-alpine`).

**Вариант В — тестовый стенд docker-compose:** временно поменяйте в `.env`
`DB_NAME` на новую базу (вариант А) — том `pgdata` не трогается, старая база
остаётся внутри того же Postgres.

## Шаг 2. Схема новой БД

Направьте `DB_*` в `.env` на **новую** базу (старая не задействована) и
примените миграции:

```bash
# .env: DB_NAME=barswatcher_v2 (и/или DB_PORT=5433 для варианта Б)
uv run alembic upgrade head        # запуск с хоста
# либо, для docker-стенда (Вариант 2 выше):
docker compose run --rm migrate
```

## Шаг 3. Перенос данных

```bash
uv run python scripts/migrate_from_master.py \
    --old-db-url postgresql+asyncpg://<user>:<pass>@<host>:5432/<старая_база> \
    --new-db-url postgresql+asyncpg://<user>:<pass>@<host>:5432/barswatcher_v2
```

Для docker-стенда — та же команда через `docker compose run --rm bot python
scripts/migrate_from_master.py ...` (примеры в разделе «Где разворачивать»).
Скрипт запускайте из папки новой версии: он берёт `SECRET_KEY` из её `.env` —
того же, которым будет шифровать пароли новая БД.

Скрипт выведет: сколько пользователей перенесено/пропущено и сколько
подключено к отслеживанию (БАРС/ОСЭП) — сверьте с ожиданиями.

## Шаг 4. Проверка до переключения

- в новой БД: `SELECT count(*) FROM users;` — совпадает со старой;
- у пары пользователей: логины на месте, пароли зашифрованы
  (`bars_credentials.password` выглядит как `gAAAAA...`);
- статусы `bars_states`/`osep_states` у подключённых = `active`.

## Шаг 5. Переключение

```bash
# остановить старый бот/worker (master)
# запустить новую версию — .env уже указывает на новую БД (DB_NAME=barswatcher_v2)
uv run python bot.py
uv run python worker.py
# либо, для docker-стенда:
docker compose up -d --build
```

Что произойдёт само:

- подключённые пользователи подхватятся планировщиком в первый же тик
  (`next_check=now`);
- авторизация выполнится автоматически по сохранённым логинам/паролям
  (если сервис требует 2FA — придёт уведомление с вводом кода, вотчер
  включится после ввода);
- профили БАРС (ФИО/группа/ответственный) обновятся при первой удачной
  авторизации;
- первая проверка ОСЭП сохранит базу папок без уведомлений — «старые»
  письма не придут, уведомления пойдут только о новых.

## Откат

Остановите новую версию, верните в `.env` старые `DB_*`, запустите старый
бот/worker — старая БД не менялась, всё вернётся как было.
