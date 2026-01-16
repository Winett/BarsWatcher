import atexit
import json
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Generic, Optional, Type, TypeVar, Union
from pydantic import BaseModel
import aiofiles
import threading
import asyncio
import signal
from enum import Enum

T = TypeVar('T')
Serializable = Union[str, dict, BaseModel, Dict[str, BaseModel], list, int, float, bool]

__all__ = ["CacheValue", "FileCache", "SyncFileCacher", "AsyncFileCacher"]

class CacheValue:
    """Класс для хранения значения кэша с метаданными"""

    def __init__(self, value: Any, ttl: Optional[float] = None):
        self.value = self._serialize_value(value)
        self.created_at = time.time()
        self.ttl = ttl

    @staticmethod
    def _serialize_value(value: Any) -> Any:
        """Рекурсивная сериализация значений"""
        if isinstance(value, BaseModel):
            return value.dict()
        elif isinstance(value, dict):
            return {k: CacheValue._serialize_value(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [CacheValue._serialize_value(v) for v in value]
        else:
            return value

    def is_expired(self) -> bool:
        """Проверяет, истекло ли время жизни значения"""
        if self.ttl is None:
            return False
        return time.time() - self.created_at > self.ttl

    def to_dict(self) -> Dict[str, Any]:
        """Преобразует значение кэша в словарь для сериализации"""
        return {
            'value': self.value,
            'created_at': self.created_at,
            'ttl': self.ttl
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CacheValue':
        """Создает CacheValue из словаря"""
        cache_value = cls(data['value'], data['ttl'])
        cache_value.created_at = data['created_at']
        return cache_value


class BaseCache(ABC):
    """Абстрактный базовый класс для кэша"""

    @abstractmethod
    def get(self, key: str) -> Optional[Any]:
        pass

    @abstractmethod
    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        pass

    @abstractmethod
    def delete(self, key: str) -> bool:
        pass

    @abstractmethod
    def exists(self, key: str) -> bool:
        pass

    @abstractmethod
    def clear(self) -> None:
        pass

    @abstractmethod
    def keys(self) -> list[str]:
        pass

    @abstractmethod
    def cleanup(self) -> int:
        pass

    @abstractmethod
    def close(self) -> None:
        pass

_SYNC_CACHE_REGISTRY: Dict[str, 'SyncFileCacher'] = {}
_ASYNC_CACHE_REGISTRY: Dict[str, 'AsyncFileCacher'] = {}

class FileCache(BaseCache):
    """Базовый класс для файлового кэша с автосохранением"""

    def __init__(self, filename: str):
        self.filename = filename
        self._cache: Dict[str, CacheValue] = {}
        self._dirty = False
        # self._lock = threading.Lock()
        self._load_cache()

    def _get_file_path(self) -> Path:
        """Получить путь к файлу кэша"""
        return Path(self.filename).with_suffix('.json')

    def _load_cache(self) -> None:
        """Загрузить кэш из файла (синхронная версия)"""
        file_path = self._get_file_path()
        if file_path.exists() and file_path.stat().st_size > 0:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for key, cache_data in data.items():
                        self._cache[key] = CacheValue.from_dict(cache_data)
            except (json.JSONDecodeError, IOError) as e:
                # Если файл поврежден, начинаем с пустого кэша
                print(f"Warning: Could not load cache from {file_path}: {e}")
                self._cache = {}
        # Если файла нет или он пустой - просто инициализируем пустой кэш

    async def _aload_cache(self) -> None:
        """Загрузить кэш из файла (асинхронная версия)"""
        file_path = self._get_file_path()
        if file_path.exists() and file_path.stat().st_size > 0:
            try:
                async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    data = json.loads(content)
                    for key, cache_data in data.items():
                        self._cache[key] = CacheValue.from_dict(cache_data)
            except (json.JSONDecodeError, IOError) as e:
                print(f"Warning: Could not load cache from {file_path}: {e}")
                self._cache = {}

    def _save_cache(self) -> None:
        """Сохранить кэш в файл (синхронная версия)"""
        if not self._dirty:
            return

        file_path = self._get_file_path()
        file_path.parent.mkdir(parents=True, exist_ok=True)

        self.cleanup()

        data = {}
        for key, cache_value in self._cache.items():
            data[key] = cache_value.to_dict()

        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self._dirty = False
        except IOError as e:
            print(f"Error saving cache to {file_path}: {e}")

    async def _asave_cache(self) -> None:
        """Сохранить кэш в файл (асинхронная версия)"""
        if not self._dirty:
            return

        file_path = self._get_file_path()
        file_path.parent.mkdir(parents=True, exist_ok=True)

        await self.cleanup()

        data = {}
        for key, cache_value in self._cache.items():
            data[key] = cache_value.to_dict()

        try:
            async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(data, indent=2))
            self._dirty = False
        except IOError as e:
            print(f"Error saving cache to {file_path}: {e}")

    def _mark_dirty(self) -> None:
        """Пометить кэш как измененный"""
        self._dirty = True

    def get(self, key: str) -> Optional[Any]:
        """Получить значение из кэша"""
        if key in self._cache:
            cache_value = self._cache[key]
            if not cache_value.is_expired():
                return cache_value.value
            else:
                # Удаляем просроченное значение
                del self._cache[key]
                self._mark_dirty()
        return None

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """Установить значение в кэше"""
        self._cache[key] = CacheValue(value, ttl)
        self._mark_dirty()

    def delete(self, key: str) -> bool:
        """Удалить значение из кэша"""
        if key in self._cache:
            del self._cache[key]
            self._mark_dirty()
            return True
        return False

    def exists(self, key: str) -> bool:
        """Проверить существование ключа в кэше"""
        if key in self._cache:
            cache_value = self._cache[key]
            if not cache_value.is_expired():
                return True
            else:
                del self._cache[key]
                self._mark_dirty()
        return False

    def clear(self) -> None:
        """Очистить весь кэш"""
        if self._cache:
            self._cache.clear()
            self._mark_dirty()

    def keys(self) -> list[str]:
        """Получить список всех действующих ключей"""
        return [key for key, cache_value in self._cache.items()
                if not cache_value.is_expired()]

    def cleanup(self) -> int:
        """Очистить просроченные значения"""
        expired_keys = []

        for key, cache_value in self._cache.items():
            if cache_value.is_expired():
                expired_keys.append(key)

        for key in expired_keys:
            del self._cache[key]

        if expired_keys:
            self._mark_dirty()

        return len(expired_keys)

    def get_with_metadata(self, key: str) -> Optional[Dict[str, Any]]:
        """Получить значение с метаданными"""
        if key in self._cache:
            cache_value = self._cache[key]
            if not cache_value.is_expired():
                return {
                    'value': cache_value.value,
                    'created_at': datetime.fromtimestamp(cache_value.created_at),
                    'ttl': cache_value.ttl,
                    'expires_at': (
                        datetime.fromtimestamp(cache_value.created_at + cache_value.ttl)
                        if cache_value.ttl else None
                    )
                }
            else:
                del self._cache[key]
                self._mark_dirty()
        return None

    def save(self) -> None:
        """Принудительно сохранить кэш в файл"""
        self._save_cache()

    async def asave(self) -> None:
        """Принудительно сохранить кэш в файл (асинхронно)"""
        await self._asave_cache()


class SyncFileCacher(FileCache):
    """Синхронная реализация файлового кэша"""

    def __new__(cls, filename: str, autosave: bool = True):
        """Singleton per filename - возвращаем существующий экземпляр для того же файла"""
        # Нормализуем путь к файлу
        file_path = Path(filename).with_suffix('.json').resolve()
        file_key = str(file_path)

        # Проверяем есть ли уже экземпляр
        if file_key in _SYNC_CACHE_REGISTRY:
            instance = _SYNC_CACHE_REGISTRY[file_key]
            # Обновляем autosave если передано другое значение
            if instance.autosave != autosave:
                instance.autosave = autosave
                # Перерегистрируем обработчик atexit если нужно
                if autosave:
                    atexit.register(instance.save)
            return instance

        # Создаем новый экземпляр
        instance = super().__new__(cls)
        _SYNC_CACHE_REGISTRY[file_key] = instance
        return instance

    def __init__(self, filename: str, autosave: bool = True):
        # Проверяем не инициализирован ли уже экземпляр
        if hasattr(self, '_initialized'):
            return

        super().__init__(filename)
        self.autosave = autosave
        self._atexit_registered = False

        if autosave:
            atexit.register(self.save)
            self._atexit_registered = True

        self._initialized = True

    def close(self) -> None:
        """Корректно завершить работу кэша"""
        # if self.autosave:
        self.save()

        # Убираем из реестра только если нет других ссылок
        file_path = Path(self.filename).with_suffix('.json').resolve()
        file_key = str(file_path)

        # Убираем обработчик atexit
        if self._atexit_registered:
            try:
                atexit.unregister(self.save)
                self._atexit_registered = False
            except (AttributeError, ValueError):
                pass


    @classmethod
    def clear_registry(cls):
        """Очистить реестр экземпляров (для тестов)"""
        _SYNC_CACHE_REGISTRY.clear()

    # def __init__(self, filename: str, autosave: bool = True):
    #     super().__init__(filename)
    #     self.autosave = autosave
    #
    #     if autosave:
    #         atexit.register(self.save)  # Регистрируем save(), а не close()
    #
    # def close(self) -> None:
    #     """Корректно завершить работу кэша"""
    #
    #     self.save()
    #
    #     # Убираем обработчик atexit
    #     if self.autosave:
    #         try:
    #             atexit.unregister(self.save)
    #         except (AttributeError, ValueError):
    #             pass
    #
    # def save(self) -> None:
    #     """Принудительно сохранить кэш в файл"""
    #     self._save_cache()  # _save_cache() сам проверяет _dirty внутри


class AsyncFileCacher(FileCache):
    """Асинхронная реализация файлового кэша"""

    def __new__(cls, filename: str, autosave: bool = True):
        """Singleton per filename - возвращаем существующий экземпляр для того же файла"""
        # Нормализуем путь к файлу
        file_path = Path(filename).with_suffix('.json').resolve()
        file_key = str(file_path)

        # Проверяем есть ли уже экземпляр
        if file_key in _ASYNC_CACHE_REGISTRY:
            instance = _ASYNC_CACHE_REGISTRY[file_key]
            # Обновляем autosave если передано другое значение
            if instance.autosave != autosave:
                instance.autosave = autosave
            return instance

        # Создаем новый экземпляр
        instance = super().__new__(cls)
        _ASYNC_CACHE_REGISTRY[file_key] = instance
        return instance

    def __init__(self, filename: str, autosave: bool = True):
        # Проверяем не инициализирован ли уже экземпляр
        if hasattr(self, '_initialized'):
            return

        super().__init__(filename)
        self.autosave = autosave
        self._initialized = False
        self._shutdown_registered = False
        self._instance_initialized = True

    async def initialize(self) -> None:
        """Инициализировать асинхронный кэш (загрузить из файла)"""
        if self._initialized:
            return

        await self._aload_cache()
        self._initialized = True

        if self.autosave and not self._shutdown_registered:
            await self._register_shutdown()
            self._shutdown_registered = True

    async def _register_shutdown(self) -> None:
        """Зарегистрировать обработчик завершения программы"""
        try:
            loop = asyncio.get_running_loop()

            def shutdown_handler():
                if self.autosave:
                    asyncio.create_task(self.asave())

            for sig in (signal.SIGTERM, signal.SIGINT):
                try:
                    loop.add_signal_handler(sig, shutdown_handler)
                except (NotImplementedError, RuntimeError):
                    pass
        except RuntimeError:
            pass

    async def close(self) -> None:
        """Корректно завершить работу кэша"""
        await self.asave()

    async def get(self, key: str) -> Optional[Any]:
        if not self._initialized:
            await self.initialize()
        return super().get(key)

    async def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        if not self._initialized:
            await self.initialize()
        super().set(key, value, ttl)

    async def delete(self, key: str) -> bool:
        if not self._initialized:
            await self.initialize()
        return super().delete(key)

    async def exists(self, key: str) -> bool:
        if not self._initialized:
            await self.initialize()
        return super().exists(key)

    async def cleanup(self) -> int:
        if not self._initialized:
            await self.initialize()
        return super().cleanup()

    @classmethod
    def clear_registry(cls):
        """Очистить реестр экземпляров (для тестов)"""
        _ASYNC_CACHE_REGISTRY.clear()
