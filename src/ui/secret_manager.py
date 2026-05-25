# -*- coding: utf-8 -*-
"""Менеджер секретов: хранение паролей, токенов, ключей.

Включает таблицу секретов с CRUD и встроенный генератор паролей.
"""

from __future__ import annotations

import logging
import secrets
import string
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.crypto import crypto
from src.models.secret import SecretEntry

if TYPE_CHECKING:
    from src.core.database import Database

logger = logging.getLogger(__name__)


class SecretManager(QWidget):
    """Менеджер секретов.

    Layout:
    +---------------------------------------------------------+
    |  [Добавить] [Редактировать] [Удалить] [Скопировать]     |
    +---------------------------------------------------------+
    |  Таблица секретов                  |  Генератор паролей  |
    |  | Имя | Логин | URL | Категория | |  Длина: [===] 24   |
    |  |-----|-------|-----|-----------|  |  [x] Заглавные     |
    |  | ... | ...   | ... | ...       | |  [x] Строчные      |
    |                                    |  [x] Цифры          |
    |                                    |  [x] Спецсимволы    |
    |                                    |  [Сгенерировать]    |
    |                                    |  [пароль] [Копир.]  |
    +---------------------------------------------------------+
    """

    def __init__(
        self,
        app_db: Database,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._app_db = app_db

        self._setup_ui()
        self._refresh_table()

    def _setup_ui(self) -> None:
        """Создание UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Панель кнопок
        self._setup_toolbar(layout)

        # Основная область: таблица + генератор
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Таблица секретов
        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels([
            "Имя", "Логин", "URL", "Категория", "Обновлено",
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
        self._table.doubleClicked.connect(self._edit_secret)

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)

        splitter.addWidget(self._table)

        # Генератор паролей
        gen_widget = self._setup_generator()
        splitter.addWidget(gen_widget)

        splitter.setSizes([600, 280])
        layout.addWidget(splitter, stretch=1)

    def _setup_toolbar(self, parent_layout: QVBoxLayout) -> None:
        """Кнопки управления."""
        row = QHBoxLayout()

        btn_style = (
            "QPushButton { background: #3B82F6; color: white;"
            " border: none; border-radius: 4px;"
            " padding: 6px 16px; font-weight: bold; }"
            "QPushButton:hover { background: #2563EB; }"
        )
        btn_style_sec = (
            "QPushButton { background: #E5E5E7; color: #18181B;"
            " border: 1px solid #D4D4D8; border-radius: 4px;"
            " padding: 6px 12px; }"
            "QPushButton:hover { background: #D4D4D8; }"
        )

        add_btn = QPushButton("Добавить")
        add_btn.setStyleSheet(btn_style)
        add_btn.clicked.connect(self._add_secret)
        row.addWidget(add_btn)

        edit_btn = QPushButton("Редактировать")
        edit_btn.setStyleSheet(btn_style_sec)
        edit_btn.clicked.connect(self._edit_secret)
        row.addWidget(edit_btn)

        del_btn = QPushButton("Удалить")
        del_btn.setStyleSheet(btn_style_sec)
        del_btn.clicked.connect(self._delete_secret)
        row.addWidget(del_btn)

        copy_btn = QPushButton("Скопировать пароль")
        copy_btn.setStyleSheet(btn_style_sec)
        copy_btn.clicked.connect(self._copy_password)
        row.addWidget(copy_btn)

        row.addStretch()

        # Поиск
        row.addWidget(QLabel("Поиск:"))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Имя, логин, URL...")
        self._search_edit.setMaximumWidth(200)
        self._search_edit.textChanged.connect(self._filter_table)
        row.addWidget(self._search_edit)

        parent_layout.addLayout(row)

    def _setup_generator(self) -> QWidget:
        """Генератор паролей."""
        group = QGroupBox("Генератор паролей")
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Длина
        len_row = QHBoxLayout()
        len_row.addWidget(QLabel("Длина:"))

        self._len_slider = QSlider(Qt.Orientation.Horizontal)
        self._len_slider.setRange(4, 128)
        self._len_slider.setValue(20)
        self._len_slider.valueChanged.connect(self._on_length_changed)
        len_row.addWidget(self._len_slider, stretch=1)

        self._len_spin = QSpinBox()
        self._len_spin.setRange(4, 128)
        self._len_spin.setValue(20)
        self._len_spin.valueChanged.connect(self._len_slider.setValue)
        self._len_slider.valueChanged.connect(self._len_spin.setValue)
        len_row.addWidget(self._len_spin)

        layout.addLayout(len_row)

        # Опции
        self._upper_cb = QCheckBox("Заглавные (A-Z)")
        self._upper_cb.setChecked(True)
        layout.addWidget(self._upper_cb)

        self._lower_cb = QCheckBox("Строчные (a-z)")
        self._lower_cb.setChecked(True)
        layout.addWidget(self._lower_cb)

        self._digits_cb = QCheckBox("Цифры (0-9)")
        self._digits_cb.setChecked(True)
        layout.addWidget(self._digits_cb)

        self._special_cb = QCheckBox("Спецсимволы (!@#$%...)")
        self._special_cb.setChecked(True)
        layout.addWidget(self._special_cb)

        # Кнопка генерации
        gen_btn = QPushButton("Сгенерировать")
        gen_btn.setStyleSheet(
            "QPushButton { background: #10B981; color: white;"
            " border: none; border-radius: 4px;"
            " padding: 8px 16px; font-weight: bold; font-size: 13px; }"
            "QPushButton:hover { background: #059669; }"
        )
        gen_btn.clicked.connect(self._generate_password)
        layout.addWidget(gen_btn)

        # Результат
        self._gen_result = QLineEdit()
        self._gen_result.setReadOnly(True)
        self._gen_result.setStyleSheet(
            "QLineEdit { font-family: monospace; font-size: 14px;"
            " padding: 6px; background: #F4F4F5; border: 1px solid #D4D4D8;"
            " border-radius: 4px; }"
        )
        layout.addWidget(self._gen_result)

        copy_gen_btn = QPushButton("Скопировать")
        copy_gen_btn.setStyleSheet(
            "QPushButton { background: #E5E5E7; color: #18181B;"
            " border: 1px solid #D4D4D8; border-radius: 4px;"
            " padding: 6px 12px; }"
            "QPushButton:hover { background: #D4D4D8; }"
        )
        copy_gen_btn.clicked.connect(self._copy_generated)
        layout.addWidget(copy_gen_btn)

        # Индикатор сложности
        self._strength_label = QLabel("")
        self._strength_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._strength_label)

        layout.addStretch()

        # Генерируем сразу
        self._generate_password()

        return group

    def _on_length_changed(self, value: int) -> None:
        """Изменение длины - автогенерация."""
        # Не генерируем при каждом движении слайдера
        pass

    # --- Таблица секретов ---

    def _refresh_table(self) -> None:
        """Обновить таблицу секретов."""
        all_secrets = self._app_db.get_all_secrets()
        self._table.setRowCount(len(all_secrets))

        for row_idx, secret in enumerate(all_secrets):
            # Имя
            name_item = QTableWidgetItem(secret.name)
            name_item.setData(Qt.ItemDataRole.UserRole, secret.id)
            self._table.setItem(row_idx, 0, name_item)

            # Логин
            self._table.setItem(row_idx, 1, QTableWidgetItem(secret.username))

            # URL
            self._table.setItem(row_idx, 2, QTableWidgetItem(secret.url))

            # Категория
            self._table.setItem(row_idx, 3, QTableWidgetItem(secret.category))

            # Обновлено
            updated = secret.updated_at[:16].replace("T", " ") if secret.updated_at else ""
            self._table.setItem(row_idx, 4, QTableWidgetItem(updated))

        self._table.resizeColumnsToContents()

    def _get_selected_id(self) -> int | None:
        """Получить ID выбранного секрета."""
        row = self._table.currentRow()
        if row < 0:
            return None
        item = self._table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _add_secret(self) -> None:
        """Добавить секрет."""
        dlg = SecretDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            secret = dlg.get_secret()
            self._app_db.save_secret(secret)
            self._refresh_table()

    def _edit_secret(self) -> None:
        """Редактировать секрет."""
        secret_id = self._get_selected_id()
        if secret_id is None:
            QMessageBox.information(
                self, "Редактирование", "Выберите запись.",
            )
            return

        secret = self._app_db.get_secret(secret_id)
        if not secret:
            return

        dlg = SecretDialog(self, secret=secret)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            updated = dlg.get_secret()
            updated.id = secret.id
            updated.created_at = secret.created_at
            self._app_db.save_secret(updated)
            self._refresh_table()

    def _delete_secret(self) -> None:
        """Удалить секрет."""
        secret_id = self._get_selected_id()
        if secret_id is None:
            return

        row = self._table.currentRow()
        name = self._table.item(row, 0).text() if self._table.item(row, 0) else ""

        reply = QMessageBox.question(
            self, "Удаление",
            f"Удалить секрет '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._app_db.delete_secret(secret_id)
            self._refresh_table()

    def _copy_password(self) -> None:
        """Скопировать пароль выбранного секрета в буфер."""
        secret_id = self._get_selected_id()
        if secret_id is None:
            QMessageBox.information(
                self, "Копирование", "Выберите запись.",
            )
            return

        secret = self._app_db.get_secret(secret_id)
        if not secret or not secret.encrypted_password:
            return

        try:
            password = crypto.decrypt(secret.encrypted_password)
            clipboard = QApplication.clipboard()
            if clipboard:
                clipboard.setText(password)
            self._show_status("Пароль скопирован в буфер обмена")
        except Exception as e:
            QMessageBox.warning(
                self, "Ошибка", f"Не удалось расшифровать пароль:\n{e}",
            )

    def _filter_table(self, text: str) -> None:
        """Фильтрация таблицы по тексту."""
        query = text.lower()
        for row in range(self._table.rowCount()):
            match = False
            for col in range(self._table.columnCount()):
                item = self._table.item(row, col)
                if item and query in item.text().lower():
                    match = True
                    break
            self._table.setRowHidden(row, not match)

    def _show_status(self, msg: str) -> None:
        """Показать статус на 3 секунды через window statusbar."""
        window = self.window()
        if hasattr(window, "statusBar"):
            window.statusBar().showMessage(msg, 3000)

    # --- Генератор ---

    def _generate_password(self) -> None:
        """Сгенерировать пароль с текущими параметрами."""
        length = self._len_spin.value()
        charset = ""

        if self._upper_cb.isChecked():
            charset += string.ascii_uppercase
        if self._lower_cb.isChecked():
            charset += string.ascii_lowercase
        if self._digits_cb.isChecked():
            charset += string.digits
        if self._special_cb.isChecked():
            charset += "!@#$%^&*()-_=+[]{}|;:,.<>?"

        if not charset:
            charset = string.ascii_letters + string.digits

        password = "".join(secrets.choice(charset) for _ in range(length))
        self._gen_result.setText(password)

        # Индикатор сложности
        entropy = length * len(set(charset))
        if entropy > 2000:
            self._strength_label.setText("Сложность: Очень высокая")
            self._strength_label.setStyleSheet("color: #10B981; font-weight: bold;")
        elif entropy > 500:
            self._strength_label.setText("Сложность: Высокая")
            self._strength_label.setStyleSheet("color: #3B82F6; font-weight: bold;")
        elif entropy > 200:
            self._strength_label.setText("Сложность: Средняя")
            self._strength_label.setStyleSheet("color: #F59E0B; font-weight: bold;")
        else:
            self._strength_label.setText("Сложность: Низкая")
            self._strength_label.setStyleSheet("color: #EF4444; font-weight: bold;")

    def _copy_generated(self) -> None:
        """Скопировать сгенерированный пароль."""
        text = self._gen_result.text()
        if text:
            clipboard = QApplication.clipboard()
            if clipboard:
                clipboard.setText(text)
            self._show_status("Пароль скопирован в буфер обмена")

    # --- Публичные ---

    def cleanup(self) -> None:
        """Очистка при закрытии."""
        pass


class SecretDialog(QDialog):
    """Диалог добавления/редактирования секрета."""

    def __init__(
        self,
        parent: QWidget | None = None,
        secret: SecretEntry | None = None,
    ) -> None:
        super().__init__(parent)
        self._secret = secret
        self.setWindowTitle(
            "Редактирование секрета" if secret else "Новый секрет"
        )
        self.setMinimumWidth(450)
        self.setModal(True)
        self._setup_ui()
        if secret:
            self._fill(secret)

    def _setup_ui(self) -> None:
        """Создание интерфейса."""
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(8)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("GitHub, Prod Server, API Key...")
        form.addRow("Имя:", self._name_edit)

        self._username_edit = QLineEdit()
        self._username_edit.setPlaceholderText("user@example.com")
        form.addRow("Логин:", self._username_edit)

        # Пароль с кнопкой показать
        pass_row = QHBoxLayout()
        self._pass_edit = QLineEdit()
        self._pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        pass_row.addWidget(self._pass_edit)

        toggle_btn = QPushButton("Показать")
        toggle_btn.setFixedWidth(70)
        toggle_btn.setCheckable(True)
        toggle_btn.toggled.connect(
            lambda checked: self._pass_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if checked
                else QLineEdit.EchoMode.Password
            )
        )
        toggle_btn.toggled.connect(
            lambda checked: toggle_btn.setText(
                "Скрыть" if checked else "Показать"
            )
        )
        pass_row.addWidget(toggle_btn)
        form.addRow("Пароль:", pass_row)

        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("https://example.com")
        form.addRow("URL:", self._url_edit)

        self._category_edit = QLineEdit()
        self._category_edit.setPlaceholderText("Серверы, API, Сервисы...")
        form.addRow("Категория:", self._category_edit)

        self._notes_edit = QPlainTextEdit()
        self._notes_edit.setPlaceholderText("Заметки...")
        self._notes_edit.setMaximumHeight(80)
        form.addRow("Заметки:", self._notes_edit)

        layout.addLayout(form)

        # Кнопки
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        save_btn = QPushButton("Сохранить")
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)

        cancel_btn = QPushButton("Отмена")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        layout.addLayout(btn_row)

    def _fill(self, secret: SecretEntry) -> None:
        """Заполнить форму."""
        self._name_edit.setText(secret.name)
        self._username_edit.setText(secret.username)
        self._url_edit.setText(secret.url)
        self._category_edit.setText(secret.category)
        self._notes_edit.setPlainText(secret.notes)
        if secret.encrypted_password:
            self._pass_edit.setPlaceholderText("••••••••")

    def _on_save(self) -> None:
        """Валидация и сохранение."""
        if not self._name_edit.text().strip():
            QMessageBox.warning(self, "Ошибка", "Имя обязательно.")
            return
        self.accept()

    def get_secret(self) -> SecretEntry:
        """Получить секрет из формы."""
        password = self._pass_edit.text()
        encrypted = ""
        if password:
            encrypted = crypto.encrypt(password)
        elif self._secret and self._secret.encrypted_password:
            encrypted = self._secret.encrypted_password

        return SecretEntry(
            name=self._name_edit.text().strip(),
            username=self._username_edit.text().strip(),
            encrypted_password=encrypted,
            url=self._url_edit.text().strip(),
            notes=self._notes_edit.toPlainText().strip(),
            category=self._category_edit.text().strip(),
        )
