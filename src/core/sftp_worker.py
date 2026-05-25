# -*- coding: utf-8 -*-
"""Фоновый worker для SFTP-операций.

Выполняет загрузку/выгрузку файлов в отдельном потоке
с отправкой прогресса через Qt Signals.

Поддерживает:
- Докачку файлов при разрыве соединения (resume)
- Автоматические повторные попытки с экспоненциальным backoff
- Прямой SCP между серверами (через sshpass) с fallback на потоковый транзит
"""

from __future__ import annotations

import logging
import os
import socket
import stat
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING

import paramiko
from PySide6.QtCore import QThread, Signal

from src.core.crypto import crypto

if TYPE_CHECKING:
    from src.models.connection import Connection

logger = logging.getLogger(__name__)

# Размер блока для передачи (64KB)
CHUNK_SIZE = 65536


class TransferDirection(Enum):
    """Направление передачи файла."""
    UPLOAD = auto()      # Локальная -> сервер
    DOWNLOAD = auto()    # Сервер -> локальная
    SERVER_TO_SERVER = auto()  # Сервер -> сервер


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
        resume: Флаг докачки (продолжить с места обрыва).
        retries: Счётчик выполненных попыток.
        max_retries: Максимальное количество повторных попыток.
        direct_scp_failed: Прямой SCP уже пробовали и не получилось.
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
    resume: bool = False
    retries: int = 0
    max_retries: int = 3
    direct_scp_failed: bool = False


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
        """Выполнение задач из очереди с retry-логикой."""
        self._running = True

        while self._running and self._tasks:
            task = self._tasks.pop(0)
            task.status = "running"

            last_error: Exception | None = None

            for attempt in range(task.max_retries + 1):
                try:
                    if task.direction == TransferDirection.DOWNLOAD:
                        self._do_download(task)
                    elif task.direction == TransferDirection.UPLOAD:
                        self._do_upload(task)
                    elif task.direction == TransferDirection.SERVER_TO_SERVER:
                        self._do_server_to_server(task)

                    task.status = "done"
                    self.task_completed.emit(task.id)
                    last_error = None
                    break
                except (
                    paramiko.SSHException,
                    socket.error,
                    socket.timeout,
                    EOFError,
                ) as e:
                    # Ошибки соединения - можно повторить
                    last_error = e
                    task.retries += 1
                    task.resume = True
                    if attempt < task.max_retries:
                        delay = min(2 ** attempt, 10)
                        logger.warning(
                            "Попытка %d/%d для %s: %s (повтор через %dс)",
                            attempt + 1, task.max_retries + 1,
                            task.source_path, e, delay,
                        )
                        time.sleep(delay)
                except Exception as e:
                    # Прочие ошибки - не повторяем
                    last_error = e
                    break

            if last_error is not None:
                task.status = "error"
                task.error = str(last_error)
                self.task_error.emit(task.id, str(last_error))
                logger.error("Ошибка передачи %s: %s", task.source_path, last_error)

        self._running = False
        self.all_completed.emit()

    def stop(self) -> None:
        """Остановить worker."""
        self._running = False

    # --- Вспомогательные методы подключения ---

    @staticmethod
    def _get_password(conn: Connection) -> str:
        """Расшифровать пароль подключения."""
        if conn.encrypted_password:
            return crypto.decrypt(conn.encrypted_password)
        return ""

    @staticmethod
    def _build_connect_kwargs(conn: Connection, password: str) -> dict:
        """Сформировать параметры подключения SSH."""
        kwargs: dict = {
            "hostname": conn.host,
            "port": conn.port,
            "username": conn.username,
            "timeout": 10,
            "allow_agent": False,
            "look_for_keys": False,
        }
        if conn.ssh_key_path:
            kwargs["key_filename"] = conn.ssh_key_path
        elif password:
            kwargs["password"] = password
        return kwargs

    def _connect_ssh(self, conn: Connection) -> paramiko.SSHClient:
        """Создать SSH-соединение.

        Args:
            conn: Объект подключения.

        Returns:
            SSH-клиент.
        """
        password = self._get_password(conn)
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(**self._build_connect_kwargs(conn, password))
        return client

    def _connect_sftp(
        self, conn: Connection,
    ) -> tuple[paramiko.SSHClient, paramiko.SFTPClient]:
        """Создать SSH + SFTP соединение.

        Args:
            conn: Объект подключения.

        Returns:
            Кортеж (SSH-клиент, SFTP-клиент).
        """
        client = self._connect_ssh(conn)
        sftp = client.open_sftp()
        return client, sftp

    @staticmethod
    def _ensure_remote_dir(sftp: paramiko.SFTPClient, file_path: str) -> None:
        """Создать родительские директории на сервере при необходимости.

        Args:
            sftp: SFTP-клиент.
            file_path: Путь к файлу (создаются родительские директории).
        """
        dir_path = os.path.dirname(file_path)
        if not dir_path or dir_path == "/":
            return

        # Собираем цепочку несуществующих директорий
        dirs_to_create: list[str] = []
        current = dir_path
        while current and current != "/":
            try:
                sftp.stat(current)
                break  # Директория существует
            except FileNotFoundError:
                dirs_to_create.append(current)
                current = os.path.dirname(current)

        # Создаём от корня к листу
        for d in reversed(dirs_to_create):
            try:
                sftp.mkdir(d)
            except IOError:
                pass  # Может уже существовать (race condition)

    # --- Передача файлов ---

    def _do_download(self, task: TransferTask) -> None:
        """Скачать файл с сервера на локальную машину.

        Поддерживает докачку: если локальный файл существует
        и меньше удалённого, продолжает с места обрыва.
        """
        conn = task.source_connection
        if conn is None:
            raise ValueError("Не указано подключение-источник")

        client, sftp = self._connect_sftp(conn)
        try:
            # Размер удалённого файла
            remote_stat = sftp.stat(task.source_path)
            remote_size = remote_stat.st_size or 0
            task.total_bytes = remote_size

            # Создаём локальные директории при необходимости
            os.makedirs(os.path.dirname(task.dest_path), exist_ok=True)

            # Проверяем частично скачанный файл
            local_size = 0
            if task.resume and os.path.exists(task.dest_path):
                local_size = os.path.getsize(task.dest_path)
                if local_size >= remote_size:
                    # Файл уже полностью скачан
                    self.progress.emit(task.id, remote_size, remote_size)
                    return

            # Блочное чтение с поддержкой resume
            with sftp.open(task.source_path, "rb") as remote_f:
                remote_f.prefetch(remote_size)

                if local_size > 0:
                    remote_f.seek(local_size)

                mode = "ab" if local_size > 0 else "wb"
                with open(task.dest_path, mode) as local_f:
                    transferred = local_size
                    while True:
                        chunk = remote_f.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        local_f.write(chunk)
                        transferred += len(chunk)
                        task.transferred_bytes = transferred
                        self.progress.emit(task.id, transferred, remote_size)

            # Проверка целостности
            final_size = os.path.getsize(task.dest_path)
            if final_size != remote_size:
                raise IOError(
                    f"Размер файла не совпадает: {final_size} != {remote_size}"
                )
        finally:
            sftp.close()
            client.close()

    def _do_upload(self, task: TransferTask) -> None:
        """Загрузить файл с локальной машины на сервер.

        Поддерживает докачку: если файл на сервере существует
        и меньше локального, продолжает с места обрыва.
        """
        conn = task.dest_connection
        if conn is None:
            raise ValueError("Не указано подключение назначения")

        client, sftp = self._connect_sftp(conn)
        try:
            local_size = os.path.getsize(task.source_path)
            task.total_bytes = local_size

            # Создаём директории на сервере при необходимости
            self._ensure_remote_dir(sftp, task.dest_path)

            # Проверяем частично загруженный файл на сервере
            remote_size = 0
            if task.resume:
                try:
                    remote_stat = sftp.stat(task.dest_path)
                    remote_size = remote_stat.st_size or 0
                    if remote_size >= local_size:
                        # Файл уже полностью загружен
                        self.progress.emit(task.id, local_size, local_size)
                        return
                except FileNotFoundError:
                    remote_size = 0

            # Блочная запись с поддержкой resume
            with open(task.source_path, "rb") as local_f:
                if remote_size > 0:
                    local_f.seek(remote_size)
                    # Открываем существующий файл для дозаписи
                    try:
                        remote_f = sftp.open(task.dest_path, "r+b")
                        remote_f.seek(remote_size)
                    except IOError:
                        # Сервер не поддерживает seek - перезаписываем
                        logger.warning(
                            "Resume upload не поддерживается, перезапись: %s",
                            task.dest_path,
                        )
                        local_f.seek(0)
                        remote_size = 0
                        remote_f = sftp.open(task.dest_path, "wb")
                else:
                    remote_f = sftp.open(task.dest_path, "wb")

                try:
                    transferred = remote_size
                    while True:
                        chunk = local_f.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        remote_f.write(chunk)
                        transferred += len(chunk)
                        task.transferred_bytes = transferred
                        self.progress.emit(task.id, transferred, local_size)
                finally:
                    remote_f.close()
        finally:
            sftp.close()
            client.close()

    def _do_server_to_server(self, task: TransferTask) -> None:
        """Передача файла между серверами.

        Стратегия:
        1. Попытка прямого SCP через sshpass на сервере-источнике
        2. Fallback: потоковый транзит через память (без tmp-файла)
        """
        if not task.direct_scp_failed:
            try:
                if self._try_direct_scp(task):
                    logger.info(
                        "Прямой SCP: %s -> %s", task.source_path, task.dest_path,
                    )
                    return
            except Exception as e:
                logger.warning("Ошибка прямого SCP: %s", e)
            task.direct_scp_failed = True

        logger.info(
            "Потоковый транзит: %s -> %s", task.source_path, task.dest_path,
        )
        self._do_streaming_transfer(task)

    def _try_direct_scp(self, task: TransferTask) -> bool:
        """Попытка прямой передачи через scp на сервере-источнике.

        Выполняет на сервере A:
            SSHPASS='pass_B' sshpass -e scp file user@B:path

        Args:
            task: Задача передачи.

        Returns:
            True при успешной передаче, False если невозможно.
        """
        src_conn = task.source_connection
        dst_conn = task.dest_connection
        if src_conn is None or dst_conn is None:
            return False

        # Расшифровываем пароль назначения
        dst_password = self._get_password(dst_conn)
        if not dst_password:
            logger.info("Прямой SCP: нет пароля назначения, пропуск")
            return False

        client = self._connect_ssh(src_conn)
        try:
            # Проверяем наличие sshpass на сервере-источнике
            _, stdout, stderr = client.exec_command("which sshpass", timeout=5)
            exit_code = stdout.channel.recv_exit_status()
            if exit_code != 0:
                logger.info("sshpass не установлен на %s", src_conn.host)
                return False

            # Экранируем пароль для shell (одинарные кавычки)
            escaped_pass = dst_password.replace("'", "'\\''")

            # Создаём директорию назначения на сервере B
            dest_dir = os.path.dirname(task.dest_path)
            if dest_dir and dest_dir != "/":
                mkdir_cmd = (
                    f"SSHPASS='{escaped_pass}' sshpass -e ssh "
                    f"-o StrictHostKeyChecking=no "
                    f"-o ConnectTimeout=10 "
                    f"-p {dst_conn.port} "
                    f"'{dst_conn.username}@{dst_conn.host}' "
                    f"'mkdir -p \"{dest_dir}\"'"
                )
                client.exec_command(mkdir_cmd, timeout=15)
                # Ждём завершения mkdir
                time.sleep(0.5)

            # Формируем команду scp
            # SSHPASS в env (не в аргументах) - безопаснее
            scp_cmd = (
                f"SSHPASS='{escaped_pass}' sshpass -e scp "
                f"-o StrictHostKeyChecking=no "
                f"-o ConnectTimeout=10 "
                f"-P {dst_conn.port} "
                f"'{task.source_path}' "
                f"'{dst_conn.username}@{dst_conn.host}:{task.dest_path}'"
            )

            logger.info(
                "Прямой SCP: %s:%s -> %s:%s",
                src_conn.host, task.source_path,
                dst_conn.host, task.dest_path,
            )

            _, stdout, stderr = client.exec_command(scp_cmd, timeout=10)
            # Ждём завершения scp (timeout не ограничивает выполнение)
            exit_code = stdout.channel.recv_exit_status()

            if exit_code != 0:
                err_msg = stderr.read().decode("utf-8", errors="replace").strip()
                logger.warning("Прямой SCP код %d: %s", exit_code, err_msg)
                return False

            # Прямой SCP не даёт промежуточный прогресс
            self.progress.emit(task.id, task.total_bytes, task.total_bytes)
            return True
        finally:
            client.close()

    def _do_streaming_transfer(self, task: TransferTask) -> None:
        """Потоковый транзит: чтение с сервера A, запись на сервер B.

        Данные идут через память без записи на диск.
        Поддерживает докачку при повторной попытке.
        """
        src_conn = task.source_connection
        dst_conn = task.dest_connection
        if src_conn is None or dst_conn is None:
            raise ValueError("Не указаны подключения для server-to-server")

        src_client, src_sftp = self._connect_sftp(src_conn)
        dst_client: paramiko.SSHClient | None = None
        dst_sftp: paramiko.SFTPClient | None = None
        try:
            # Размер исходного файла
            src_stat = src_sftp.stat(task.source_path)
            total_size = src_stat.st_size or 0
            task.total_bytes = total_size

            dst_client, dst_sftp = self._connect_sftp(dst_conn)

            # Создаём директории на назначении
            self._ensure_remote_dir(dst_sftp, task.dest_path)

            # Проверяем resume
            start_offset = 0
            if task.resume:
                try:
                    dst_stat = dst_sftp.stat(task.dest_path)
                    start_offset = dst_stat.st_size or 0
                    if start_offset >= total_size:
                        self.progress.emit(task.id, total_size, total_size)
                        return
                except FileNotFoundError:
                    start_offset = 0

            # Открываем источник
            with src_sftp.open(task.source_path, "rb") as src_f:
                src_f.prefetch(total_size)

                if start_offset > 0:
                    src_f.seek(start_offset)
                    # Дозапись в существующий файл
                    try:
                        dst_f = dst_sftp.open(task.dest_path, "r+b")
                        dst_f.seek(start_offset)
                    except IOError:
                        # Fallback: перезапись
                        src_f.seek(0)
                        start_offset = 0
                        dst_f = dst_sftp.open(task.dest_path, "wb")
                else:
                    dst_f = dst_sftp.open(task.dest_path, "wb")

                try:
                    transferred = start_offset
                    while True:
                        chunk = src_f.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        dst_f.write(chunk)
                        transferred += len(chunk)
                        task.transferred_bytes = transferred
                        self.progress.emit(task.id, transferred, total_size)
                finally:
                    dst_f.close()
        finally:
            src_sftp.close()
            src_client.close()
            if dst_sftp:
                dst_sftp.close()
            if dst_client:
                dst_client.close()
