from src.core.config import settings

from .models import (
    AbsenceRecord,
    Attachment,
    DisciplineMarks,
    LessonInfo,
    MailMessage,
    Mark,
    OsepFolder,
    RewritingMark,
    ScheduleItem,
    StudentBrief,
    StudentProfile,
    StudyGroupInfo,
    TwoFASendResult,
    absences_to_snapshot,
    marks_to_snapshot,
)
from .ports import (
    OSEP_IGNORE_FOLDERS,
    AuthError,
    AuthResult,
    AuthStatus,
    BarsClient,
    DataParseError,
    OsepClient,
    ServiceUnavailableError,
    TwoFactorRequiredError,
)
from .sim import SimBarsClient, SimOsepClient

__all__ = [
    "AuthStatus",
    "AuthResult",
    "ServiceUnavailableError",
    "AuthError",
    "TwoFactorRequiredError",
    "DataParseError",
    "BarsClient",
    "OsepClient",
    "OSEP_IGNORE_FOLDERS",
    "get_bars_client",
    "get_osep_client",
    # модели
    "Mark",
    "RewritingMark",
    "DisciplineMarks",
    "AbsenceRecord",
    "StudentBrief",
    "StudentProfile",
    "StudyGroupInfo",
    "LessonInfo",
    "ScheduleItem",
    "TwoFASendResult",
    "Attachment",
    "MailMessage",
    "OsepFolder",
    "marks_to_snapshot",
    "absences_to_snapshot",
]


def get_bars_client(user_id: int) -> BarsClient:
    if settings.integration_mode == "sim":
        return SimBarsClient(user_id)
    if settings.integration_mode == "file":
        from .file_sim import FileSimBarsClient

        return FileSimBarsClient(user_id)
    from .bars import BarsWebClient

    return BarsWebClient()


def get_osep_client(user_id: int) -> OsepClient:
    if settings.integration_mode == "sim":
        return SimOsepClient(user_id)
    if settings.integration_mode == "file":
        from .file_sim import FileSimOsepClient

        return FileSimOsepClient(user_id)
    from .osep import OsepWebClient

    return OsepWebClient()
