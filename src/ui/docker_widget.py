# -*- coding: utf-8 -*-
"""Docker-менеджер: управление контейнерами на удалённых серверах через SSH.

Позволяет просматривать список контейнеров, логи,
выполнять start/stop/restart.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.crypto import crypto
from src.models.connection import Connection

if TYPE_CHECKING:
    from src.core.database import Database

logger = logging.getLogger(__name__)


@dataclass
class DockerContainer:
    """Информация о Docker-контейнере."""

    container_id: str = ""
    name: str = ""
    image: str = ""
    status: str = ""
    ports: str = ""
    created: str = ""
    state: str = ""  # running, exited, paused и т.д.


class _SshSignals(QObject):
    """Сигналы для SSH-воркеров."""

    finished = Signal(str, str, bool)  # action, output, success
    containers_loaded = Signal(list)  # list[DockerContainer]
    logs_loaded = Signal(str, str)  # container_name, logs


class _SshWorker(QRunnable):
    """Воркер для выполнения Docker-команды через SSH."""

    def __init__(
        self,
        conn: Connection,
        command: str,
        action: str = "command",
    ) -> None:
        super().__init__()
        self.signals = _SshSignals()
        self._conn = conn
        self._command = command
        self._action = action
        self.setAutoDelete(True)

    def _connect(self):
        """Создать SSH-клиент и подключиться."""
        import paramiko

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        kwargs: dict = {
            "hostname": self._conn.host,
            "port": self._conn.port,
            "username": self._conn.username,
            "timeout": 15,
        }

        if self._conn.encrypted_password:
            try:
                kwargs["password"] = crypto.decrypt(
                    self._conn.encrypted_password
                )
            except Exception:
                pass

        if self._conn.ssh_key_path:
            kwargs["key_filename"] = self._conn.ssh_key_path

        client.connect(**kwargs)
        return client

    @Slot()
    def run(self) -> None:
        """Выполнить команду."""
        try:
            client = self._connect()
            _, stdout, stderr = client.exec_command(
                self._command, timeout=30,
            )
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            exit_code = stdout.channel.recv_exit_status()
            client.close()

            success = exit_code == 0
            output = out if out else err

            if self._action == "list":
                containers = self._parse_containers(out)
                self.signals.containers_loaded.emit(containers)
            elif self._action == "logs":
                self.signals.logs_loaded.emit("", output)
            else:
                self.signals.finished.emit(
                    self._action, output.strip(), success,
                )

        except Exception as e:
            self.signals.finished.emit(self._action, str(e), False)

    def _parse_containers(self, raw: str) -> list[DockerContainer]:
        """Парсинг вывода docker ps --format json."""
        containers = []
        for line in raw.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                containers.append(DockerContainer(
                    container_id=data.get("ID", "")[:12],
                    name=data.get("Names", ""),
                    image=data.get("Image", ""),
                    status=data.get("Status", ""),
                    ports=data.get("Ports", ""),
                    created=data.get("CreatedAt", "")[:19],
                    state=data.get("State", ""),
                ))
            except json.JSONDecodeError:
                continue
        return containers


class DockerManagerWidget(QWidget):
    """Виджет управления Docker-контейнерами.

    Layout:
    +-----------------------------------------------+
    |  Сервер: [___v___]  [Обновить]                |
    +-----------------------------------------------+
    |  | Имя | Образ | Статус | Порты | Создан |    |
    |  |-----|-------|--------|-------|--------|    |
    +-----------------------------------------------+
    |  [Start] [Stop] [Restart] [Логи] [Inspect]   |
    +-----------------------------------------------+
    """

    def __init__(
        self,
        app_db: Database,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._app_db = app_db
        self._thread_pool = QThreadPool()
        self._thread_pool.setMaxThreadCount(3)
        self._current_conn: Connection | None = None

        self._setup_ui()
        self._refresh_connections()

    def _setup_ui(self) -> None:
        """Создание UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Выбор сервера
        server_row = QHBoxLayout()
        server_row.addWidget(QLabel("Сервер:"))

        self._server_combo = QComboBox()
        self._server_combo.setMinimumWidth(300)
        self._server_combo.currentIndexChanged.connect(self._on_server_changed)
        server_row.addWidget(self._server_combo, stretch=1)

        refresh_btn = QPushButton("Обновить")
        refresh_btn.setStyleSheet(
            "QPushButton { background: #3B82F6; color: white;"
            " border: none; border-radius: 4px;"
            " padding: 6px 16px; font-weight: bold; }"
            "QPushButton:hover { background: #2563EB; }"
        )
        refresh_btn.clicked.connect(self._load_containers)
        server_row.addWidget(refresh_btn)

        show_all_cb_label = QLabel("Все:")
        server_row.addWidget(show_all_cb_label)

        from PySide6.QtWidgets import QCheckBox
        self._show_all_cb = QCheckBox()
        self._show_all_cb.setChecked(True)
        self._show_all_cb.setToolTip("Показать остановленные контейнеры")
        self._show_all_cb.stateChanged.connect(self._load_containers)
        server_row.addWidget(self._show_all_cb)

        layout.addLayout(server_row)

        # Таблица контейнеров
        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels([
            "Имя", "Образ", "Состояние", "Статус", "Порты", "Создан",
        ])
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self._table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        self._table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)

        layout.addWidget(self._table, stretch=1)

        # Кнопки управления
        btn_row = QHBoxLayout()

        btn_style = (
            "QPushButton { background: #E5E5E7; color: #18181B;"
            " border: 1px solid #D4D4D8; border-radius: 4px;"
            " padding: 6px 14px; font-size: 12px; }"
            "QPushButton:hover { background: #D4D4D8; }"
            "QPushButton:disabled { color: #A1A1AA; }"
        )
        btn_green = (
            "QPushButton { background: #10B981; color: white;"
            " border: none; border-radius: 4px;"
            " padding: 6px 14px; font-weight: bold; }"
            "QPushButton:hover { background: #059669; }"
        )
        btn_red = (
            "QPushButton { background: #EF4444; color: white;"
            " border: none; border-radius: 4px;"
            " padding: 6px 14px; font-weight: bold; }"
            "QPushButton:hover { background: #DC2626; }"
        )

        start_btn = QPushButton("Start")
        start_btn.setStyleSheet(btn_green)
        start_btn.clicked.connect(lambda: self._docker_action("start"))
        btn_row.addWidget(start_btn)

        stop_btn = QPushButton("Stop")
        stop_btn.setStyleSheet(btn_red)
        stop_btn.clicked.connect(lambda: self._docker_action("stop"))
        btn_row.addWidget(stop_btn)

        restart_btn = QPushButton("Restart")
        restart_btn.setStyleSheet(btn_style)
        restart_btn.clicked.connect(lambda: self._docker_action("restart"))
        btn_row.addWidget(restart_btn)

        logs_btn = QPushButton("Логи")
        logs_btn.setStyleSheet(btn_style)
        logs_btn.clicked.connect(self._show_logs)
        btn_row.addWidget(logs_btn)

        inspect_btn = QPushButton("Inspect")
        inspect_btn.setStyleSheet(btn_style)
        inspect_btn.clicked.connect(self._show_inspect)
        btn_row.addWidget(inspect_btn)

        exec_btn = QPushButton("Exec")
        exec_btn.setStyleSheet(btn_style)
        exec_btn.clicked.connect(self._exec_in_container)
        btn_row.addWidget(exec_btn)

        btn_row.addStretch()

        self._status_label = QLabel("")
        btn_row.addWidget(self._status_label)

        layout.addLayout(btn_row)

    def _refresh_connections(self) -> None:
        """Обновить список SSH-подключений."""
        self._server_combo.clear()
        self._server_combo.addItem("-- Выберите сервер --", None)

        connections = self._app_db.get_all_connections()
        for conn in connections:
            label = f"{conn.name} ({conn.username}@{conn.host}:{conn.port})"
            self._server_combo.addItem(label, conn.id)

    def _on_server_changed(self, index: int) -> None:
        """При смене сервера."""
        conn_id = self._server_combo.currentData()
        if conn_id is None:
            self._current_conn = None
            self._table.setRowCount(0)
            return

        conn = self._app_db.get_connection(conn_id)
        self._current_conn = conn
        if conn:
            self._load_containers()

    def _get_selected_container(self) -> str | None:
        """Получить имя выбранного контейнера."""
        row = self._table.currentRow()
        if row < 0:
            return None
        item = self._table.item(row, 0)
        return item.text() if item else None

    def _load_containers(self) -> None:
        """Загрузить список контейнеров."""
        if not self._current_conn:
            return

        show_all = "-a" if self._show_all_cb.isChecked() else ""
        cmd = f"docker ps {show_all} --format '{{{{json .}}}}' --no-trunc"

        self._status_label.setText("Загрузка...")
        worker = _SshWorker(self._current_conn, cmd, action="list")
        worker.signals.containers_loaded.connect(self._on_containers_loaded)
        worker.signals.finished.connect(self._on_action_finished)
        self._thread_pool.start(worker)

    @Slot(list)
    def _on_containers_loaded(
        self, containers: list[DockerContainer],
    ) -> None:
        """Отобразить контейнеры."""
        self._table.setRowCount(len(containers))

        for row, c in enumerate(containers):
            name_item = QTableWidgetItem(c.name)
            self._table.setItem(row, 0, name_item)
            self._table.setItem(row, 1, QTableWidgetItem(c.image))

            # Состояние с цветом
            state_item = QTableWidgetItem(c.state)
            if c.state == "running":
                state_item.setForeground(Qt.GlobalColor.darkGreen)
            elif c.state in ("exited", "dead"):
                state_item.setForeground(Qt.GlobalColor.red)
            else:
                state_item.setForeground(Qt.GlobalColor.darkYellow)
            self._table.setItem(row, 2, state_item)

            self._table.setItem(row, 3, QTableWidgetItem(c.status))
            self._table.setItem(row, 4, QTableWidgetItem(c.ports))
            self._table.setItem(row, 5, QTableWidgetItem(c.created))

        self._table.resizeColumnsToContents()
        self._status_label.setText(
            f"Контейнеров: {len(containers)}"
        )

    def _docker_action(self, action: str) -> None:
        """Выполнить docker start/stop/restart."""
        name = self._get_selected_container()
        if not name or not self._current_conn:
            QMessageBox.information(
                self, "Docker", "Выберите контейнер.",
            )
            return

        cmd = f"docker {action} {name}"
        self._status_label.setText(f"{action}: {name}...")

        worker = _SshWorker(self._current_conn, cmd, action=action)
        worker.signals.finished.connect(self._on_action_finished)
        self._thread_pool.start(worker)

    @Slot(str, str, bool)
    def _on_action_finished(
        self, action: str, output: str, success: bool,
    ) -> None:
        """Обработка завершения действия."""
        if action == "list" and not success:
            self._status_label.setText(f"Ошибка: {output[:80]}")
            return

        if action in ("start", "stop", "restart"):
            status = "OK" if success else f"Ошибка: {output[:60]}"
            self._status_label.setText(f"{action}: {status}")
            # Обновить список после действия
            if success:
                self._load_containers()

    def _show_logs(self) -> None:
        """Показать логи контейнера."""
        name = self._get_selected_container()
        if not name or not self._current_conn:
            QMessageBox.information(
                self, "Docker", "Выберите контейнер.",
            )
            return

        # Диалог с настройками
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Логи: {name}")
        dlg.setMinimumSize(700, 500)

        layout = QVBoxLayout(dlg)

        # Настройки
        opts_row = QHBoxLayout()
        opts_row.addWidget(QLabel("Строк:"))
        lines_spin = QSpinBox()
        lines_spin.setRange(10, 5000)
        lines_spin.setValue(100)
        opts_row.addWidget(lines_spin)

        reload_btn = QPushButton("Загрузить")
        reload_btn.setStyleSheet(
            "QPushButton { background: #3B82F6; color: white;"
            " border: none; border-radius: 4px;"
            " padding: 6px 14px; font-weight: bold; }"
        )
        opts_row.addWidget(reload_btn)
        opts_row.addStretch()

        copy_btn = QPushButton("Скопировать")
        opts_row.addWidget(copy_btn)

        layout.addLayout(opts_row)

        # Текст логов
        log_text = QPlainTextEdit()
        log_text.setReadOnly(True)
        log_text.setStyleSheet(
            "QPlainTextEdit { font-family: monospace; font-size: 12px;"
            " background: #1E1E1E; color: #D4D4D4; }"
        )
        layout.addWidget(log_text)

        def load_logs():
            """Загрузить логи."""
            log_text.setPlainText("Загрузка...")
            tail = lines_spin.value()
            cmd = f"docker logs --tail {tail} {name} 2>&1"

            import paramiko
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

                kwargs: dict = {
                    "hostname": self._current_conn.host,
                    "port": self._current_conn.port,
                    "username": self._current_conn.username,
                    "timeout": 15,
                }
                if self._current_conn.encrypted_password:
                    try:
                        kwargs["password"] = crypto.decrypt(
                            self._current_conn.encrypted_password
                        )
                    except Exception:
                        pass
                if self._current_conn.ssh_key_path:
                    kwargs["key_filename"] = self._current_conn.ssh_key_path

                client.connect(**kwargs)
                _, stdout, _ = client.exec_command(cmd, timeout=30)
                out = stdout.read().decode("utf-8", errors="replace")
                client.close()
                log_text.setPlainText(out)
            except Exception as e:
                log_text.setPlainText(f"Ошибка: {e}")

        reload_btn.clicked.connect(load_logs)
        copy_btn.clicked.connect(lambda: (
            QApplication.clipboard().setText(log_text.toPlainText())
            if QApplication.clipboard() else None
        ))

        # Загружаем сразу
        load_logs()
        dlg.exec()

    def _show_inspect(self) -> None:
        """Показать docker inspect."""
        name = self._get_selected_container()
        if not name or not self._current_conn:
            return

        cmd = f"docker inspect {name}"
        self._run_and_show(cmd, f"Inspect: {name}")

    def _exec_in_container(self) -> None:
        """Выполнить команду внутри контейнера."""
        name = self._get_selected_container()
        if not name or not self._current_conn:
            QMessageBox.information(
                self, "Docker", "Выберите контейнер.",
            )
            return

        from PySide6.QtWidgets import QInputDialog
        cmd, ok = QInputDialog.getText(
            self, "Exec", f"Команда для {name}:",
            text="sh -c 'cat /etc/os-release'",
        )
        if not ok or not cmd:
            return

        full_cmd = f"docker exec {name} {cmd}"
        self._run_and_show(full_cmd, f"Exec: {name}")

    def _run_and_show(self, cmd: str, title: str) -> None:
        """Выполнить команду и показать результат в диалоге."""
        if not self._current_conn:
            return

        import paramiko
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            kwargs: dict = {
                "hostname": self._current_conn.host,
                "port": self._current_conn.port,
                "username": self._current_conn.username,
                "timeout": 15,
            }
            if self._current_conn.encrypted_password:
                try:
                    kwargs["password"] = crypto.decrypt(
                        self._current_conn.encrypted_password
                    )
                except Exception:
                    pass
            if self._current_conn.ssh_key_path:
                kwargs["key_filename"] = self._current_conn.ssh_key_path

            client.connect(**kwargs)
            _, stdout, stderr = client.exec_command(cmd, timeout=30)
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            client.close()
            result = out if out else err
        except Exception as e:
            result = f"Ошибка: {e}"

        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.setMinimumSize(600, 400)
        layout = QVBoxLayout(dlg)

        text = QPlainTextEdit()
        text.setPlainText(result)
        text.setReadOnly(True)
        text.setStyleSheet(
            "QPlainTextEdit { font-family: monospace; font-size: 13px; }"
        )
        layout.addWidget(text)

        copy_btn = QPushButton("Скопировать")
        copy_btn.clicked.connect(lambda: (
            QApplication.clipboard().setText(result)
            if QApplication.clipboard() else None
        ))
        layout.addWidget(copy_btn)

        dlg.exec()

    def cleanup(self) -> None:
        """Очистка при закрытии."""
        self._thread_pool.waitForDone(1000)
