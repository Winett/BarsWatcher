from cryptography.fernet import Fernet
from loguru import logger


class SecurityManager:
    def __init__(self, secret_key: str):
        self._cipher = Fernet(secret_key.encode("utf-8"))

    def encrypt(self, raw_password: str) -> str:
        if not raw_password:
            return ""

        encrypted_password = self._cipher.encrypt(raw_password.encode("utf-8"))

        return encrypted_password.decode("utf-8")

    def decrypt(self, encrypted_password: str) -> str:
        if not encrypted_password:
            return ""
        try:
            decrypted_password = self._cipher.decrypt(
                encrypted_password.encode("utf-8")
            )
            return decrypted_password.decode("utf-8")
        except ValueError:
            logger.error(
                "Невозможно расшифровать пароль! Возможно изменился SECRET_KEY"
            )
            raise ValueError("Ошибка расшифровки пароля")
