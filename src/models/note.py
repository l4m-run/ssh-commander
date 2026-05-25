# -*- coding: utf-8 -*-
"""Модель заметки к серверу."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ServerNote:
    """Заметка, привязанная к SSH-подключению.

    Attributes:
        id: Уникальный идентификатор.
        connection_id: ID SSH-подключения (FK).
        title: Заголовок заметки.
        content: Содержимое (Markdown).
        created_at: Дата создания.
        updated_at: Дата обновления.
    """

    id: int | None = None
    connection_id: int | None = None
    title: str = ""
    content: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        """Сериализация в словарь."""
        return {
            "id": self.id,
            "connection_id": self.connection_id,
            "title": self.title,
            "content": self.content,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ServerNote:
        """Десериализация из словаря."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
