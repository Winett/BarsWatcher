"""Тестовый режим интеграций: данные из файлов simdata/*.json.

INTEGRATION_MODE=file — клиенты читают ответы из файлов, которые можно
править руками; стандартный цикл проверки (diff снимков) создаёт события,
как будто сервисы изменились. Файлы создаются с шаблоном при первом
запуске — дальше их можно редактировать:

  simdata/bars_marks.json     оценки (формат снимка diff)
  simdata/bars_absences.json  пропуски (формат снимка diff)
  simdata/bars_profile.json   профиль {fio, group, is_journal_responsible}
  simdata/osep_folders.json   {имя папки: total_count}
  simdata/osep_mail.json      {папка: [письмо, ...]}
  simdata/service.json        {"bars_up": true, "osep_up": true} (или {"is_up": ...})
  simdata/attachments/<id>    байты вложений для download_attachment
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from src.integrations.models import (
    AbsenceRecord,
    Attachment,
    DisciplineMarks,
    MailMessage,
    Mark,
    RewritingMark,
    StudentProfile,
)
from src.integrations.ports import AuthResult, AuthStatus

__all__ = ["FileSimBarsClient", "FileSimOsepClient", "data_dir"]

_DEFAULTS: dict[str, dict] = {
    "bars_marks.json": {
        "Информатика, Фомин Д.А., зачёт с оценкой": {
            "marks": [
                {"mark": "5", "date": "2026-09-08", "rewriting": []},
                {"mark": "", "date": "", "rewriting": []},
            ],
            "score": "4,00",
            "mark_pa": "",
            "mark_final": "",
        }
    },
    "bars_absences.json": {
        "2649661": {
            "lesson": (
                "22.10.26, 2 пара (11:10-12:45), практическое занятие "
                "(Степанов Г.А.), Спортивные секции (зачёт (без оценки)), А-16-23"
            ),
            "created": "30.08.2026 20:40:40 Степанов Г.А.",
            "changed": "18.12.2026 13:47:57 Степанов Г.А.",
            "date": "2026-10-22",
            "pair": 2,
            "time_start": "11:10",
            "time_end": "12:45",
        }
    },
    "bars_profile.json": {
        "fio": "Тестов Тест Тестович",
        "group": "А-16-23",
        "is_journal_responsible": True,
    },
    "osep_folders.json": {"Входящие": 2},
    "osep_mail.json": {
        "Входящие": [
            {
                "subject": "Допуск к зачёту",
                "sender_name": "Учебный отдел",
                "sender_email": "study@mpei.ru",
                "body": "Добрый день! Во вложении допуск к зачёту.",
                "has_attachments": True,
                "attachments": [
                    {
                        "filename": "dopusk.txt",
                        "content_type": "text/plain",
                        "size": 2048,
                        "attachment_id": "att-dopusk",
                    }
                ],
            }
        ]
    },
    "service.json": {"bars_up": True, "osep_up": True},
}


def data_dir() -> Path:
    import os

    return Path(os.environ.get("SIM_DATA_DIR", "simdata"))


def service_up(service: str) -> bool:
    """Доступность сервиса из simdata/service.json (симуляция падения/подъёма).

    Поддерживаемые формы файла:
      {"bars_up": true, "osep_up": true}   — шаблон по умолчанию;
      {"is_up": false}                     — на оба сервиса сразу;
      {"bars": {"is_up": false}}           — на один сервис.
    SIM_DOWN=1 (или SIM_DOWN=<service>) тоже выключает доступность.
    Используется и sim-, и file-режимом: правка файла = «сервис упал/поднялся».
    """
    import os

    if os.environ.get("SIM_DOWN", "") == service or os.environ.get("SIM_DOWN") == "1":
        return False
    data = _load("service.json")
    node = data.get(service)
    if isinstance(node, dict) and "is_up" in node:
        return bool(node["is_up"])
    if f"{service}_up" in data:
        return bool(data[f"{service}_up"])
    if "is_up" in data:
        return bool(data["is_up"])
    return True


def _load(name: str) -> dict:
    path = data_dir() / name
    if not path.exists():
        data_dir().mkdir(parents=True, exist_ok=True)
        template = _DEFAULTS.get(name, {})
        path.write_text(
            json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("file-sim | создан шаблон {} — правьте его", path)
        return json.loads(json.dumps(template))
    return json.loads(path.read_text(encoding="utf-8"))


def _mark_from_dict(data: dict) -> Mark:
    return Mark(
        mark=data.get("mark", ""),
        date=data.get("date", ""),
        rewriting=[
            RewritingMark(
                mark=entry.get("mark", ""),
                date=entry.get("date", ""),
                not_counted=bool(entry.get("not_counted", False)),
            )
            for entry in data.get("rewriting", [])
        ],
    )


class FileSimBarsClient:
    """БАРС из файлов simdata/ (тестовый режим)."""

    def __init__(self, user_id: int):
        self._user_id = user_id
        self._login: str | None = None

    async def is_authenticated(self) -> bool:
        return True

    async def authenticate(self, login: str, password: str) -> AuthResult:
        self._login = login
        return AuthResult(status=AuthStatus.OK)

    async def send_2fa_code(self):
        from src.integrations.models import TwoFASendResult

        return TwoFASendResult(success=True, destination="тестовый режим (файлы)")

    async def verify_2fa_code(self, code: str):
        result = AuthResult(status=AuthStatus.OK)
        return result

    async def is_journal_responsible(self) -> bool:
        data = _load("bars_profile.json")
        return bool(data.get("is_journal_responsible", False))

    async def fetch_profile(self) -> StudentProfile:
        data = _load("bars_profile.json")
        return StudentProfile(
            fio=data.get("fio", ""),
            group=data.get("group", ""),
            is_journal_responsible=bool(data.get("is_journal_responsible", False)),
        )

    async def fetch_marks(self) -> dict[str, DisciplineMarks]:
        data = _load("bars_marks.json")
        return {
            name: DisciplineMarks(
                name=name,
                marks=[_mark_from_dict(m) for m in values.get("marks", [])],
                score=values.get("score", ""),
                mark_pa=values.get("mark_pa", ""),
                mark_final=values.get("mark_final", ""),
            )
            for name, values in data.items()
        }

    async def fetch_absences(self) -> dict[str, AbsenceRecord]:
        data = _load("bars_absences.json")
        return {
            key: AbsenceRecord(
                key=key,
                lesson=values.get("lesson", ""),
                created=values.get("created", ""),
                changed=values.get("changed", ""),
                date=values.get("date", ""),
                pair=values.get("pair"),
                time_start=values.get("time_start", ""),
                time_end=values.get("time_end", ""),
            )
            for key, values in data.items()
        }

    async def fetch_today_lessons(self) -> list:
        return []

    async def fetch_lessons_range(self, date_from, date_to, **_) -> list:
        return []

    async def fetch_schedule(self, date_from, date_to, group_name=None) -> list:
        return []

    async def check_availability(self) -> bool:
        return service_up("bars")

    def export_session(self) -> dict:
        return {"cookies": {}, "pending_2fa": None}

    def restore_session(self, session: dict | None) -> None:
        return


class FileSimOsepClient:
    """ОСЭП из файлов simdata/ (тестовый режим)."""

    def __init__(self, user_id: int):
        self._user_id = user_id
        self._login: str | None = None

    async def is_authenticated(self) -> bool:
        return True

    async def authenticate(self, login: str, password: str) -> AuthResult:
        self._login = login
        return AuthResult(status=AuthStatus.OK)

    async def fetch_folder_counts(self) -> dict[str, int]:
        return {name: int(count) for name, count in _load("osep_folders.json").items()}

    async def fetch_new_mails(self, folder: str, count: int) -> list[MailMessage]:
        mails = _load("osep_mail.json").get(folder, [])
        result = []
        for entry in mails[:count]:
            result.append(
                MailMessage(
                    subject=entry.get("subject", ""),
                    sender_name=entry.get("sender_name", ""),
                    sender_email=entry.get("sender_email", ""),
                    body=entry.get("body", ""),
                    has_attachments=bool(entry.get("has_attachments")),
                    attachments=[
                        Attachment(
                            filename=a.get("filename", "file"),
                            content_type=a.get("content_type", ""),
                            size=int(a.get("size", 0)),
                            attachment_id=a.get("attachment_id", ""),
                        )
                        for a in entry.get("attachments", [])
                    ],
                )
            )
        return result

    async def download_attachment(self, attachment_id: str) -> bytes:
        path = data_dir() / "attachments" / attachment_id
        if path.exists():
            return path.read_bytes()
        return f"[тестовое вложение {attachment_id}]".encode()

    async def check_availability(self) -> bool:
        return service_up("osep")

    def export_session(self) -> dict:
        return {"cookies": {}, "pending_2fa": None}

    def restore_session(self, session: dict | None) -> None:
        return
