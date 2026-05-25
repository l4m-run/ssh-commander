# -*- coding: utf-8 -*-
"""Сравнение файлов: diff между локальным и удалённым, или между серверами.

Split-view с подсветкой изменений.
"""

from __future__ import annotations

import difflib
import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QTextCharFormat
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.core.crypto import crypto
from src.models.connection import Connection

if TYPE_CHECKING:
    from src.core.database import Database

logger = logging.getLogger(__name__)


class DiffViewerWidget(QWidget):
    """Виджет сравнения файлов.

    Layout:
    +---------------------------+---------------------------+
    |  Левая сторона            |  Правая сторона           |
    |  [Локальный v] [Файл..]  |  [SSH: serv v] [Файл..]  |
    |  [ содержимое файла ]     |  [ содержимое файла ]     |
    +---------------------------+---------------------------+
    |  [Сравнить]  [Diff-вывод]                             |
    +-------------------------------------------------------+
    """

    def __init__(
        self,
        app_db: Database,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._app_db = app_db
        self._left_content: str = ""
        self._right_content: str = ""

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Создание UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Верхний сплиттер: две панели файлов
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # --- Левая панель ---
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        left_header = QHBoxLayout()
        left_header.addWidget(QLabel("Источник:"))

        self._left_source = QComboBox()
        self._left_source.addItem("Локальный файл", "local")
        self._fill_ssh_sources(self._left_source)
        left_header.addWidget(self._left_source, stretch=1)

        self._left_path = QLineEdit()
        self._left_path.setPlaceholderText("Путь к файлу")
        left_header.addWidget(self._left_path, stretch=2)

        left_browse = QPushButton("...")
        left_browse.setMaximumWidth(30)
        left_browse.clicked.connect(self._browse_left)
        left_header.addWidget(left_browse)

        left_load = QPushButton("Загрузить")
        left_load.setStyleSheet(
            "QPushButton { background: #3B82F6; color: white;"
            " border: none; border-radius: 4px;"
            " padding: 4px 10px; font-size: 12px; }"
            "QPushButton:hover { background: #2563EB; }"
        )
        left_load.clicked.connect(self._load_left)
        left_header.addWidget(left_load)

        left_layout.addLayout(left_header)

        self._left_text = QPlainTextEdit()
        self._left_text.setReadOnly(True)
        self._left_text.setStyleSheet(
            "QPlainTextEdit { font-family: monospace; font-size: 12px; }"
        )
        left_layout.addWidget(self._left_text)

        splitter.addWidget(left_panel)

        # --- Правая панель ---
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        right_header = QHBoxLayout()
        right_header.addWidget(QLabel("Источник:"))

        self._right_source = QComboBox()
        self._right_source.addItem("Локальный файл", "local")
        self._fill_ssh_sources(self._right_source)
        right_header.addWidget(self._right_source, stretch=1)

        self._right_path = QLineEdit()
        self._right_path.setPlaceholderText("Путь к файлу")
        right_header.addWidget(self._right_path, stretch=2)

        right_browse = QPushButton("...")
        right_browse.setMaximumWidth(30)
        right_browse.clicked.connect(self._browse_right)
        right_header.addWidget(right_browse)

        right_load = QPushButton("Загрузить")
        right_load.setStyleSheet(
            "QPushButton { background: #3B82F6; color: white;"
            " border: none; border-radius: 4px;"
            " padding: 4px 10px; font-size: 12px; }"
            "QPushButton:hover { background: #2563EB; }"
        )
        right_load.clicked.connect(self._load_right)
        right_header.addWidget(right_load)

        right_layout.addLayout(right_header)

        self._right_text = QPlainTextEdit()
        self._right_text.setReadOnly(True)
        self._right_text.setStyleSheet(
            "QPlainTextEdit { font-family: monospace; font-size: 12px; }"
        )
        right_layout.addWidget(self._right_text)

        splitter.addWidget(right_panel)
        splitter.setSizes([500, 500])

        layout.addWidget(splitter, stretch=2)

        # --- Панель управления ---
        ctrl_row = QHBoxLayout()

        compare_btn = QPushButton("Сравнить")
        compare_btn.setStyleSheet(
            "QPushButton { background: #10B981; color: white;"
            " border: none; border-radius: 4px;"
            " padding: 8px 24px; font-weight: bold; font-size: 14px; }"
            "QPushButton:hover { background: #059669; }"
        )
        compare_btn.clicked.connect(self._compare)
        ctrl_row.addWidget(compare_btn)

        self._status_label = QLabel("")
        ctrl_row.addWidget(self._status_label, stretch=1)

        swap_btn = QPushButton("Поменять местами")
        swap_btn.setStyleSheet("QPushButton { padding: 6px 12px; }")
        swap_btn.clicked.connect(self._swap_sides)
        ctrl_row.addWidget(swap_btn)

        layout.addLayout(ctrl_row)

        # --- Diff-вывод ---
        self._diff_output = QTextEdit()
        self._diff_output.setReadOnly(True)
        self._diff_output.setStyleSheet(
            "QTextEdit { font-family: monospace; font-size: 12px; }"
        )
        layout.addWidget(self._diff_output, stretch=1)

    def _fill_ssh_sources(self, combo: QComboBox) -> None:
        """Добавить SSH-подключения в комбобокс."""
        connections = self._app_db.get_all_connections()
        for conn in connections:
            label = f"SSH: {conn.name} ({conn.host})"
            combo.addItem(label, conn.id)

    def _browse_left(self) -> None:
        """Выбрать локальный файл для левой стороны."""
        if self._left_source.currentData() != "local":
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Выбрать файл",
        )
        if path:
            self._left_path.setText(path)

    def _browse_right(self) -> None:
        """Выбрать локальный файл для правой стороны."""
        if self._right_source.currentData() != "local":
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Выбрать файл",
        )
        if path:
            self._right_path.setText(path)

    def _load_file(
        self, source_combo: QComboBox, path_edit: QLineEdit,
    ) -> str:
        """Загрузить содержимое файла.

        Args:
            source_combo: Комбобокс источника.
            path_edit: Поле пути.

        Returns:
            Содержимое файла.
        """
        path = path_edit.text().strip()
        if not path:
            raise ValueError("Путь к файлу не указан.")

        source = source_combo.currentData()

        if source == "local":
            return self._load_local(path)
        else:
            # SSH
            conn = self._app_db.get_connection(source)
            if not conn:
                raise ValueError("Подключение не найдено.")
            return self._load_remote(conn, path)

    def _load_local(self, path: str) -> str:
        """Загрузить локальный файл."""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception as e:
            raise ValueError(f"Ошибка чтения: {e}") from e

    def _load_remote(self, conn: Connection, path: str) -> str:
        """Загрузить файл через SSH/SFTP."""
        import paramiko

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        kwargs: dict = {
            "hostname": conn.host,
            "port": conn.port,
            "username": conn.username,
            "timeout": 15,
        }
        if conn.encrypted_password:
            try:
                kwargs["password"] = crypto.decrypt(conn.encrypted_password)
            except Exception:
                pass
        if conn.ssh_key_path:
            kwargs["key_filename"] = conn.ssh_key_path

        client.connect(**kwargs)
        _, stdout, _ = client.exec_command(f"cat '{path}'", timeout=30)
        content = stdout.read().decode("utf-8", errors="replace")
        client.close()
        return content

    def _load_left(self) -> None:
        """Загрузить файл в левую панель."""
        try:
            self._left_content = self._load_file(
                self._left_source, self._left_path,
            )
            self._left_text.setPlainText(self._left_content)
            lines = self._left_content.count("\n") + 1
            self._status_label.setText(
                f"Левый файл загружен ({lines} строк)"
            )
        except ValueError as e:
            QMessageBox.warning(self, "Ошибка", str(e))

    def _load_right(self) -> None:
        """Загрузить файл в правую панель."""
        try:
            self._right_content = self._load_file(
                self._right_source, self._right_path,
            )
            self._right_text.setPlainText(self._right_content)
            lines = self._right_content.count("\n") + 1
            self._status_label.setText(
                f"Правый файл загружен ({lines} строк)"
            )
        except ValueError as e:
            QMessageBox.warning(self, "Ошибка", str(e))

    def _compare(self) -> None:
        """Выполнить сравнение."""
        if not self._left_content and not self._right_content:
            QMessageBox.information(
                self, "Сравнение",
                "Загрузите файлы для сравнения.",
            )
            return

        left_lines = self._left_content.splitlines(keepends=True)
        right_lines = self._right_content.splitlines(keepends=True)

        left_name = self._left_path.text() or "Левый"
        right_name = self._right_path.text() or "Правый"

        diff = list(difflib.unified_diff(
            left_lines, right_lines,
            fromfile=left_name,
            tofile=right_name,
            lineterm="",
        ))

        if not diff:
            self._diff_output.setPlainText("Файлы идентичны.")
            self._status_label.setText("Файлы идентичны.")
            return

        # Форматирование diff с цветами
        self._diff_output.clear()
        cursor = self._diff_output.textCursor()

        # Форматы
        fmt_add = QTextCharFormat()
        fmt_add.setForeground(QColor("#22C55E"))
        fmt_add.setBackground(QColor("#052E16"))

        fmt_del = QTextCharFormat()
        fmt_del.setForeground(QColor("#EF4444"))
        fmt_del.setBackground(QColor("#450A0A"))

        fmt_header = QTextCharFormat()
        fmt_header.setForeground(QColor("#3B82F6"))
        fmt_header.setFontWeight(QFont.Weight.Bold)

        fmt_range = QTextCharFormat()
        fmt_range.setForeground(QColor("#A855F7"))

        fmt_normal = QTextCharFormat()
        fmt_normal.setForeground(QColor("#D4D4D4"))

        # Считаем изменения
        added = sum(1 for line in diff if line.startswith("+")
                    and not line.startswith("+++"))
        removed = sum(1 for line in diff if line.startswith("-")
                      and not line.startswith("---"))

        for line in diff:
            line_text = line.rstrip("\n") + "\n"
            if line.startswith("+++") or line.startswith("---"):
                cursor.insertText(line_text, fmt_header)
            elif line.startswith("@@"):
                cursor.insertText(line_text, fmt_range)
            elif line.startswith("+"):
                cursor.insertText(line_text, fmt_add)
            elif line.startswith("-"):
                cursor.insertText(line_text, fmt_del)
            else:
                cursor.insertText(line_text, fmt_normal)

        self._diff_output.setTextCursor(cursor)
        self._status_label.setText(
            f"Изменений: +{added} / -{removed}"
        )

    def _swap_sides(self) -> None:
        """Поменять левую и правую стороны."""
        # Содержимое
        self._left_content, self._right_content = (
            self._right_content, self._left_content
        )
        self._left_text.setPlainText(self._left_content)
        self._right_text.setPlainText(self._right_content)

        # Пути
        left_path = self._left_path.text()
        right_path = self._right_path.text()
        self._left_path.setText(right_path)
        self._right_path.setText(left_path)

        # Источники
        left_idx = self._left_source.currentIndex()
        right_idx = self._right_source.currentIndex()
        self._left_source.setCurrentIndex(right_idx)
        self._right_source.setCurrentIndex(left_idx)

    def cleanup(self) -> None:
        """Очистка при закрытии."""
        pass
