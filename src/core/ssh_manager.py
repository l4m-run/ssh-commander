# -*- coding: utf-8 -*-
"""Управление SSH-сессиями.

Обёртка над paramiko для установки SSH-соединений с PTY.
Каждая сессия работает в отдельном QThread, данные передаются
через Qt Signals для thread-safety.
"""

from __future__ import annotations

import logging
import socket
from typing import TYPE_CHECKING

import paramiko
from PySide6.QtCore import QThread, Signal

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class SSHSession(QThread):
    """SSH-сессия в отдельном потоке.

    Signals:
        data_received: Данные от удалённого сервера (bytes).
        connected: Соединение установлено.
        disconnected: Соединение закрыто.
        error_occurred: Ошибка соединения (str).
    """

    data_received = Signal(bytes)
    connected = Signal()
    disconnected = Signal()
    error_occurred = Signal(str)

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str = "",
        key_path: str = "",
    ) -> None:
        super().__init__()
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._key_path = key_path

        self._client: paramiko.SSHClient | None = None
        self._channel: paramiko.Channel | None = None
        self._running = False

    @property
    def is_connected(self) -> bool:
        """Проверка активности соединения."""
        return (
            self._channel is not None
            and not self._channel.closed
            and self._running
        )

    def run(self) -> None:
        """Основной цикл потока: подключение и чтение данных."""
        try:
            self._connect()
            self.connected.emit()
            self._read_loop()
        except paramiko.AuthenticationException:
            self.error_occurred.emit("Ошибка аутентификации: неверный логин или пароль")
        except paramiko.SSHException as e:
            self.error_occurred.emit(f"Ошибка SSH: {e}")
        except socket.timeout:
            self.error_occurred.emit(
                f"Таймаут подключения к {self._host}:{self._port}"
            )
        except socket.gaierror:
            self.error_occurred.emit(
                f"Не удалось разрешить хост: {self._host}"
            )
        except OSError as e:
            self.error_occurred.emit(f"Ошибка сети: {e}")
        except Exception as e:
            self.error_occurred.emit(f"Неизвестная ошибка: {e}")
        finally:
            self._cleanup()
            self.disconnected.emit()

    def _connect(self) -> None:
        """Установить SSH-соединение с PTY."""
        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs: dict = {
            "hostname": self._host,
            "port": self._port,
            "username": self._username,
            "timeout": 10,
            "allow_agent": False,
            "look_for_keys": False,
        }

        # Аутентификация по ключу или паролю
        if self._key_path:
            connect_kwargs["key_filename"] = self._key_path
        elif self._password:
            connect_kwargs["password"] = self._password

        self._client.connect(**connect_kwargs)

        # Открытие интерактивной сессии с PTY
        self._channel = self._client.invoke_shell(
            term="xterm-256color",
            width=80,
            height=24,
        )
        self._channel.settimeout(0.1)
        self._running = True
        logger.info("Подключено к %s:%d", self._host, self._port)

    def _read_loop(self) -> None:
        """Цикл чтения данных из SSH-канала."""
        while self._running and self._channel and not self._channel.closed:
            try:
                if self._channel.recv_ready():
                    data = self._channel.recv(65536)
                    if data:
                        self.data_received.emit(data)
                    else:
                        # Канал закрыт удалённой стороной
                        break
                elif self._channel.exit_status_ready():
                    break
            except socket.timeout:
                continue
            except OSError:
                break

    def write(self, data: bytes) -> None:
        """Отправить данные в SSH-канал.

        Args:
            data: Байты для отправки (ввод с клавиатуры).
        """
        if self._channel and not self._channel.closed:
            try:
                self._channel.sendall(data)
            except OSError as e:
                logger.error("Ошибка отправки данных: %s", e)

    def resize_pty(self, cols: int, rows: int) -> None:
        """Изменить размер PTY.

        Args:
            cols: Количество колонок.
            rows: Количество строк.
        """
        if self._channel and not self._channel.closed:
            try:
                self._channel.resize_pty(width=cols, height=rows)
            except OSError:
                pass

    def disconnect(self) -> None:
        """Отключить сессию."""
        self._running = False

    def _cleanup(self) -> None:
        """Закрыть соединение и освободить ресурсы."""
        self._running = False
        if self._channel:
            try:
                self._channel.close()
            except Exception:
                pass
            self._channel = None
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        logger.info("Отключено от %s:%d", self._host, self._port)

    def execute_command(self, command: str) -> None:
        """Выполнить команду в текущей сессии.

        Отправляет команду как ввод с клавиатуры (через канал).

        Args:
            command: Текст команды.
        """
        if not command.endswith("\n"):
            command += "\n"
        self.write(command.encode("utf-8"))
