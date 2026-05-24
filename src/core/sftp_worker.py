# -*- coding: utf-8 -*-
"""Фоновый worker для SFTP-операций.

Выполняет загрузку/выгрузку файлов в отдельном потоке
с отправкой прогресса через Qt Signals.
"""

from __future__ import annotations

import logging
import os
import stat
import tempfile
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING

import paramiko
from PySide6.QtCore import QObject, QThread, Signal

from src.core.crypto import crypto

if TYPE_CHECKING:
    from src.models.connection import Connection

logger = logging.getLogger(__name__)


class TransferDirection(Enum):
    """Направление передачи файла."""
    UPLOAD = auto()      # Локальная -> сервер
    DOWNLOAD = auto()    # Сервер -> локальная
    SERVER_TO_SERVER = auto()  # Сервер -> сервер (транзит через локальную)


@dataclass
class FileEntry:
    """Запись файла/директории для отображения в панели.

    Attributes:
        name: Имя файла.
        path: Полный путь.
        is_dir: Является ли директорией.
        size: Размер в байтах.
        modified: Время последнего изменения (timestamp).
        permissions: Строка прав доступа (например, "rwxr-xr-x").
    """
    name: str = ""
    path: str = ""
    is_dir: bool = False
    size: int = 0
    modified: float = 0.0
    permissions: str = ""


@dataclass
class TransferTask:
    """Задача на передачу файла.

    Attributes:
        id: Уникальный идентификатор задачи.
        source_path: Путь к исходному файлу.
        dest_path: Путь назначения.
        direction: Направление передачи.
        source_connection: Подключение-источник (None = локальная).
        dest_connection: Подключение-назначение (None = локальная).
        total_bytes: Общий размер файла.
        transferred_bytes: Переданные байты.
        status: Статус ("pending", "running", "done", "error").
        error: Текст ошибки.
    """
    id: int = 0
    source_path: str = ""
    dest_path: str = ""
    direction: TransferDirection = TransferDirection.DOWNLOAD
    source_connection: Connection | None = None
    dest_connection: Connection | None = None
    total_bytes: int = 0
    transferred_bytes: int = 0
    status: str = "pending"
    error: str = ""


class SFTPBrowser:
    """Навигация по удалённой ФС через SFTP.

    Управляет SSH-соединением и SFTP-клиентом для одной панели.
    """

    def __init__(self) -> None:
        self._client: paramiko.SSHClient | None = None
        self._sftp: paramiko.SFTPClient | None = None
        self._current_path = "/"
        self._connection: Connection | None = None

    @property
    def is_connected(self) -> bool:
        """Проверка активности SFTP-соединения."""
        return self._sftp is not None

    @property
    def current_path(self) -> str:
        """Текущая директория."""
        return self._current_path

    @property
    def connection(self) -> Connection | None:
        """Текущее подключение."""
        return self._connection

    def connect(self, connection: Connection, password: str = "") -> None:
        """Подключиться к серверу.

        Args:
            connection: Объект подключения.
            password: Расшифрованный пароль.
        """
        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs: dict = {
            "hostname": connection.host,
            "port": connection.port,
            "username": connection.username,
            "timeout": 10,
            "allow_agent": False,
            "look_for_keys": False,
        }

        if connection.ssh_key_path:
            connect_kwargs["key_filename"] = connection.ssh_key_path
        elif password:
            connect_kwargs["password"] = password

        self._client.connect(**connect_kwargs)
        self._sftp = self._client.open_sftp()
        self._connection = connection

        # Перейти в домашнюю директорию
        try:
            self._current_path = self._sftp.normalize(".")
        except Exception:
            self._current_path = "/"

        logger.info(
            "SFTP подключено к %s:%d", connection.host, connection.port
        )

    def disconnect(self) -> None:
        """Закрыть SFTP-соединение."""
        if self._sftp:
            try:
                self._sftp.close()
            except Exception:
                pass
            self._sftp = None
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        self._connection = None

    def list_dir(self, path: str | None = None) -> list[FileEntry]:
        """Получить список файлов в директории.

        Args:
            path: Путь к директории (None = текущая).

        Returns:
            Список файлов и директорий.
        """
        if self._sftp is None:
            return []

        target = path or self._current_path
        entries: list[FileEntry] = []

        try:
            for attr in self._sftp.listdir_attr(target):
                is_dir = stat.S_ISDIR(attr.st_mode) if attr.st_mode else False
                # Формируем строку прав доступа
                perms = ""
                if attr.st_mode:
                    perms = stat.filemode(attr.st_mode)

                full_path = f"{target.rstrip('/')}/{attr.filename}"
                entries.append(FileEntry(
                    name=attr.filename,
                    path=full_path,
                    is_dir=is_dir,
                    size=attr.st_size or 0,
                    modified=attr.st_mtime or 0.0,
                    permissions=perms,
                ))
        except PermissionError:
            logger.warning("Нет доступа к %s", target)
        except Exception as e:
            logger.error("Ошибка чтения %s: %s", target, e)

        # Сортировка: директории сверху, потом по имени
        entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        return entries

    def change_dir(self, path: str) -> str:
        """Перейти в директорию.

        Args:
            path: Путь (абсолютный или относительный).

        Returns:
            Новый текущий путь.
        """
        if self._sftp is None:
            return self._current_path

        try:
            if not path.startswith("/"):
                path = f"{self._current_path.rstrip('/')}/{path}"
            # Нормализуем путь
            normalized = self._sftp.normalize(path)
            # Проверяем, что это директория
            st = self._sftp.stat(normalized)
            if st.st_mode and stat.S_ISDIR(st.st_mode):
                self._current_path = normalized
        except Exception as e:
            logger.error("Ошибка перехода в %s: %s", path, e)

        return self._current_path

    def go_up(self) -> str:
        """Перейти в родительскую директорию."""
        parent = str(Path(self._current_path).parent)
        return self.change_dir(parent)

    def mkdir(self, name: str) -> bool:
        """Создать директорию."""
        if self._sftp is None:
            return False
        try:
            full_path = f"{self._current_path.rstrip('/')}/{name}"
            self._sftp.mkdir(full_path)
            return True
        except Exception as e:
            logger.error("Ошибка создания директории: %s", e)
            return False

    def delete(self, path: str, is_dir: bool = False) -> bool:
        """Удалить файл или пустую директорию."""
        if self._sftp is None:
            return False
        try:
            if is_dir:
                self._sftp.rmdir(path)
            else:
                self._sftp.remove(path)
            return True
        except Exception as e:
            logger.error("Ошибка удаления %s: %s", path, e)
            return False


class TransferWorker(QThread):
    """Фоновый поток для передачи файлов.

    Signals:
        progress: Прогресс передачи (task_id, transferred, total).
        task_completed: Задача завершена (task_id).
        task_error: Ошибка задачи (task_id, error_message).
        all_completed: Все задачи завершены.
    """

    progress = Signal(int, int, int)
    task_completed = Signal(int)
    task_error = Signal(int, str)
    all_completed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._tasks: list[TransferTask] = []
        self._running = False
        self._task_counter = 0

    def add_task(self, task: TransferTask) -> int:
        """Добавить задачу в очередь.

        Returns:
            ID задачи.
        """
        self._task_counter += 1
        task.id = self._task_counter
        self._tasks.append(task)
        return task.id

    def run(self) -> None:
        """Выполнение задач из очереди."""
        self._running = True

        while self._running and self._tasks:
            task = self._tasks.pop(0)
            task.status = "running"

            try:
                if task.direction == TransferDirection.DOWNLOAD:
                    self._do_download(task)
                elif task.direction == TransferDirection.UPLOAD:
                    self._do_upload(task)
                elif task.direction == TransferDirection.SERVER_TO_SERVER:
                    self._do_server_to_server(task)

                task.status = "done"
                self.task_completed.emit(task.id)
            except Exception as e:
                task.status = "error"
                task.error = str(e)
                self.task_error.emit(task.id, str(e))
                logger.error("Ошибка передачи: %s", e)

        self._running = False
        self.all_completed.emit()

    def stop(self) -> None:
        """Остановить worker."""
        self._running = False

    def _make_progress_callback(self, task: TransferTask):
        """Создать callback для отслеживания прогресса."""
        def callback(transferred: int, total: int) -> None:
            task.transferred_bytes = transferred
            task.total_bytes = total
            self.progress.emit(task.id, transferred, total)
        return callback

    def _do_download(self, task: TransferTask) -> None:
        """Скачать файл с сервера на локальную машину."""
        conn = task.source_connection
        if conn is None:
            raise ValueError("Не указано подключение-источник")

        password = ""
        if conn.encrypted_password:
            password = crypto.decrypt(conn.encrypted_password)

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs: dict = {
            "hostname": conn.host,
            "port": conn.port,
            "username": conn.username,
            "timeout": 10,
            "allow_agent": False,
            "look_for_keys": False,
        }
        if conn.ssh_key_path:
            connect_kwargs["key_filename"] = conn.ssh_key_path
        elif password:
            connect_kwargs["password"] = password

        try:
            client.connect(**connect_kwargs)
            sftp = client.open_sftp()
            sftp.get(
                task.source_path,
                task.dest_path,
                callback=self._make_progress_callback(task),
            )
            sftp.close()
        finally:
            client.close()

    def _do_upload(self, task: TransferTask) -> None:
        """Загрузить файл с локальной машины на сервер."""
        conn = task.dest_connection
        if conn is None:
            raise ValueError("Не указано подключение назначения")

        password = ""
        if conn.encrypted_password:
            password = crypto.decrypt(conn.encrypted_password)

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs: dict = {
            "hostname": conn.host,
            "port": conn.port,
            "username": conn.username,
            "timeout": 10,
            "allow_agent": False,
            "look_for_keys": False,
        }
        if conn.ssh_key_path:
            connect_kwargs["key_filename"] = conn.ssh_key_path
        elif password:
            connect_kwargs["password"] = password

        try:
            client.connect(**connect_kwargs)
            sftp = client.open_sftp()
            sftp.put(
                task.source_path,
                task.dest_path,
                callback=self._make_progress_callback(task),
            )
            sftp.close()
        finally:
            client.close()

    def _do_server_to_server(self, task: TransferTask) -> None:
        """Копирование между серверами через локальную машину."""
        # Скачиваем во временный файл
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name

        try:
            # Фаза 1: скачиваем с источника
            download_task = TransferTask(
                id=task.id,
                source_path=task.source_path,
                dest_path=tmp_path,
                source_connection=task.source_connection,
            )
            self._do_download(download_task)

            # Фаза 2: загружаем на назначение
            upload_task = TransferTask(
                id=task.id,
                source_path=tmp_path,
                dest_path=task.dest_path,
                dest_connection=task.dest_connection,
            )
            self._do_upload(upload_task)
        finally:
            # Удаляем временный файл
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
