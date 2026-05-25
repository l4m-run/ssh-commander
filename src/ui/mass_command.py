# -*- coding: utf-8 -*-
"""Массовое выполнение команд на нескольких серверах.

Запускает одну команду параллельно на выбранных серверах
и собирает результаты в таблицу.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

from src.core.crypto import crypto
from src.models.connection import Connection

if TYPE_CHECKING:
    from src.core.database import Database

logger = logging.getLogger(__name__)


class _WorkerSignals(QObject):
    """Сигналы для воркера выполнения команды."""

    finished = Signal(int, str, str, float)  # conn_id, status, output, elapsed


class _CommandWorker(QRunnable):
    """Воркер для выполнения команды на одном сервере."""

    def __init__(
        self,
        conn: Connection,
        command: str,
    ) -> None:
        super().__init__()
        self.signals = _WorkerSignals()
        self._conn = conn
        self._command = command
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        """Подключиться, выполнить команду, отключиться."""
        import paramiko

        start = time.monotonic()
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            connect_kwargs: dict = {
                "hostname": self._conn.host,
                "port": self._conn.port,
                "username": self._conn.username,
                "timeout": 15,
            }

            # Пароль
            if self._conn.encrypted_password:
                try:
                    connect_kwargs["password"] = crypto.decrypt(
                        self._conn.encrypted_password
                    )
                except Exception:
                    pass

            # SSH-ключ
            if self._conn.ssh_key_path:
                connect_kwargs["key_filename"] = self._conn.ssh_key_path

            client.connect(**connect_kwargs)

            _, stdout, stderr = client.exec_command(
                self._command, timeout=120,
            )
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")

            exit_code = stdout.channel.recv_exit_status()
            client.close()

            elapsed = time.monotonic() - start
            output = out if out else err
            status = "OK" if exit_code == 0 else f"Код: {exit_code}"

            self.signals.finished.emit(
                self._conn.id or 0, status, output.strip(), elapsed,
            )

        except Exception as e:
            elapsed = time.monotonic() - start
            self.signals.finished.emit(
                self._conn.id or 0, "Ошибка", str(e), elapsed,
            )


class MassCommandWidget(QWidget):
    """Виджет массового выполнения команд.

    Layout:
    +-------------------------------------------+
    |  [x] Сервер1  [x] Сервер2  [ ] Сервер3   |
    |  [Выбрать все] [Снять все]                |
    +-------------------------------------------+
    |  Команда: [____________________] [Выпол.] |
    +-------------------------------------------+
    |  Результаты:                              |
    |  | Сервер | Статус | Время | Вывод |      |
    +-------------------------------------------+
    """

    def __init__(
        self,
        app_db: Database,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._app_db = app_db
        self._thread_pool = QThreadPool()
        self._thread_pool.setMaxThreadCount(10)
        self._pending = 0
        self._checkboxes: list[tuple[QCheckBox, Connection]] = []

        self._setup_ui()
        self._refresh_servers()

    def _setup_ui(self) -> None:
        """Создание UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Верхняя часть: серверы + команда
        top = QVBoxLayout()

        # Серверы
        servers_label = QLabel("Серверы:")
        servers_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        top.addWidget(servers_label)

        # Кнопки выбора
        sel_row = QHBoxLayout()

        select_all_btn = QPushButton("Выбрать все")
        select_all_btn.setStyleSheet(
            "QPushButton { padding: 4px 10px; font-size: 12px; }"
        )
        select_all_btn.clicked.connect(self._select_all)
        sel_row.addWidget(select_all_btn)

        deselect_all_btn = QPushButton("Снять все")
        deselect_all_btn.setStyleSheet(
            "QPushButton { padding: 4px 10px; font-size: 12px; }"
        )
        deselect_all_btn.clicked.connect(self._deselect_all)
        sel_row.addWidget(deselect_all_btn)

        refresh_btn = QPushButton("Обновить")
        refresh_btn.setStyleSheet(
            "QPushButton { padding: 4px 10px; font-size: 12px; }"
        )
        refresh_btn.clicked.connect(self._refresh_servers)
        sel_row.addWidget(refresh_btn)

        sel_row.addStretch()
        top.addLayout(sel_row)

        # Скролл-область с чекбоксами серверов
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(120)
        self._servers_container = QWidget()
        self._servers_layout = QVBoxLayout(self._servers_container)
        self._servers_layout.setSpacing(2)
        self._servers_layout.setContentsMargins(4, 4, 4, 4)
        scroll.setWidget(self._servers_container)
        top.addWidget(scroll)

        # Команда
        cmd_row = QHBoxLayout()
        cmd_row.addWidget(QLabel("Команда:"))

        self._cmd_edit = QLineEdit()
        self._cmd_edit.setPlaceholderText("hostname && uptime")
        self._cmd_edit.returnPressed.connect(self._execute)
        self._cmd_edit.setStyleSheet(
            "QLineEdit { font-family: monospace; padding: 6px;"
            " font-size: 13px; }"
        )
        cmd_row.addWidget(self._cmd_edit, stretch=1)

        self._exec_btn = QPushButton("Выполнить")
        self._exec_btn.setStyleSheet(
            "QPushButton { background: #10B981; color: white;"
            " border: none; border-radius: 4px;"
            " padding: 8px 20px; font-weight: bold; font-size: 13px; }"
            "QPushButton:hover { background: #059669; }"
            "QPushButton:disabled { background: #9CA3AF; }"
        )
        self._exec_btn.clicked.connect(self._execute)
        cmd_row.addWidget(self._exec_btn)

        top.addLayout(cmd_row)
        layout.addLayout(top)

        # Результаты
        self._results_table = QTableWidget()
        self._results_table.setColumnCount(4)
        self._results_table.setHorizontalHeaderLabels([
            "Сервер", "Статус", "Время", "Вывод",
        ])
        self._results_table.setAlternatingRowColors(True)
        self._results_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self._results_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )

        header = self._results_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

        # Двойной клик - детальный вывод
        self._results_table.doubleClicked.connect(self._show_detail)

        layout.addWidget(self._results_table, stretch=1)

        # Нижняя панель
        bottom = QHBoxLayout()

        self._status_label = QLabel("")
        bottom.addWidget(self._status_label, stretch=1)

        export_btn = QPushButton("Экспорт результатов")
        export_btn.setStyleSheet(
            "QPushButton { padding: 6px 12px; }"
        )
        export_btn.clicked.connect(self._export_results)
        bottom.addWidget(export_btn)

        clear_btn = QPushButton("Очистить")
        clear_btn.setStyleSheet(
            "QPushButton { padding: 6px 12px; }"
        )
        clear_btn.clicked.connect(self._clear_results)
        bottom.addWidget(clear_btn)

        layout.addLayout(bottom)

    def _refresh_servers(self) -> None:
        """Обновить список серверов."""
        # Очистить
        for cb, _ in self._checkboxes:
            cb.deleteLater()
        self._checkboxes.clear()

        connections = self._app_db.get_all_connections()
        for conn in connections:
            label = f"{conn.name} ({conn.username}@{conn.host}:{conn.port})"
            cb = QCheckBox(label)
            cb.setStyleSheet("QCheckBox { padding: 2px; }")
            self._servers_layout.addWidget(cb)
            self._checkboxes.append((cb, conn))

        self._servers_layout.addStretch()

    def _select_all(self) -> None:
        """Выбрать все серверы."""
        for cb, _ in self._checkboxes:
            cb.setChecked(True)

    def _deselect_all(self) -> None:
        """Снять выбор со всех серверов."""
        for cb, _ in self._checkboxes:
            cb.setChecked(False)

    def _get_selected(self) -> list[Connection]:
        """Получить выбранные серверы."""
        return [conn for cb, conn in self._checkboxes if cb.isChecked()]

    def _execute(self) -> None:
        """Запустить команду на выбранных серверах."""
        command = self._cmd_edit.text().strip()
        if not command:
            QMessageBox.information(
                self, "Выполнение", "Введите команду.",
            )
            return

        selected = self._get_selected()
        if not selected:
            QMessageBox.information(
                self, "Выполнение", "Выберите хотя бы один сервер.",
            )
            return

        # Очистить предыдущие результаты
        self._results_table.setRowCount(0)
        self._exec_btn.setEnabled(False)
        self._pending = len(selected)

        # Заполнить таблицу placeholders
        self._results_table.setRowCount(len(selected))
        self._conn_row_map: dict[int, int] = {}

        for i, conn in enumerate(selected):
            label = f"{conn.name} ({conn.host})"
            self._results_table.setItem(i, 0, QTableWidgetItem(label))
            self._results_table.setItem(i, 1, QTableWidgetItem("Выполняется..."))
            self._results_table.setItem(i, 2, QTableWidgetItem("-"))
            self._results_table.setItem(i, 3, QTableWidgetItem(""))
            self._conn_row_map[conn.id or 0] = i

        self._status_label.setText(
            f"Выполняется на {len(selected)} серверах..."
        )

        # Запуск воркеров
        for conn in selected:
            worker = _CommandWorker(conn, command)
            worker.signals.finished.connect(self._on_worker_finished)
            self._thread_pool.start(worker)

    @Slot(int, str, str, float)
    def _on_worker_finished(
        self, conn_id: int, status: str, output: str, elapsed: float,
    ) -> None:
        """Обработка завершения команды на сервере."""
        row = self._conn_row_map.get(conn_id)
        if row is None:
            return

        status_item = QTableWidgetItem(status)
        if "Ошибка" in status or "Код:" in status:
            status_item.setForeground(Qt.GlobalColor.red)
        else:
            status_item.setForeground(Qt.GlobalColor.darkGreen)

        self._results_table.setItem(row, 1, status_item)
        self._results_table.setItem(
            row, 2, QTableWidgetItem(f"{elapsed:.1f}с"),
        )

        # Обрезаем длинный вывод для таблицы
        short_output = output[:200].replace("\n", " ")
        if len(output) > 200:
            short_output += "..."
        out_item = QTableWidgetItem(short_output)
        out_item.setData(Qt.ItemDataRole.UserRole, output)
        self._results_table.setItem(row, 3, out_item)

        self._pending -= 1
        if self._pending <= 0:
            self._exec_btn.setEnabled(True)
            self._status_label.setText(
                f"Завершено. Всего: {self._results_table.rowCount()}"
            )

    def _show_detail(self) -> None:
        """Показать полный вывод в отдельном диалоге."""
        row = self._results_table.currentRow()
        if row < 0:
            return

        server = self._results_table.item(row, 0)
        output_item = self._results_table.item(row, 3)

        if not server or not output_item:
            return

        full_output = output_item.data(Qt.ItemDataRole.UserRole) or output_item.text()

        from PySide6.QtWidgets import QDialog
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Вывод: {server.text()}")
        dlg.setMinimumSize(600, 400)
        layout = QVBoxLayout(dlg)
        text = QPlainTextEdit()
        text.setPlainText(full_output)
        text.setReadOnly(True)
        text.setStyleSheet(
            "QPlainTextEdit { font-family: monospace; font-size: 13px; }"
        )
        layout.addWidget(text)

        copy_btn = QPushButton("Скопировать")
        copy_btn.clicked.connect(lambda: (
            QApplication.clipboard().setText(full_output)
            if QApplication.clipboard() else None
        ))
        layout.addWidget(copy_btn)
        dlg.exec()

    def _export_results(self) -> None:
        """Экспорт результатов в текстовый файл."""
        if self._results_table.rowCount() == 0:
            return

        from PySide6.QtWidgets import QFileDialog
        from datetime import datetime

        default_name = f"mass_cmd_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.txt"
        path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт результатов", default_name,
            "Text (*.txt);;CSV (*.csv)",
        )
        if not path:
            return

        lines = []
        lines.append(f"Команда: {self._cmd_edit.text()}")
        lines.append(f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("=" * 60)

        for row in range(self._results_table.rowCount()):
            server = self._results_table.item(row, 0)
            status = self._results_table.item(row, 1)
            elapsed = self._results_table.item(row, 2)
            output_item = self._results_table.item(row, 3)

            full_output = ""
            if output_item:
                full_output = (
                    output_item.data(Qt.ItemDataRole.UserRole)
                    or output_item.text()
                )

            lines.append(f"\n--- {server.text() if server else ''} ---")
            lines.append(f"Статус: {status.text() if status else ''}")
            lines.append(f"Время: {elapsed.text() if elapsed else ''}")
            lines.append(f"Вывод:\n{full_output}")

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            QMessageBox.information(
                self, "Экспорт", f"Результаты сохранены: {path}",
            )
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Ошибка сохранения:\n{e}")

    def _clear_results(self) -> None:
        """Очистить таблицу результатов."""
        self._results_table.setRowCount(0)
        self._status_label.setText("")

    def cleanup(self) -> None:
        """Очистка при закрытии."""
        self._thread_pool.waitForDone(1000)
