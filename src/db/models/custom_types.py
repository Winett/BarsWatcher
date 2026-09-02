from sqlalchemy.types import String, TypeDecorator
from src.core.security import SecurityManager
from src.core.config import settings

security_manager = SecurityManager(settings.secret_key.get_secret_value())


class EncryptedString(TypeDecorator):
    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            return security_manager.encrypt(value)
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            return security_manager.decrypt(value)
        return value
