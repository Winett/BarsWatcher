from enum import StrEnum

__all__ = ["WatchStates", "ServiceKind"]


class WatchStates(StrEnum):
    INITIAL = "initial"
    ACTIVE = "active"
    INACTIVE = "inactive"
    WAITING_2FA = "waiting_2fa"
    INVALID_CREDS = "invalid_creds"


class ServiceKind(StrEnum):
    BARS = "bars"
    OSEP = "osep"
