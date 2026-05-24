# -*- coding: utf-8 -*-
"""Модель сохранённой команды."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SavedCommand:
    """Сохранённая команда для быстрого выполнения.

    Attributes:
        id: Уникальный идентификатор.
        name: Отображаемое имя команды.
        command_text: Текст команды для выполнения.
        connection_id: ID привязанного подключения (None = глобальная).
        category: Категория/группа команды.
        sort_order: Порядок сортировки в списке.
    """

    id: int | None = None
    name: str = ""
    command_text: str = ""
    connection_id: int | None = None
    category: str = ""
    sort_order: int = 0

    def to_dict(self) -> dict:
        """Сериализация в словарь для БД."""
        return {
            "id": self.id,
            "name": self.name,
            "command_text": self.command_text,
            "connection_id": self.connection_id,
            "category": self.category,
            "sort_order": self.sort_order,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SavedCommand:
        """Десериализация из словаря БД."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
