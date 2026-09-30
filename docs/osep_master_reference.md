# Справка по клиенту ОСЭП/OWA из ветки `master` (для реализации нового клиента в `rewrite`)

Источник: `git show master:<путь>`. Ключевые файлы `master`:
`watchers/auth/osep_auth.py`, `watchers/api/osep_api.py`, `watchers/api/base_api.py`,
`watchers/auth/base_auth.py`, `watchers/watchers/osep_watcher.py`,
`watchers/models/mail_models.py`, `watchers/session/pool_session.py`,
`watchers/utils/file_cache_manager.py`, `watchers/utils/decorators.py`,
`watchers/services/notification_service.py`, `watchers/managers/watcher_manager.py`,
`watchers/connectors/connection_monitor.py`, `loader.py`, `handlers/users/callback/osep.py`.

**Важно:** в `master` НЕТ IMAP/POP/SMTP/EWS-SOAP кода (`git grep -i imap|pop3|smtp` пуст).
Весь клиент — это OWA (Exchange 2013) **JSON API** поверх `/owa/service.svc` через `aiohttp`.
Продакшен-ветка использует только этот вариант.

---

## 1. Авторизация

**Базовый URL:** `https://mail.mpei.ru`

Механизм — **forms-based auth (FBA) Exchange 2013 через `/CookieAuth.dll?Logon`**.
Это НЕ NTLM/Basic/digest: обычный POST HTML-формы (`application/x-www-form-urlencoded`),
после чего сервер ставит сессионные куки (cadata и др.).

Полный flow (`watchers/auth/osep_auth.py`, `watchers/auth/base_auth.py`):

```python
BASE_URL = "https://mail.mpei.ru"

async def is_authenticated(self) -> bool:
    async with self.session.get(
        f"{self.BASE_URL}/owa/#path=/mail",
        allow_redirects=False
    ) as response:
        if response.status == 200:
            return True
    return False

@_rate_limit   # RateLimiter(max_requests=15, period_seconds=1.5)
async def _login_with_credentials(self) -> bool:
    self.session.cookie_jar.clear()

    await self.session.get(
        f"{self.BASE_URL}/owa/#path=/mail",
        allow_redirects=False
    )

    data = {
        'curl': 'Z2FowaZ2F',
        'flags': 0,
        'forcedownlevel': 0,
        'formdir': 2,
        'username': self.credentials.username,
        'password': self.credentials.password,
        'isUtf8': 1,
        'trusted': 4
    }
    await self.session.post(
        f"{self.BASE_URL}/CookieAuth.dll?Logon",
        data=data,
        allow_redirects=False
    )

    if await self.is_authenticated():
        self._save_cookies()
        return True
    return False
```

Пошагово:
1. `GET https://mail.mpei.ru/owa/#path=/mail` (фрагмент не отправляется на сервер, реально `GET /owa/`), `allow_redirects=False` — «покормить» куки.
2. `POST https://mail.mpei.ru/CookieAuth.dll?Logon` с формой выше (через `data=`, т.е. urlencoded). `curl=Z2FowaZ2F` — константа OWA, `formdir=2`, `trusted=4`, `isUtf8=1` — тоже константы.
3. Проверка успеха: `GET /owa/#path=/mail` с `allow_redirects=False` → **HTTP 200 = успех** (неавторизованный получает 302 на логин-страницу).

**2FA в ОСЭП нет** — `send_2fa_code()` пустой (`pass`), `verify_2fa_code()` всегда `False`.

**Определение успеха входа** — только статус 200 на проверочном GET (никаких токенов/парсинга тела).
На API-запросах «сессия слетела» определяется по **HTTP 302 или 401** → `AuthError` → re-login + повтор запроса.

**X-OWA-CANARY** — canary-токен OWA, живёт в куке:

```python
@property
def x_owa_canary(self):
    a = self.session.cookie_jar.filter_cookies(self.BASE_URL).get('X-OWA-CANARY', "")
    return a.value if a else a
```

Передаётся в каждом API-запросе заголовком `X-Owa-Canary` (и query-параметром `X-OWA-CANARY` для вложений).
После re-login canary обновляется (см. §6).

**Общие заголовки всех OWA-запросов** (`OsepAPI.headers_to_update(action)`):

```python
{
    "Action": action,                       # имя OWA-действия: GetOwaUserConfiguration / FindFolder /
                                            # FindConversation / GetConversationItems / GetFileAttachment
    "X-Owa-Canary": self.auth.x_owa_canary,
    "x-requested-with": "XMLHttpRequest",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 OPR/125.0.0.0 (Edition Yx GX)",
    "referer": "https://mail.mpei.ru/owa/",
}
```

JSON-тела передаются через `json=data` aiohttp → `Content-Type: application/json`.
**Никакого SOAP/XML** — это OWA JSON-протокол (`__type": "...JsonRequest:#Exchange"`), формально похожий на EWS, но JSON.

Логин rate-limited: `RateLimiter(max_requests=15, period_seconds=1.5)` (скользящее окно, общий на всех).
`login()` (BaseAuth): сначала попытка через сохранённые куки (`_login_with_cookies`: загрузить куки → `is_authenticated()`), затем по логину/паролю. Декоратор `@retry(max_attempts=4, delays=(1, 2, 5), exclude_exceptions=(AuthError,))` — до 4 попыток с паузами 1/2/5 с.

---

## 2. Точные эндпоинты и тела запросов

Все — `POST https://mail.mpei.ru/owa/service.svc` (JSON), плюс один GET для вложений.

### 2.1 GetOwaUserConfiguration — получить root folder id

```
POST https://mail.mpei.ru/owa/service.svc?action=GetOwaUserConfiguration
Headers: Action: GetOwaUserConfiguration, X-Owa-Canary, x-requested-with: XMLHttpRequest, ...
Body: ПУСТОЙ (вообще не передаётся!)
```

```python
answer = await self._request_with_authorization(
    endpoint="/owa/service.svc?action=GetOwaUserConfiguration",
    method="POST",
    headers=headers,
)
answer = json.loads(answer)
session_settings = answer.get("SessionSettings", {})
folder_names = session_settings.get("DefaultFolderNames", [])
ind = folder_names.index("msgfolderroot")
self._root_folder_id = session_settings.get("DefaultFolderIds", [])[ind].get("Id", "")
```

Ответ JSON: `SessionSettings.DefaultFolderNames` — список имён (`"inbox"`, `"msgfolderroot"`, ...),
`SessionSettings.DefaultFolderIds` — параллельный список `{"Id": "..."}`. Корневая папка = id с индексом `"msgfolderroot"`.
Результат кэшируется в `self._root_folder_id`.

### 2.2 FindFolder — дерево папок со счётчиками

```
POST https://mail.mpei.ru/owa/service.svc          # params={"action": "FindFolder"}
Headers: Action: FindFolder, X-Owa-Canary, ...
Content-Type: application/json
```

```python
data = {
    "__type": "FindFolderJsonRequest:#Exchange",
    "Header": {
        "__type": "JsonRequestHeaders:#Exchange",
        "RequestServerVersion": "Exchange2013",
        "TimeZoneContext": {
            "__type": "TimeZoneContext:#Exchange",
            "TimeZoneDefinition": {
                "__type": "TimeZoneDefinitionType:#Exchange",
                "Id": "Russian Standard Time"
            }
        }
    },
    "Body": {
        "__type": "FindFolderRequest:#Exchange",
        "FolderShape": {
            "__type": "FolderResponseShape:#Exchange",
            "BaseShape": "IdOnly"
        },
        "Paging": {
            "__type": "IndexedPageView:#Exchange",
            "BasePoint": "Beginning",
            "Offset": 0,
            "MaxEntriesReturned": 10000
        },
        "ParentFolderIds": [
            {
                "__type": "FolderId:#Exchange",
                "ChangeKey": "AQAAAA==",
                "Id": root_folder_id
            }
        ],
        "Traversal": "Deep",
        "ShapeName": "Folder",
        "ReturnParentFolder": True,
        "RequiredFolders": ["inbox"],
        "FoldersToMoveToTop": None
    }
}
```

Разбор ответа: `Body.ResponseMessages.Items[0].RootFolder` → `{"Folders": [...]}`.
Поля папки (`watchers/models/mail_models.py`):
`FolderId {Id, ChangeKey}`, `ParentFolderId`, `DisplayName`, `TotalCount`, `UnreadCount`,
`ChildFolderCount`, `FolderClass`, `EffectiveRights`, `ExtendedProperty`.

### 2.3 FindConversation — список тредов в папке (поиск «новых»)

```
POST https://mail.mpei.ru/owa/service.svc?action=FindConversation
Headers: Action: FindConversation, X-Owa-Canary, ...
```

```python
payload = {
    "__type": "FindConversationJsonRequest:#Exchange",
    "Header": {
        "__type": "JsonRequestHeaders:#Exchange",
        "RequestServerVersion": "Exchange2013",
        "TimeZoneContext": {
            "__type": "TimeZoneContext:#Exchange",
            "TimeZoneDefinition": {
                "__type": "TimeZoneDefinitionType:#Exchange",
                "Id": "Russian Standard Time"
            }
        }
    },
    "Body": {
        "__type": "FindConversationRequest:#Exchange",
        "ParentFolderId": {
            "__type": "TargetFolderId:#Exchange",
            "BaseFolderId": {
                "__type": f"{type_folder_id}:#Exchange",   # "FolderId" (реальный id) или "DistinguishedFolderId" ("inbox")
                "Id": folder_id
            }
        },
        "ConversationShape": {
            "__type": "ConversationResponseShape:#Exchange",
            "BaseShape": "IdOnly"
        },
        "ShapeName": "ConversationListView",
        "Paging": {
            "__type": "IndexedPageView:#Exchange",
            "BasePoint": "Beginning",
            "Offset": 0,
            "MaxEntriesReturned": max_entries_returned
        },
        "ViewFilter": "All",
        "SortOrder": [
            {
                "__type": "SortResults:#Exchange",
                "Order": "Descending",
                "Path": {
                    "__type": "PropertyUri:#Exchange",
                    "FieldURI": "ConversationLastDeliveryTime"
                }
            }
        ]
    }
}
```

Вызов из вотчера: `find_conversations_from_folder(folder_id=<реальный FolderId>, type_folder_id="FolderId", max_entries_returned=<число новых>)`.
Ответ (сырой dict, парсится вручную): `Body.Conversations` — список объектов, из каждого берут `ConversationId.Id`.
При не-JSON ответе → `ResponseError` (контент уходит админу).

### 2.4 GetConversationItems — содержимое треда (письмо)

```
POST https://mail.mpei.ru/owa/service.svc          # params={"action": "GetConversationItems"}
Headers: Action: GetConversationItems, X-Owa-Canary, ...
```

```python
data = {
    "__type": "GetConversationItemsJsonRequest:#Exchange",
    "Header": {
        "__type": "JsonRequestHeaders:#Exchange",
        "RequestServerVersion": "Exchange2013",
        "TimeZoneContext": {
            "__type": "TimeZoneContext:#Exchange",
            "TimeZoneDefinition": {
                "__type": "TimeZoneDefinitionType:#Exchange",
                "Id": "Russian Standard Time"
            }
        }
    },
    "Body": {
        "__type": "GetConversationItemsRequest:#Exchange",
        "Conversations": [
            {
                "__type": "ConversationRequestType:#Exchange",
                "ConversationId": {
                    "__type": "ItemId:#Exchange",
                    "Id": conversation_id
                }
            }
        ],
        "ItemShape": {
            "__type": "ItemResponseShape:#Exchange",
            "BaseShape": "IdOnly",
            "BodyType": "Text",          # тело как текст, не HTML
            "FilterHtmlContent": True
        },
        "ShapeName": "ItemPartUniqueBody",
        "SortOrder": "DateOrderDescending",
    }
}
```

Разбор ответа: `Body.ResponseMessages.Items[0].Conversation.ConversationNodes` → берётся
**`conversation_nodes[0].items[0]`** — самое свежее письмо треда (SortOrder DateOrderDescending).

### 2.5 GetFileAttachment — скачать вложение

```
GET https://mail.mpei.ru/owa/service.svc/s/GetFileAttachment?id=<AttachmentId.Id>&X-OWA-CANARY=<canary>
Headers: Action: GetFileAttachment, X-Owa-Canary, x-requested-with, user-agent, referer
```

```python
params = {
    "id": attachment.attachment_id.id,
    "X-OWA-CANARY": self.auth.x_owa_canary
}
url = f"{self.base_url}/owa/service.svc/s/GetFileAttachment"
```

Ответ — **сырые байты файла** (НЕ base64!), отдаётся потоково (`iter_chunked(65536)`).
Статус 302/401 → `AuthError`. Лимит размера 100 МБ (по `Content-Length` и по факту записи);
при превышении файл удаляется с диска и вложение пропускается (`return None`).

---

## 3. Получение писем и определение «новых»

Алгоритм `OsepWatcher` (poll-цикл, `poll_interval` дефолт 60 с):

1. **Снимок папок**: `get_folders()` = FindFolder (Deep от `msgfolderroot`) → dict `{DisplayName: Folder}`.
2. Снимок сохраняется в **Redis** (ключ `OsepWatcher_{username}`, TTL `cache_ttl` = 86400 с) — это «старые данные».
3. На следующем цикле `detect_changes(old, new)`: для каждой папки (кроме игнорируемых, §5)
   сравнивается **`TotalCount`**: если `new_count > old_count` → `diff = new_count - old_count` новых писем.
4. Для папки с приростом: `FindConversation(folder_id=<FolderId папки>, type_folder_id="FolderId", MaxEntriesReturned=diff)` —
   топ-N тредов по `ConversationLastDeliveryTime` desc — и для каждого `ConversationId.Id`
   отдельной `asyncio.create_task` вызывается `get_conversation_items` → берётся письмо `conversation_nodes[0].items[0]`.

**Определение «нового» — ТОЛЬКО по счётчику `TotalCount` папки.** Никакой дедупликации по ItemId/ChangeKey,
никакого поиска по дате. Первый цикл (пустой кэш) ничего не отправляет. Это важно воспроизвести/улучшить в рерайте.

**Поля письма** (из `Conversation`/`Items`, `mail_models.py`), реально используются:
- `Subject` — тема
- `From.Mailbox.Name` / `From.Mailbox.EmailAddress` — отправитель (также есть `Sender`)
- `UniqueBody.Value` — **тело письма** (текст, `BodyType: Text`, `FilterHtmlContent: True`)
- `HasAttachments: bool` и `Attachments: [{AttachmentId:{Id}, ContentId, ContentType, Name, Size, LastModifiedTime}]`
- `ConversationId.Id`, `ItemId {Id, ChangeKey}`, `InternetMessageId`, `DateTimeCreated/Received/Sent`,
  `IsRead`, `IsDraft`, `Importance`, `Sensitivity`, `Size`, `ToRecipients[]`, `DisplayTo`, `RecipientCounts`

Формат уведомления (`MailMessage.format_message()`):

```
От: {sender_name}
Тема: {subject}

{body.replace('<', ' ').replace('>', ' ')}     # < и > заменяются, чтобы Telegram не парсил теги

Есть вложения!                                  # если has_attachments
```

Заголовок уведомления в Telegram: `"Новое письмо!"`. Текст экранируется `html.escape` перед отправкой.

**Blacklist отправителей** (`OsepWatcherConfig.blacklist`, JSON-список из БД `osep_blacklist`):
проверка `fnmatch.fnmatch(email.lower(), pattern.lower())` — с wildcard `*`; совпало → письмо пропускается.

---

## 4. Вложения

**Скачивание** — `load_attachment_to_file` (см. §2.5): GET `GetFileAttachment`, потоковая запись
на диск чанками по 64 КБ. В память целиком не грузится (есть и `load_attachment() -> bytes`, но вотчер его не использует).

**Файловый кэш** (`WORKDIR/cashed_files/`, `WORKDIR` = корень проекта):
имя файла на диске — `"{file_hash}_{attachment.name}.bin"`, в Telegram отдаётся `original_name`.

**«Не качать повторно» — двухуровневый кэш:**
1. **Redis-ключ** `attachment_osep_{file_hash}` с метаданными `{content_type, filename, size, id, content: None}`, TTL = `cache_file_ttl` (600 с = 10 мин).
2. Хеш (`file_hash = md5(...)`):
   - `size < 1024` байт: `md5(f"{user_id}{name}{size}")` — персональные файлы (расчётные листы и т.п.) **не кэшируются между пользователями**;
   - `size >= 1024`: `md5(f"{name}{size}")` — общий кэш на всех пользователей (одинаковая рассылка не качается повторно).

Логика: если метаданные в Redis есть → файл НЕ качается, используется локальный файл
`cashed_files/{file_hash}_{filename}.bin`; **если файла нет на диске — вложение просто пропускается**
(warning «не найден на диске, пропуск», без перекачивания). Если кэша нет → скачать, записать метаданные в Redis,
запланировать удаление файла.

**Удаление файлов** — `FileCacheManager.schedule_removal(filename, ttl=cache_file_ttl)`:
asyncio-задача спит `ttl` секунд (600) и делает `unlink`. Удаление **по TTL после скачивания, а не после отправки**
в Telegram. Повторное `schedule_removal` для того же файла отменяет старую задачу. `cancel_all()` — при shutdown.

**Отправка в Telegram** (`TelegramNotificationService`):
`DiskFile(path, original_name)` → `FSInputFile(path, filename=original_name)` → `InputMediaDocument`,
`send_media_group` по 10 файлов за раз; rate limit 25 сообщений/с; `TelegramRetryAfter` → повтор до 5 раз.
При неудаче отправки вложений — текстовое сообщение «⚠️ Не удалось отправить вложения».

---

## 5. Папки

- **Корень**: `msgfolderroot` (из `GetOwaUserConfiguration`, §2.1). Обход `Traversal: "Deep"` — все папки, включая вложенные.
- Отслеживаются **все папки с растущим `TotalCount`**, кроме игнорируемых (`ignore_folders` в `detect_changes`):

```python
ignore_folders = {
    "Журнал", "Задачи", "Заметки", "Исходящие", "Нежелательная почта",
    "Отправленные", "Удаленные", "Черновики",
    "Conversation Action Settings", "Working Set",
    "{06967759-274D-40B2-A3EB-D7F9E73727D7}",
    "{A9E2BC46-B3A0-4243-B315-60D991004455}",
    "GAL Contacts", "Recipient Cache",
}
```

- «Входящие» определяются не по имени, а попадают под общий механизм (папка `"Входящие"`/`"Inbox"` не в ignore-списке);
  в FindFolder есть `RequiredFolders: ["inbox"]`. Для поиска в конкретной папке используется её реальный
  `FolderId.Id` (не `DistinguishedFolderId`), т.е. `type_folder_id="FolderId"`.

---

## 6. Разлогин, сессии, таймауты, повторные попытки

**Сессии** (`watchers/session/pool_session.py`):
- `aiohttp.ClientSession` на пару `(user_id, service="osep")`; `TCPConnector` шарится по домену `mail.mpei.ru`:
  `ssl=False` (!!! TLS не проверяется), `limit=100`, `limit_per_host=10`, `keepalive_timeout=30`, `ttl_dns_cache=300`.
- `connector_owner=False` — закрытие сессии не закрывает коннектор; `cookie_jar` переносится из закрытой сессии.
- `timeout=aiohttp.ClientTimeout(total=30)`.

**Cookie-файл**: `WORKDIR/sessions/{username}_OsepAuth.json` — JSON `{cookie_name: value}`.
Сохраняется после успешного логина, загружается при старте (`login()` сначала пробует куки — валидирует через `is_authenticated()`).

**Разлогин**: явного logout-запроса к серверу НЕТ. Выключение ОСЭП = `OsepWatcherManager.stop_and_delete(user_id)`
(отмена задач, включая задачи скачивания/обработки писем) + `PoolSession.release(user_id, "osep")` (закрытие сессии).
Cookie-файл не удаляется, БД-флаг `used_osep=False`.

**Повторные попытки** (`watchers/utils/decorators.py`):
- `@retry(max_attempts=4, delays=(1, 2, 5))` — на `_request_with_authorization`, `_request`, `login`.
- `AuthError` НЕ ретраится декоратором: вместо этого `_request_with_authorization` делает **re-login и один повтор запроса**,
  обновляя заголовок `'X-OWA-CANARY'` (баг-фича: merge идёт по ключу `X-OWA-CANARY`, а в `headers_to_update` ключ `X-Owa-Canary` —
  в итоге могут присутствовать два заголовка).
- Фатальная ошибка авторизации в цикле → событие `EXCEPTION` → пользователю «Неверный логин или пароль»,
  вотчер останавливается, `used_osep` сбрасывается.

**Мониторинг доступности** (`OsepMonitor`):
```
GET https://mail.mpei.ru/CookieAuth.dll?GetLogon?curl=Z2FowaZ2FZ3FbOZ3D1&reason=0&formdir=2#path=/mail
```
state machine CONNECTED/DEGRADED/DISCONNECTED/RECOVERING; при DISCONNECTED все вотчеры ставятся на паузу,
при восстановлении — staggered resume (база 2.0 с + случайный jitter до 3.0 с). При включении ОСЭП
проверяется живость сессии простым `api.get_folders()`.

**Конфиг** (`WatcherConfig`): `poll_interval=60`, `timeout=30`, `max_retries=3`, `retry_delay=5`,
`cache_ttl=86400` (Redis-снимок папок), `cache_file_ttl=600` (файлы вложений и Redis-метаданные вложений).
AutoScaler может менять poll_interval (base/max из GlobalConfig в БД).

---

## Краткая шпаргалка запросов

| Шаг | Метод | URL | Тело |
|---|---|---|---|
| warm-up | GET | `/owa/#path=/mail` (реально `/owa/`), no redirects | — |
| login | POST | `/CookieAuth.dll?Logon` | form: `curl=Z2FowaZ2F&flags=0&forcedownlevel=0&formdir=2&username=..&password=..&isUtf8=1&trusted=4` |
| check auth | GET | `/owa/#path=/mail`, no redirects | 200 = OK |
| root folder | POST | `/owa/service.svc?action=GetOwaUserConfiguration` | пусто |
| folders | POST | `/owa/service.svc?action=FindFolder` | `FindFolderJsonRequest` (см. §2.2) |
| conversations | POST | `/owa/service.svc?action=FindConversation` | `FindConversationJsonRequest` (см. §2.3) |
| letter | POST | `/owa/service.svc?action=GetConversationItems` | `GetConversationItemsJsonRequest` (см. §2.4) |
| attachment | GET | `/owa/service.svc/s/GetFileAttachment?id=..&X-OWA-CANARY=..` | ответ — raw bytes |
| health | GET | `/CookieAuth.dll?GetLogon?curl=Z2FowaZ2FZ3FbOZ3D1&reason=0&formdir=2#path=/mail` | — |
