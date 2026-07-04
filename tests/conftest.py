import sys
from unittest.mock import MagicMock

# Мокаем модули БД до импорта watchers, чтобы избежать
# подключения к реальной БД при тестировании
db_mock = MagicMock()
db_mock.async_session = MagicMock()
sys.modules['database'] = db_mock
sys.modules['database.db'] = db_mock

user_service_mock = MagicMock()
sys.modules['services'] = MagicMock()
sys.modules['services.user'] = user_service_mock
sys.modules['services.notification'] = MagicMock()
