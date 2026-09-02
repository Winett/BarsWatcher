from enum import Enum


class WatchStates(str, Enum):
    INITIAL = "initial"
    ACTIVE = "active"
    INACTIVE = "inactive"
    WAITING_2FA = "waiting_2fa"
    INVALID_CREDS = "invalid_creds"
