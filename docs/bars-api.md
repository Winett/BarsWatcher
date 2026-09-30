# БАРС (bars.mpei.ru) — фактический web-API

Реализация: `src/integrations/bars/` (`parser.py` — чистый парсинг HTML,
`auth.py` — вход/2FA/сессия, `api.py` — эндпоинты, `client.py` — `BarsWebClient`
по порту `BarsClient`). Семантика оценок (КМ, переписывания, итоговая)
совпадает с продакшеном master (`watchers/api/bars_api.py`).

## Авторизация

1. `GET /bars_web/` → из страницы берём `value` элемента
   `name=__RequestVerificationToken`.
2. `POST /bars_web/` с form-data:
   `__RequestVerificationToken`, `StopOpenDefault=False`, `Account=<login>`,
   `Password=<password>`, `RememberMe=True`.
3. Проверка кук:
   - нет `ses_bars` → неверный логин/пароль;
   - `ses_bars` + `auth_bars` → авторизованы;
   - `ses_bars` без `auth_bars` → требуется 2FA.

**Разлогин**: любой запрос данных отвечает 302 с редиректом на
`https://bars.mpei.ru/bars_web/` → перелогин (в `BarsWebClient._with_relogin`
один повтор; если перелогин снова требует 2FA — `TwoFactorRequiredError`,
worker переводит пользователя в WAITING_2FA и запрашивает новый код).

## 2FA

1. Со страницы после POST: новый `__RequestVerificationToken` и
   `a#btnSend` → `onclick="af2_code_send('btnSend', '/bars_web/Auth/JSON_SendAF2_Code', '2', '4');"` —
   тип 2FA (`tid`) = второй элемент с конца (`'2'`).
2. `GET /bars_web/Auth/JSON_SendAF2_Code?tid=<tid>` →
   `{"success": true, "message": "Сообщение отправлено в 'ВКонтакте'"}`
   — `message` показываем пользователю («код отправлен вам на ...»).
3. `POST /bars_web//Auth/LoginCode` с form-data:
   `__RequestVerificationToken` (новый), `StopOpenDefault=False`,
   `Account=<login>`, `RememberMe=True`, `AF2_Code=<код>`.
4. Появилась кука `auth_bars` → код верный; иначе — неверный (из ответа
   берём новый токен для повторной попытки).

Состояние между задачами worker хранится в `bars_states`
(`cookies` + `pending_2fa` = `{token, tid, account}`).

## Данные

| Что | Эндпоинт | Параметры | Парсер |
|---|---|---|---|
| studentID + ФИО | `GET /bars_web/ST/Student/ListStudent` | — | `parse_student_brief` (таблица `tbl__PartialListStudent`) |
| Оценки | `GET /bars_web/ST_Study/Student_SemesterSheet/_PartialListStudent_SemesterSheet__Mark` | `studentID`, `query={"ID": student_id, "FilterSemester": {"Value": <semester>}}` | `parse_marks` |
| Пропуски | `GET /bars_web/ST_Study/Lesson__Student/_PartialListStudent__Lesson__Student` | `studentID`, `query` с `PageSize=500`, `FilterSemester={"Value": <semester>}` (текущий журнал; `*` — все семестры, нельзя), `FilterLessonSkipType={"Value":"1"}` (только пропуски) | `parse_absences` (ключи `data-ls-id`) |
| Группа (sgID, название) | `GET /bars_web/SG/StudyGroup/_PartialListStudyGroup` | `query` (фильтры по умолчанию) | `parse_study_group` |
| Ответственные за журнал | `GET /bars_web/SG/StudyGroupLeader/_PartialListStudyGroup_Leaders` | `sgID`, `query` | `parse_leaders` (сравнение логина `public\AkimovNA` → `AkimovNA`) |
| Учебные журналы (tjID) | `GET /bars_web/SG/TrainingJournal/_PartialListStudyGroup__TrainingJournals` | `sgID`, `query` | `parse_training_journal_id` (по названию семестра) |
| Занятия журнала (за день) | `GET /bars_web/SG/Lesson/_PartialListTrainingJournal_Lessons` | `tjID`, `query` с `FilterStartDate/FilterEndDate` = сегодня, `DisplayModeFilter` | `parse_lessons` |
| Расписание (открытый) | `GET /bars_web/Open/RUZ/_PartialTimetable` | `rt=3`, `name=<группа>`, `sd`, `ed`, `st=1/2` | `parse_schedule` |

**Семестр**: `id = (academic_year - 2025) * 2 + 26 + offset`, где
`academic_year = год (если месяц ≥ 9) иначе год-1`, `offset = 0` осенью / `1`
весной. 28 — осень 2026/2027, 29 — весна 2026/2027.

**Форма снимка оценок** (`fetch_marks`):
`{дисциплина: DisciplineMarks}` — `marks` (КМ-1..N с датами и переписываниями),
`score` («Балл текущего контроля», например «5,00»), `mark_pa`, `mark_final`.
Diff — `diff_bars_marks` (new_mark / mark_changed / rewriting / score_changed /
final_changed / pa_changed).

**Даты/время в моделях**: структурированно — `date` = ISO («2026-09-21»),
`time_start`/`time_end` = «HH:MM», `pair`/`week` = int; исходные строки
(`lesson`, `day`, `time`, `pair_label`) хранятся рядом как raw для показа.
Форматирование текста («понедельник, 21 сентября») — только в слое отображения.

**Ошибки разделены**: `ServiceUnavailableError` — сервис упал/недоступен (сеть,
5xx, таймаут) — это не ошибка авторизации; `AuthError` — сессия/перелогин;
`TwoFactorRequiredError` — перелогин потребовал новый код 2FA; `DataParseError` —
неожиданный формат ответа; неверный логин/пароль — `AuthStatus.INVALID_CREDENTIALS`.

## Занятия: признак «пара не проставлена» (для фичи напоминаний)

- пара в статусе «на рассмотрении» или «требуется корректировка» и
  отсутствующих нет вовсе → пара не отмечена как есть;
- пары нет в журнале, а в расписании (RUZ) она есть → тоже уведомляем.
