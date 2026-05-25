# -*- coding: utf-8 -*-
"""Systemd-менеджер: управление сервисами на удалённых серверах через SSH.

Позволяет просматривать список сервисов, управлять ими
(start/stop/restart) и просматривать логи (journalctl).
"""

from __future__ import annotations

import logging
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
    QLineEdit,
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
class SystemdUnit:
    """Информация о systemd-юните."""

    name: str = ""
    load: str = ""       # loaded / not-found / masked
    active: str = ""     # active / inactive / failed / activating
    sub: str = ""        # running / dead / exited / failed
    description: str = ""


class _SshSignals(QObject):
    """Сигналы для SSH-воркеров."""

    finished = Signal(str, str, bool)  # action, output, success
    units_loaded = Signal(list)  # list[SystemdUnit]


class _SshWorker(QRunnable):
    """Воркер для выполнения systemd-команды через SSH."""

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
                units = self._parse_units(out)
                try:
                    self.signals.units_loaded.emit(units)
                except RuntimeError:
                    pass
            else:
                try:
                    self.signals.finished.emit(
                        self._action, output.strip(), success,
                    )
                except RuntimeError:
                    pass

        except Exception as e:
            try:
                self.signals.finished.emit(self._action, str(e), False)
            except RuntimeError:
                pass

    @staticmethod
    def _parse_units(raw: str) -> list[SystemdUnit]:
        """Парсинг вывода systemctl list-units."""
        units = []
        lines = raw.strip().splitlines()

        for line in lines:
            # Пропуск заголовка и итоговых строк
            line = line.strip()
            if not line or line.startswith("UNIT") or line.startswith("LOAD"):
                continue
            if "loaded units listed" in line or line.startswith("To show"):
                continue

            # Формат: UNIT LOAD ACTIVE SUB DESCRIPTION...
            # Первый символ может быть маркером (●)
            if line.startswith("●"):
                line = line[1:].strip()

            parts = line.split(None, 4)
            if len(parts) < 4:
                continue

            name = parts[0]
            # Фильтруем только .service
            if not name.endswith(".service"):
                continue

            units.append(SystemdUnit(
                name=name.replace(".service", ""),
                load=parts[1],
                active=parts[2],
                sub=parts[3],
                description=parts[4] if len(parts) > 4 else "",
            ))

        return units


class SystemdWidget(QWidget):
    """Виджет управления systemd-сервисами.

    Layout:
    +--------------------------------------------------+
    |  Сервер: [___v___]  [Обновить]  Фильтр: [____]   |
    +--------------------------------------------------+
    |  | Сервис | Состояние | Статус | Описание |       |
    +--------------------------------------------------+
    |  [Start] [Stop] [Restart] [Status] [Логи]        |
    +--------------------------------------------------+
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
        self._all_units: list[SystemdUnit] = []

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
        self._server_combo.setMinimumWidth(250)
        self._server_combo.currentIndexChanged.connect(
            self._on_server_changed,
        )
        server_row.addWidget(self._server_combo, stretch=1)

        refresh_btn = QPushButton("Обновить")
        refresh_btn.setStyleSheet(
            "QPushButton { background: #3B82F6; color: white;"
            " border: none; border-radius: 4px;"
            " padding: 6px 16px; font-weight: bold; }"
            "QPushButton:hover { background: #2563EB; }"
        )
        refresh_btn.clicked.connect(self._load_units)
        server_row.addWidget(refresh_btn)

        # Фильтр по имени
        server_row.addWidget(QLabel("Фильтр:"))
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("nginx, ssh, docker...")
        self._filter_edit.setMaximumWidth(200)
        self._filter_edit.textChanged.connect(self._apply_filter)
        server_row.addWidget(self._filter_edit)

        # Фильтр по состоянию
        self._state_combo = QComboBox()
        self._state_combo.addItems(["Все", "active", "inactive", "failed"])
        self._state_combo.currentIndexChanged.connect(self._apply_filter)
        server_row.addWidget(self._state_combo)

        layout.addLayout(server_row)

        # Таблица сервисов
        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels([
            "Сервис", "Состояние", "Статус", "Описание",
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
        start_btn.clicked.connect(lambda: self._systemd_action("start"))
        btn_row.addWidget(start_btn)

        stop_btn = QPushButton("Stop")
        stop_btn.setStyleSheet(btn_red)
        stop_btn.clicked.connect(lambda: self._systemd_action("stop"))
        btn_row.addWidget(stop_btn)

        restart_btn = QPushButton("Restart")
        restart_btn.setStyleSheet(btn_style)
        restart_btn.clicked.connect(lambda: self._systemd_action("restart"))
        btn_row.addWidget(restart_btn)

        status_btn = QPushButton("Status")
        status_btn.setStyleSheet(btn_style)
        status_btn.clicked.connect(self._show_status)
        btn_row.addWidget(status_btn)

        logs_btn = QPushButton("Логи")
        logs_btn.setStyleSheet(btn_style)
        logs_btn.clicked.connect(self._show_logs)
        btn_row.addWidget(logs_btn)

        enable_btn = QPushButton("Enable")
        enable_btn.setStyleSheet(btn_style)
        enable_btn.clicked.connect(lambda: self._systemd_action("enable"))
        btn_row.addWidget(enable_btn)

        disable_btn = QPushButton("Disable")
        disable_btn.setStyleSheet(btn_style)
        disable_btn.clicked.connect(lambda: self._systemd_action("disable"))
        btn_row.addWidget(disable_btn)

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

    def _on_server_changed(self, _index: int) -> None:
        """При смене сервера."""
        conn_id = self._server_combo.currentData()
        if conn_id is None:
            self._current_conn = None
            self._table.setRowCount(0)
            return

        conn = self._app_db.get_connection(conn_id)
        self._current_conn = conn
        if conn:
            self._load_units()

    def _get_selected_unit(self) -> str | None:
        """Получить имя выбранного сервиса."""
        row = self._table.currentRow()
        if row < 0:
            return None
        item = self._table.item(row, 0)
        return item.text() if item else None

    def _load_units(self) -> None:
        """Загрузить список сервисов."""
        if not self._current_conn:
            return

        cmd = (
            "systemctl list-units --type=service --all "
            "--no-pager --plain --no-legend"
        )

        self._status_label.setText("Загрузка...")
        worker = _SshWorker(self._current_conn, cmd, action="list")
        worker.signals.units_loaded.connect(self._on_units_loaded)
        worker.signals.finished.connect(self._on_action_finished)
        self._thread_pool.start(worker)

    @Slot(list)
    def _on_units_loaded(self, units: list[SystemdUnit]) -> None:
        """Отобразить сервисы."""
        self._all_units = units
        self._apply_filter()
        self._status_label.setText(f"Сервисов: {len(units)}")

    def _apply_filter(self) -> None:
        """Применить фильтры по имени и состоянию."""
        text_filter = self._filter_edit.text().strip().lower()
        state_filter = self._state_combo.currentText()

        filtered = self._all_units
        if text_filter:
            filtered = [
                u for u in filtered
                if text_filter in u.name.lower()
                or text_filter in u.description.lower()
            ]
        if state_filter != "Все":
            filtered = [u for u in filtered if u.active == state_filter]

        self._table.setRowCount(len(filtered))

        for row, u in enumerate(filtered):
            name_item = QTableWidgetItem(u.name)
            self._table.setItem(row, 0, name_item)

            # Состояние с цветом
            active_item = QTableWidgetItem(u.active)
            if u.active == "active":
                active_item.setForeground(Qt.GlobalColor.darkGreen)
            elif u.active == "failed":
                active_item.setForeground(Qt.GlobalColor.red)
            elif u.active == "inactive":
                active_item.setForeground(Qt.GlobalColor.gray)
            else:
                active_item.setForeground(Qt.GlobalColor.darkYellow)
            self._table.setItem(row, 1, active_item)

            sub_item = QTableWidgetItem(u.sub)
            if u.sub == "running":
                sub_item.setForeground(Qt.GlobalColor.darkGreen)
            elif u.sub in ("dead", "failed"):
                sub_item.setForeground(Qt.GlobalColor.red)
            self._table.setItem(row, 2, sub_item)

            self._table.setItem(row, 3, QTableWidgetItem(u.description))

        self._table.resizeColumnsToContents()

    def _systemd_action(self, action: str) -> None:
        """Выполнить systemctl start/stop/restart/enable/disable."""
        name = self._get_selected_unit()
        if not name or not self._current_conn:
            QMessageBox.information(
                self, "Systemd", "Выберите сервис.",
            )
            return

        cmd = f"sudo systemctl {action} {name}.service"
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

        if action in ("start", "stop", "restart", "enable", "disable"):
            status = "OK" if success else f"Ошибка: {output[:60]}"
            self._status_label.setText(f"{action}: {status}")
            if success:
                self._load_units()

    def _show_status(self) -> None:
        """Показать systemctl status."""
        name = self._get_selected_unit()
        if not name or not self._current_conn:
            return

        cmd = f"systemctl status {name}.service --no-pager -l"
        self._run_and_show(cmd, f"Status: {name}")

    def _show_logs(self) -> None:
        """Показать логи сервиса через journalctl."""
        name = self._get_selected_unit()
        if not name or not self._current_conn:
            QMessageBox.information(
                self, "Systemd", "Выберите сервис.",
            )
            return

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

        opts_row.addWidget(QLabel("Приоритет:"))
        prio_combo = QComboBox()
        prio_combo.addItems([
            "Все", "emerg", "alert", "crit", "err",
            "warning", "notice", "info", "debug",
        ])
        opts_row.addWidget(prio_combo)

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
            "QPlainTextEdit { font-family: monospace; font-size: 12px; }"
        )
        layout.addWidget(log_text)

        def load_logs() -> None:
            """Загрузить логи."""
            log_text.setPlainText("Загрузка...")
            tail = lines_spin.value()
            prio = prio_combo.currentText()

            prio_flag = ""
            if prio != "Все":
                prio_flag = f" -p {prio}"

            cmd = (
                f"sudo journalctl -u {name}.service"
                f" --no-pager -n {tail}{prio_flag}"
            )

            import paramiko
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(
                    paramiko.AutoAddPolicy(),
                )

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

        load_logs()
        dlg.exec()

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
        d_layout = QVBoxLayout(dlg)

        text = QPlainTextEdit()
        text.setPlainText(result)
        text.setReadOnly(True)
        text.setStyleSheet(
            "QPlainTextEdit { font-family: monospace; font-size: 13px; }"
        )
        d_layout.addWidget(text)

        copy_btn = QPushButton("Скопировать")
        copy_btn.clicked.connect(lambda: (
            QApplication.clipboard().setText(result)
            if QApplication.clipboard() else None
        ))
        d_layout.addWidget(copy_btn)

        dlg.exec()

    def cleanup(self) -> None:
        """Очистка при закрытии."""
        self._thread_pool.waitForDone(1000)
