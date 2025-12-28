from typing import Any, Type, TypeVar, Optional
from abc import ABC, abstractmethod
import json
from pathlib import Path
import atexit
from datetime import datetime
import time

from loguru import logger
from pydantic import BaseModel

T = TypeVar('T', bound=BaseModel)


class BaseCacher(ABC):

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(BaseCacher, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._initialized = True

    @abstractmethod
    def get(self, key: str, model_class: Type[T] = None, multiple: bool = True) -> Any | None:
        ...

    @abstractmethod
    def set(self, key: str, value: Any) -> None:
        ...
    @abstractmethod
    def save(self) -> None:
        ...


class FileCacher(BaseCacher):

    def __init__(self, file_path: str = "cache.json"):
        super().__init__()
        self.file_path = file_path
        # _cache хранит актуальные данные
        self._cache: dict[str, dict[str, Any]] = {}
        # _expire_times хранит время истечения
        self._expire_times: dict[str, float] = {}
        atexit.register(self.save)
        self.load()

    def get(self, key: str, model_class: Type[T] = None, multiple: bool = True) -> dict[str, T] | None | Any:
        if key in self._expire_times:
            expire_time = self._expire_times[key]
            if expire_time and time.time() > expire_time:
                self.delete(key)
                return None

        if key not in self._cache:
            return None

        cache_item = self._cache[key]
        data = cache_item['value']

        try:
            if isinstance(data, str):
                try:
                    data_dict = json.loads(data)
                except json.JSONDecodeError:
                    data_dict = data

            else:
                data_dict = data

            if model_class:
                to_return = {}
                if multiple:
                    for key_dict, value in data_dict.items():
                        to_return[key_dict] = model_class(**value)
                else:
                    to_return = model_class(**data_dict)
                return to_return

            return data_dict

        except Exception as e:
            logger.error(f"Ошибка восстановления модели: {e}")
            return None


    def set(self, key: str, value, expire: int = 60 * 60 * 24):
        expire_timestamp = time.time() + expire if expire else None
        self._expire_times[key] = expire_timestamp

        if isinstance(value, BaseModel):
            serialized = value.model_dump_json()
        elif isinstance(value, dict):
            serialized = self._serialize_dict(value)
        else:
            serialized = value

        self._cache[key] = {
            'value': serialized,
            'expire': expire_timestamp
        }

    def _serialize_dict(self, data: dict) -> dict:
        result = {}
        for key, value in data.items():
            if isinstance(value, BaseModel):
                result[key] = value.model_dump()
            elif isinstance(value, dict):
                result[key] = self._serialize_dict(value)
            elif isinstance(value, list):
                result[key] = [
                    item.model_dump() if isinstance(item, BaseModel)
                    else self._serialize_dict(item) if isinstance(item, dict)
                    else item
                    for item in value
                ]
            else:
                result[key] = value
        return result


    def save(self):
        data_to_save = {
            'cache': self._cache,
            'expire_times': self._expire_times
        }
        with open(self.file_path, 'w', encoding='utf-8') as f:
            json.dump(data_to_save, f, ensure_ascii=False)

    def load(self):
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self._cache = data.get('cache', {})
                self._expire_times = data.get('expire_times', {})
        except FileNotFoundError:
            self._cache = {}
            self._expire_times = {}

        self.cleanup()

    def delete(self, key: str):
        self._cache.pop(key, None)
        self._expire_times.pop(key, None)

    def cleanup(self):
        current_time = time.time()
        expired_keys = [
            key for key, expire_time in self._expire_times.items()
            if expire_time and expire_time < current_time
        ]
        for key in expired_keys:
            self.delete(key)

    def get_valid_keys(self) -> list[str]:
        self.cleanup()
        return list(self._cache.keys())

    def compact(self):
        self.cleanup()
        self.save()