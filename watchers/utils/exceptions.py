class WatcherError(Exception):
    """Базовое исключение для вотчеров"""
    pass

class AuthError(WatcherError):
    """Ошибка авторизации"""
    pass

class PermanentAuthError(AuthError):
    """Постоянная ошибка авторизации (неверные данные)"""
    pass

class TransientAuthError(AuthError):
    """Временная ошибка авторизации"""
    pass

class ConnectionError(WatcherError):
    """Ошибка подключения"""
    pass

class DataParsingError(WatcherError):
    """Ошибка парсинга данных"""
    pass

class StudentIdGettingError(WatcherError):
    """Ошибка получения student_id"""
    pass

class RequestVerificationTokenError(WatcherError):
    """Ошибка получения токена RequestVerificationToken"""
    pass

class ResponseError(WatcherError):
    def __init__(self, message: str, content: str):
        super().__init__(message)
        self.content = content

class ConfigurationError(WatcherError):
    """Ошибка конфигурации"""
    pass