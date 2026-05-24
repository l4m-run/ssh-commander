# -*- coding: utf-8 -*-
"""Панель избранных команд.

Отображает список сохранённых команд с возможностью
быстрого запуска одним кликом.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.models.command import SavedCommand

if TYPE_CHECKING:
    from src.core.database import Database


class CommandEditDialog(QDialog):
    """Диалог создания/редактирования команды."""

    def __init__(
        self,
        parent: QWidget | None = None,
        command: SavedCommand | None = None,
        connections: list | None = None,
    ) -> None:
        super().__init__(parent)
        self._command = command
        self._connections = connections or []
        self.setWindowTitle(
            "Редактировать команду" if command else "Новая команда"
        )
        self.setMinimumWidth(400)
        self.setModal(True)
        self._setup_ui()
        if command:
            self._fill(command)

    def _setup_ui(self) -> None:
        """Создание UI диалога."""
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(8)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Имя команды")
        form.addRow("Имя:", self._name_edit)

        self._cmd_edit = QTextEdit()
        self._cmd_edit.setPlaceholderText("Текст команды (например: systemctl status nginx)")
        self._cmd_edit.setMaximumHeight(80)
        form.addRow("Команда:", self._cmd_edit)

        self._category_edit = QLineEdit()
        self._category_edit.setPlaceholderText("Категория (мониторинг, деплой...)")
        form.addRow("Категория:", self._category_edit)

        self._conn_combo = QComboBox()
        self._conn_combo.addItem("Глобальная (все серверы)", None)
        for conn in self._connections:
            self._conn_combo.addItem(conn.display_name, conn.id)
        form.addRow("Сервер:", self._conn_combo)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _fill(self, cmd: SavedCommand) -> None:
        """Заполнить поля данными команды."""
        self._name_edit.setText(cmd.name)
        self._cmd_edit.setPlainText(cmd.command_text)
        self._category_edit.setText(cmd.category)
        if cmd.connection_id is not None:
            idx = self._conn_combo.findData(cmd.connection_id)
            if idx >= 0:
                self._conn_combo.setCurrentIndex(idx)

    def _on_save(self) -> None:
        """Валидация и сохранение."""
        if not self._name_edit.text().strip():
            QMessageBox.warning(self, "Ошибка", "Имя команды обязательно.")
            return
        if not self._cmd_edit.toPlainText().strip():
            QMessageBox.warning(self, "Ошибка", "Текст команды обязателен.")
            return
        self.accept()

    def get_command(self) -> SavedCommand:
        """Получить объект команды из формы."""
        conn_id = self._conn_combo.currentData()
        return SavedCommand(
            id=self._command.id if self._command else None,
            name=self._name_edit.text().strip(),
            command_text=self._cmd_edit.toPlainText().strip(),
            connection_id=conn_id,
            category=self._category_edit.text().strip(),
            sort_order=self._command.sort_order if self._command else 0,
        )


class CommandPanel(QWidget):
    """Панель избранных команд.

    Signals:
        command_execute: Сигнал на выполнение команды (str - текст команды).
    """

    command_execute = Signal(str)

    def __init__(self, db: Database, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._db = db
        self._commands: list[SavedCommand] = []
        self._current_connection_id: int | None = None
        self._setup_ui()
        self.refresh()

    def _setup_ui(self) -> None:
        """Создание UI панели."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Заголовок
        header = QHBoxLayout()
        title = QLabel("Команды")
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        header.addWidget(title)

        add_btn = QPushButton("+")
        add_btn.setFixedSize(28, 28)
        add_btn.setToolTip("Добавить команду")
        add_btn.clicked.connect(self._add_command)
        header.addWidget(add_btn)

        layout.addLayout(header)

        # Фильтр
        self._filter_combo = QComboBox()
        self._filter_combo.addItem("Все команды", "all")
        self._filter_combo.addItem("Глобальные", "global")
        self._filter_combo.addItem("Для текущего сервера", "current")
        self._filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        layout.addWidget(self._filter_combo)

        # Список команд
        self._list = QListWidget()
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._show_context_menu)
        self._list.itemDoubleClicked.connect(self._execute_item)
        layout.addWidget(self._list)

    def set_current_connection(self, conn_id: int | None) -> None:
        """Установить текущее подключение для фильтрации."""
        self._current_connection_id = conn_id
        self._apply_filter()

    def refresh(self) -> None:
        """Обновить список команд из БД."""
        self._commands = self._db.get_all_commands()
        self._apply_filter()

    def _apply_filter(self) -> None:
        """Применить фильтр к списку команд."""
        self._list.clear()
        filter_type = self._filter_combo.currentData()

        for cmd in self._commands:
            show = False
            if filter_type == "all":
                show = True
            elif filter_type == "global":
                show = cmd.connection_id is None
            elif filter_type == "current":
                show = (
                    cmd.connection_id is None
                    or cmd.connection_id == self._current_connection_id
                )

            if show:
                item = QListWidgetItem()
                label = cmd.name
                if cmd.category:
                    label = f"[{cmd.category}] {label}"
                item.setText(label)
                item.setToolTip(cmd.command_text)
                item.setData(Qt.ItemDataRole.UserRole, cmd.id)
                self._list.addItem(item)

    def _on_filter_changed(self) -> None:
        """Обработка изменения фильтра."""
        self._apply_filter()

    def _execute_item(self, item: QListWidgetItem) -> None:
        """Выполнить команду по двойному клику."""
        cmd_id = item.data(Qt.ItemDataRole.UserRole)
        for cmd in self._commands:
            if cmd.id == cmd_id:
                self.command_execute.emit(cmd.command_text)
                break

    def _add_command(self) -> None:
        """Добавить новую команду."""
        connections = self._db.get_all_connections()
        dialog = CommandEditDialog(self, connections=connections)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            cmd = dialog.get_command()
            self._db.save_command(cmd)
            self.refresh()

    def _show_context_menu(self, pos) -> None:
        """Показать контекстное меню для команды."""
        item = self._list.itemAt(pos)
        if not item:
            return

        menu = QMenu(self)
        run_action = menu.addAction("Выполнить")
        edit_action = menu.addAction("Редактировать")
        dup_action = menu.addAction("Дублировать")
        menu.addSeparator()
        del_action = menu.addAction("Удалить")

        action = menu.exec(self._list.mapToGlobal(pos))
        cmd_id = item.data(Qt.ItemDataRole.UserRole)
        cmd = next((c for c in self._commands if c.id == cmd_id), None)

        if not cmd:
            return

        if action == run_action:
            self.command_execute.emit(cmd.command_text)
        elif action == edit_action:
            self._edit_command(cmd)
        elif action == dup_action:
            self._duplicate_command(cmd)
        elif action == del_action:
            self._delete_command(cmd)

    def _edit_command(self, cmd: SavedCommand) -> None:
        """Редактировать команду."""
        connections = self._db.get_all_connections()
        dialog = CommandEditDialog(self, command=cmd, connections=connections)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            updated = dialog.get_command()
            self._db.save_command(updated)
            self.refresh()

    def _duplicate_command(self, cmd: SavedCommand) -> None:
        """Дублировать команду."""
        new_cmd = SavedCommand(
            name=f"{cmd.name} (копия)",
            command_text=cmd.command_text,
            connection_id=cmd.connection_id,
            category=cmd.category,
        )
        self._db.save_command(new_cmd)
        self.refresh()

    def _delete_command(self, cmd: SavedCommand) -> None:
        """Удалить команду."""
        reply = QMessageBox.question(
            self,
            "Удаление",
            f"Удалить команду '{cmd.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes and cmd.id is not None:
            self._db.delete_command(cmd.id)
            self.refresh()
