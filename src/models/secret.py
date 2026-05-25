# -*- coding: utf-8 -*-
"""Модель секретной записи (пароль, токен и т.д.)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SecretEntry:
    """Секретная запись.

    Attributes:
        id: Уникальный идентификатор.
        name: Название (например: "GitHub token").
        username: Логин / email.
        encrypted_password: Зашифрованный пароль/токен.
        url: URL сервиса (опционально).
        notes: Заметки (опционально).
        category: Категория (например: "Серверы", "API").
        created_at: Дата создания.
        updated_at: Дата обновления.
    """

    id: int | None = None
    name: str = ""
    username: str = ""
    encrypted_password: str = ""
    url: str = ""
    notes: str = ""
    category: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def display_name(self) -> str:
        """Отображаемое имя."""
        if self.name:
            return self.name
        return self.username or "(без имени)"

    def to_dict(self) -> dict:
        """Сериализация в словарь."""
        return {
            "id": self.id,
            "name": self.name,
            "username": self.username,
            "encrypted_password": self.encrypted_password,
            "url": self.url,
            "notes": self.notes,
            "category": self.category,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SecretEntry:
        """Десериализация из словаря."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
