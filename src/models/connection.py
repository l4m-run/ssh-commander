# -*- coding: utf-8 -*-
"""Модель SSH-подключения."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Connection:
    """SSH-подключение.

    Attributes:
        id: Уникальный идентификатор.
        name: Отображаемое имя подключения.
        host: Хост или IP-адрес.
        port: Порт SSH (по умолчанию 22).
        username: Имя пользователя.
        encrypted_password: Зашифрованный пароль (Fernet).
        ssh_key_path: Путь к SSH-ключу (опционально).
        group_name: Группа/категория подключения.
        created_at: Дата создания.
        last_used: Дата последнего использования.
    """

    id: int | None = None
    name: str = ""
    host: str = ""
    port: int = 22
    username: str = ""
    encrypted_password: str = ""
    ssh_key_path: str = ""
    group_name: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_used: str = ""

    @property
    def display_name(self) -> str:
        """Отображаемое имя для sidebar."""
        if self.name:
            return self.name
        return f"{self.username}@{self.host}:{self.port}"

    def to_dict(self) -> dict:
        """Сериализация в словарь для БД."""
        return {
            "id": self.id,
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "encrypted_password": self.encrypted_password,
            "ssh_key_path": self.ssh_key_path,
            "group_name": self.group_name,
            "created_at": self.created_at,
            "last_used": self.last_used,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Connection:
        """Десериализация из словаря БД."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
