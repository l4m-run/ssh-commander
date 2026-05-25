# -*- coding: utf-8 -*-
"""Модель сохранённого подключения к базе данных."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DbConnectionConfig:
    """Конфигурация подключения к базе данных.

    Attributes:
        id: Уникальный идентификатор.
        name: Отображаемое имя.
        ssh_connection_id: ID SSH-подключения (FK).
        db_type: Тип СУБД (postgresql, mysql, sqlite).
        db_host: Хост БД на сервере.
        db_port: Порт БД.
        db_user: Пользователь БД.
        encrypted_db_password: Зашифрованный пароль БД.
        database_name: Имя базы данных.
    """

    id: int | None = None
    name: str = ""
    ssh_connection_id: int | None = None
    db_type: str = "postgresql"
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = ""
    encrypted_db_password: str = ""
    database_name: str = ""

    @property
    def display_name(self) -> str:
        """Отображаемое имя."""
        if self.name:
            return self.name
        return f"{self.db_type}://{self.db_user}@{self.db_host}:{self.db_port}/{self.database_name}"

    def to_dict(self) -> dict:
        """Сериализация в словарь."""
        return {
            "id": self.id,
            "name": self.name,
            "ssh_connection_id": self.ssh_connection_id,
            "db_type": self.db_type,
            "db_host": self.db_host,
            "db_port": self.db_port,
            "db_user": self.db_user,
            "encrypted_db_password": self.encrypted_db_password,
            "database_name": self.database_name,
        }

    @classmethod
    def from_dict(cls, data: dict) -> DbConnectionConfig:
        """Десериализация из словаря."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
