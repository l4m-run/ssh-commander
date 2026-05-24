# -*- coding: utf-8 -*-
"""Конфигурация приложения.

Определяет пути к файлам данных и настройки по умолчанию.
Следует XDG Base Directory Specification:
- ~/.config/ssh-commander/ - конфигурация
- ~/.local/share/ssh-commander/ - данные (БД)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Имя приложения для XDG-путей
APP_NAME = "ssh-commander"


def _get_config_dir() -> Path:
    """Получить путь к директории конфигурации (XDG_CONFIG_HOME)."""
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / APP_NAME


def _get_data_dir() -> Path:
    """Получить путь к директории данных (XDG_DATA_HOME)."""
    xdg = os.environ.get("XDG_DATA_HOME", "")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / APP_NAME


@dataclass
class AppConfig:
    """Конфигурация приложения.

    Attributes:
        config_dir: Путь к директории конфигурации.
        data_dir: Путь к директории данных.
        db_path: Путь к файлу БД SQLite.
        salt_path: Путь к файлу с солью для шифрования.
        terminal_font_family: Шрифт терминала.
        terminal_font_size: Размер шрифта терминала.
        terminal_scrollback: Размер буфера прокрутки (строк).
        default_port: Порт SSH по умолчанию.
    """

    config_dir: Path = field(default_factory=_get_config_dir)
    data_dir: Path = field(default_factory=_get_data_dir)
    db_path: Path = field(default=None)  # type: ignore[assignment]
    salt_path: Path = field(default=None)  # type: ignore[assignment]

    # Настройки терминала
    terminal_font_family: str = "Ubuntu Mono"
    terminal_font_size: int = 13
    terminal_scrollback: int = 5000

    # SSH
    default_port: int = 22

    def __post_init__(self) -> None:
        """Инициализация вычисляемых путей и создание директорий."""
        if self.db_path is None:
            self.db_path = self.data_dir / "connections.db"
        if self.salt_path is None:
            self.salt_path = self.config_dir / "salt"

        # Создаём директории, если не существуют
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)


# Глобальный экземпляр конфигурации
config = AppConfig()
