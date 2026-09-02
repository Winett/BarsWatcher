import imaplib
import poplib
import ssl
import socket
from email.parser import BytesParser
from email.message import Message
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime


@dataclass
class EmailMessage:
    """Представление email сообщения"""

    id: str
    subject: str
    from_address: str
    to_addresses: List[str]
    date: datetime
    body_text: str
    body_html: Optional[str] = None
    attachments: List[Dict[str, Any]] = None

    def __post_init__(self):
        if self.attachments is None:
            self.attachments = []


class MailClient:
    """Клиент для работы с почтой через IMAP/POP3"""

    def __init__(
        self,
        server: str,
        username: str,
        password: str,
        port: int = 993,
        protocol: str = "IMAP",
    ):
        """
        Инициализация клиента

        Args:
            server: Адрес почтового сервера
            username: Логин пользователя
            password: Пароль
            port: Порт (993 для IMAP SSL, 995 для POP3 SSL)
            protocol: Протокол ('IMAP' или 'POP3')
        """
        self.server = server
        self.username = username
        self.password = password
        self.port = port
        self.protocol = protocol.upper()
        self.connection = None
        self.ssl_context = self._create_ssl_context()

    def _create_ssl_context(self) -> ssl.SSLContext:
        """Создает SSL контекст для подключения"""
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.set_ciphers("ALL:@SECLEVEL=0")

        return context

    def connect(self) -> bool:
        """Подключение к почтовому серверу"""
        try:
            if self.protocol == "IMAP":
                self.connection = imaplib.IMAP4_SSL(
                    self.server, self.port, ssl_context=self.ssl_context
                )
                self.connection.login(self.username, self.password)
            elif self.protocol == "POP3":
                # POP3 требует ручной обертки сокета
                sock = socket.create_connection((self.server, self.port), timeout=10)
                ssl_sock = self.ssl_context.wrap_socket(
                    sock, server_hostname=self.server
                )

                self.connection = poplib.POP3(self.server)
                self.connection.sock = ssl_sock
                self.connection.user(self.username)
                self.connection.pass_(self.password)

            print(f"✓ Подключено к {self.server}:{self.port} ({self.protocol})")
            return True

        except Exception as e:
            print(f"✗ Ошибка подключения: {e}")
            return False

    def disconnect(self):
        """Отключение от сервера"""
        if self.connection:
            try:
                if self.protocol == "IMAP":
                    self.connection.logout()
                else:
                    self.connection.quit()
                print("✓ Отключено")
            except Exception as e:
                print(f"⚠ Предупреждение при отключении: {e}")

    def get_messages_count(self) -> int:
        """Получить количество сообщений во входящих"""
        try:
            if self.protocol == "IMAP":
                self.connection.select("INBOX")
                status, messages = self.connection.search(None, "ALL")
                if status == "OK":
                    return len(messages[0].split())
            else:  # POP3
                response, messages, octets = self.connection.list()
                return len(messages)
        except Exception as e:
            print(f"✗ Ошибка получения количества сообщений: {e}")
            return 0

    def get_message_ids(self) -> List[str]:
        """Получить ID всех сообщений"""
        try:
            if self.protocol == "IMAP":
                self.connection.select("INBOX")
                status, messages = self.connection.search(None, "ALL")
                if status == "OK":
                    return [msg_id.decode("utf-8") for msg_id in messages[0].split()]
            else:  # POP3
                response, messages, octets = self.connection.list()
                # В POP3 возвращаем номера сообщений
                return [str(i + 1) for i in range(len(messages))]
        except Exception as e:
            print(f"✗ Ошибка получения ID сообщений: {e}")
            return []

    def _decode_header(self, header_value: str) -> str:
        from email.header import decode_header

        if not header_value:
            return ""

        decoded_parts = decode_header(header_value)
        result = []

        for part, charset in decoded_parts:
            if isinstance(part, bytes):
                # Декодируем байты с указанной кодировкой
                if charset:
                    try:
                        decoded = part.decode(charset)
                    except (LookupError, UnicodeDecodeError):
                        # Если кодировка не найдена или ошибка декодирования
                        # пробуем utf-8, затем latin-1
                        try:
                            decoded = part.decode("utf-8")
                        except:
                            decoded = part.decode("latin-1")
                else:
                    # Нет кодировки, пробуем utf-8
                    try:
                        decoded = part.decode("utf-8")
                    except:
                        decoded = part.decode("latin-1")
                result.append(decoded)
            else:
                # Уже строка
                result.append(part)

        return "".join(result)

    def get_message(self, message_id: str) -> Optional[EmailMessage]:
        """Получить сообщение по ID"""
        try:
            if self.protocol == "IMAP":
                status, msg_data = self.connection.fetch(message_id, "(RFC822)")
                if status != "OK":
                    return None
                msg_content = msg_data[0][1]
            else:  # POP3
                response, lines, octets = self.connection.retr(int(message_id))
                msg_content = b"\n".join(lines)

            # Парсим сообщение
            parser = BytesParser()
            message: Message = parser.parsebytes(msg_content)

            # Извлекаем данные С ДЕКОДИРОВАНИЕМ
            subject = self._decode_header(message["Subject"]) or "Без темы"
            from_address = self._decode_header(message["From"]) or "Unknown"

            # Обрабатываем получателей
            to_header = message["To"] or ""
            to_decoded = self._decode_header(to_header)
            to_addresses = [
                addr.strip() for addr in to_decoded.split(",") if addr.strip()
            ]

            # Дата
            date_str = message["Date"]
            try:
                date = datetime.strptime(date_str[:25], "%a, %d %b %Y %H:%M:%S")
            except:
                date = datetime.now()

            # Тело письма (остальной код без изменений...)
            body_text = ""
            body_html = None
            attachments = []

            if message.is_multipart():
                for part in message.walk():
                    content_type = part.get_content_type()
                    content_disposition = str(part.get("Content-Disposition", ""))

                    if (
                        content_type == "text/plain"
                        and "attachment" not in content_disposition
                    ):
                        payload = part.get_payload(decode=True)
                        if payload:
                            charset = part.get_content_charset() or "utf-8"
                            body_text = payload.decode(charset, errors="ignore")

                    elif (
                        content_type == "text/html"
                        and "attachment" not in content_disposition
                    ):
                        payload = part.get_payload(decode=True)
                        if payload:
                            charset = part.get_content_charset() or "utf-8"
                            body_html = payload.decode(charset, errors="ignore")

                    elif "attachment" in content_disposition:
                        filename = self._decode_header(part.get_filename() or "")
                        payload = part.get_payload(decode=True)
                        if payload and filename:
                            attachments.append(
                                {
                                    "filename": filename,
                                    "content_type": content_type,
                                    "data": payload,
                                    "size": len(payload),
                                }
                            )
            else:
                payload = message.get_payload(decode=True)
                if payload:
                    charset = message.get_content_charset() or "utf-8"
                    body_text = payload.decode(charset, errors="ignore")

            return EmailMessage(
                id=message_id,
                subject=subject,
                from_address=from_address,
                to_addresses=to_addresses,
                date=date,
                body_text=body_text,
                body_html=body_html,
                attachments=attachments,
            )

        except Exception as e:
            print(f"✗ Ошибка получения сообщения {message_id}: {e}")
            return None

    def get_latest_messages(self, count: int = 10) -> List[EmailMessage]:
        """
        Получить последние N сообщений

        Args:
            count: Количество сообщений

        Returns:
            Список EmailMessage
        """
        message_ids = self.get_message_ids()

        if not message_ids:
            return []

        # Берем последние count сообщений
        latest_ids = message_ids[-count:]

        messages = []
        for msg_id in latest_ids:
            msg = self.get_message(msg_id)
            if msg:
                messages.append(msg)

        return messages

    def search_messages(self, criteria: str = "ALL") -> List[str]:
        """
        Поиск сообщений по критериям (только IMAP)

        Args:
            criteria: Критерий поиска IMAP (например, 'UNSEEN', 'FROM "example.com"')

        Returns:
            Список ID найденных сообщений
        """
        if self.protocol != "IMAP":
            print("✗ Поиск доступен только для IMAP")
            return []

        try:
            self.connection.select("INBOX")
            status, messages = self.connection.search(None, criteria)

            if status == "OK":
                return [msg_id.decode("utf-8") for msg_id in messages[0].split()]
            return []

        except Exception as e:
            print(f"✗ Ошибка поиска: {e}")
            return []

    def mark_as_read(self, message_id: str) -> bool:
        """
        Отметить сообщение как прочитанное (только IMAP)

        Args:
            message_id: ID сообщения

        Returns:
            True если успешно
        """
        if self.protocol != "IMAP":
            print("✗ Доступно только для IMAP")
            return False

        try:
            self.connection.store(message_id, "+FLAGS", "\\Seen")
            return True
        except Exception as e:
            print(f"✗ Ошибка маркировки: {e}")
            return False

    def delete_message(self, message_id: str) -> bool:
        """
        Удалить сообщение

        Args:
            message_id: ID сообщения

        Returns:
            True если успешно
        """
        try:
            if self.protocol == "IMAP":
                self.connection.store(message_id, "+FLAGS", "\\Deleted")
                self.connection.expunge()
            else:  # POP3
                self.connection.dele(int(message_id))
            return True
        except Exception as e:
            print(f"✗ Ошибка удаления: {e}")
            return False

    def download_attachments(
        self, message: EmailMessage, save_dir: str = "./attachments"
    ) -> List[str]:
        """
        Сохранить вложения из сообщения

        Args:
            message: Объект EmailMessage
            save_dir: Директория для сохранения

        Returns:
            Список путей к сохраненным файлам
        """
        import os

        os.makedirs(save_dir, exist_ok=True)
        saved_files = []

        for attachment in message.attachments:
            filepath = os.path.join(save_dir, attachment["filename"])

            with open(filepath, "wb") as f:
                f.write(attachment["data"])

            saved_files.append(filepath)
            print(f"✓ Сохранено: {filepath}")

        return saved_files

    def get_folders(self) -> List[str]:
        """
        Получить список всех доступных папок (только IMAP)

        Returns:
            Список названий папок
        """
        if self.protocol != "IMAP":
            print("✗ Получение списка папок доступно только для IMAP")
            return []

        try:
            # LIST "" "*" запрашивает все папки
            status, folders = self.connection.list("", "*")

            if status == "OK":
                folder_list = []
                for item in folders:
                    # Формат ответа: (flags) "/" "FolderName"
                    # Нам нужно извлечь имя папки
                    if isinstance(item, bytes):
                        item = item.decode("utf-8", errors="ignore")

                    # Разбираем строку ответа
                    parts = item.split(' "/" ')
                    if len(parts) > 1:
                        folder_name = parts[-1].strip('"')
                        folder_list.append(folder_name)
                    else:
                        # Иногда разделитель может быть другим
                        folder_list.append(item)

                return folder_list
            return []

        except Exception as e:
            print(f"✗ Ошибка получения списка папок: {e}")
            return []

    def __enter__(self):
        """Контекстный менеджер - подключение"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Контекстный менеджер - отключение"""
        self.disconnect()


# Пример использования
if __name__ == "__main__":
    # Использование с контекстным менеджером
    with MailClient(
        server="mail.mpei.ru",
        username="AntipovVA",
        password="hel835v",
        port=993,
        protocol="IMAP",
    ) as client:
        print(f"Папки: {client.get_folders()}")
        # Получить количество сообщений
        count = client.get_messages_count()
        print(f"\nВсего сообщений: {count}")

        # Получить последние 5 сообщений
        print("\nПоследние 5 сообщений:")
        messages = client.get_latest_messages(5)

        for i, msg in enumerate(messages, 1):
            print(f"\n{i}. {msg.subject}")
            print(f"   От: {msg.from_address}")
            print(f"   Дата: {msg.date.strftime('%Y-%m-%d %H:%M')}")
            print(f"   Текст: {msg.body_text[:100]}...")

            if msg.attachments:
                print(f"   Вложений: {len(msg.attachments)}")
                print(msg.attachments[0])
                # Сохранить вложения
                # client.download_attachments(msg)

        # Поиск непрочитанных сообщений (IMAP)
        if client.protocol == "IMAP":
            unread_ids = client.search_messages("UNSEEN")
            print(f"\nНепрочитанных сообщений: {len(unread_ids)}")
