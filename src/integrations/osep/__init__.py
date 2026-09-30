"""Интеграция с ОСЭП (OWA, Exchange 2013): https://mail.mpei.ru."""

from .auth import OwaAuth
from .client import OsepWebClient

__all__ = ["OwaAuth", "OsepWebClient"]
