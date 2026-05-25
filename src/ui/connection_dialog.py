# -*- coding: utf-8 -*-
"""Диалог создания и редактирования SSH-подключения."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.core.crypto import crypto
from src.models.connection import Connection
from src.models.db_connection import DbConnectionConfig

if TYPE_CHECKING:
    from src.core.database import Database

logger = logging.getLogger(__name__)


class ConnectionDialog(QDialog):
    """Диалог создания/редактирования SSH-подключения.

    Args:
        parent: Родительский виджет.
        connection: Существующее подключение для редактирования (None = создание).
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        connection: Connection | None = None,
        app_db: Database | None = None,
    ) -> None:
        super().__init__(parent)
        self._connection = connection
        self._app_db = app_db
        self._is_edit = connection is not None

        self.setWindowTitle(
            "Редактирование подключения" if self._is_edit else "Новое подключение"
        )
        self.setMinimumWidth(500)
        self.setModal(True)

        self._setup_ui()

        if self._is_edit and connection:
            self._fill_from_connection(connection)
            self._refresh_db_list()

    def _setup_ui(self) -> None:
        """Создание интерфейса диалога."""
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        # --- Основные параметры ---
        main_group = QGroupBox("Подключение")
        form = QFormLayout(main_group)
        form.setSpacing(8)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Имя подключения (например: prod-server)")
        form.addRow("Имя:", self._name_edit)

        self._host_edit = QLineEdit()
        self._host_edit.setPlaceholderText("192.168.1.100 или hostname.example.com")
        form.addRow("Хост:", self._host_edit)

        self._port_spin = QSpinBox()
        self._port_spin.setRange(1, 65535)
        self._port_spin.setValue(22)
        form.addRow("Порт:", self._port_spin)

        layout.addWidget(main_group)

        # --- Аутентификация ---
        auth_group = QGroupBox("Аутентификация")
        auth_form = QFormLayout(auth_group)
        auth_form.setSpacing(8)

        self._username_edit = QLineEdit()
        self._username_edit.setPlaceholderText("root")
        auth_form.addRow("Логин:", self._username_edit)

        self._password_edit = QLineEdit()
        self._password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._password_edit.setPlaceholderText("Пароль SSH")
        auth_form.addRow("Пароль:", self._password_edit)

        # Путь к ключу
        key_row = QHBoxLayout()
        self._key_edit = QLineEdit()
        self._key_edit.setPlaceholderText("~/.ssh/id_rsa (опционально)")
        key_row.addWidget(self._key_edit)
        browse_btn = QPushButton("...")
        browse_btn.setFixedWidth(40)
        browse_btn.clicked.connect(self._browse_key)
        key_row.addWidget(browse_btn)
        auth_form.addRow("SSH-ключ:", key_row)

        layout.addWidget(auth_group)

        # --- Группировка ---
        group_group = QGroupBox("Организация")
        group_form = QFormLayout(group_group)

        self._group_edit = QLineEdit()
        self._group_edit.setPlaceholderText("production, staging, dev...")
        group_form.addRow("Группа:", self._group_edit)

        layout.addWidget(group_group)

        # --- Базы данных ---
        db_group = QGroupBox("Базы данных")
        db_layout = QVBoxLayout(db_group)

        self._db_list = QListWidget()
        self._db_list.setMaximumHeight(120)
        db_layout.addWidget(self._db_list)

        db_btn_row = QHBoxLayout()
        btn_style_sm = (
            "QPushButton { padding: 4px 10px; font-size: 12px; }"
        )

        add_db_btn = QPushButton("Добавить")
        add_db_btn.setStyleSheet(btn_style_sm)
        add_db_btn.clicked.connect(self._add_db_connection)
        db_btn_row.addWidget(add_db_btn)

        edit_db_btn = QPushButton("Редактировать")
        edit_db_btn.setStyleSheet(btn_style_sm)
        edit_db_btn.clicked.connect(self._edit_db_connection)
        db_btn_row.addWidget(edit_db_btn)

        del_db_btn = QPushButton("Удалить")
        del_db_btn.setStyleSheet(btn_style_sm)
        del_db_btn.clicked.connect(self._delete_db_connection)
        db_btn_row.addWidget(del_db_btn)

        db_btn_row.addStretch()
        db_layout.addLayout(db_btn_row)

        # Скрываем секцию для новых подключений (сначала нужно сохранить SSH)
        if not self._is_edit:
            db_group.setVisible(False)
        self._db_group = db_group
        layout.addWidget(db_group)

        # --- Кнопки ---
        btn_layout = QHBoxLayout()

        self._test_btn = QPushButton("Проверить")
        self._test_btn.clicked.connect(self._test_connection)
        btn_layout.addWidget(self._test_btn)

        btn_layout.addStretch()

        save_btn = QPushButton("Сохранить")
        save_btn.clicked.connect(self._on_save)
        btn_layout.addWidget(save_btn)

        cancel_btn = QPushButton("Отмена")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        layout.addLayout(btn_layout)

    def _fill_from_connection(self, conn: Connection) -> None:
        """Заполнить поля данными существующего подключения."""
        self._name_edit.setText(conn.name)
        self._host_edit.setText(conn.host)
        self._port_spin.setValue(conn.port)
        self._username_edit.setText(conn.username)
        self._key_edit.setText(conn.ssh_key_path)
        self._group_edit.setText(conn.group_name)
        # Пароль не показываем, только placeholder
        if conn.encrypted_password:
            self._password_edit.setPlaceholderText("••••••••")

    def _browse_key(self) -> None:
        """Открыть диалог выбора SSH-ключа."""
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Выбрать SSH-ключ",
            str(__import__("pathlib").Path.home() / ".ssh"),
            "Все файлы (*)",
        )
        if path:
            self._key_edit.setText(path)

    def _test_connection(self) -> None:
        """Проверить подключение к серверу."""
        host = self._host_edit.text().strip()
        if not host:
            QMessageBox.warning(self, "Ошибка", "Укажите хост для подключения.")
            return

        self._test_btn.setText("Проверяю...")
        self._test_btn.setEnabled(False)

        import paramiko
        import socket

        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            connect_kwargs: dict = {
                "hostname": host,
                "port": self._port_spin.value(),
                "username": self._username_edit.text().strip() or "root",
                "timeout": 5,
                "allow_agent": False,
                "look_for_keys": False,
            }

            key_path = self._key_edit.text().strip()
            password = self._password_edit.text()

            if key_path:
                connect_kwargs["key_filename"] = key_path
            elif password:
                connect_kwargs["password"] = password

            client.connect(**connect_kwargs)
            client.close()

            QMessageBox.information(
                self, "Успех", f"Подключение к {host} успешно!"
            )
        except paramiko.AuthenticationException:
            QMessageBox.warning(
                self, "Ошибка", "Неверный логин или пароль."
            )
        except socket.timeout:
            QMessageBox.warning(
                self, "Ошибка", f"Таймаут подключения к {host}."
            )
        except Exception as e:
            QMessageBox.warning(
                self, "Ошибка", f"Не удалось подключиться:\n{e}"
            )
        finally:
            self._test_btn.setText("Проверить")
            self._test_btn.setEnabled(True)

    def _on_save(self) -> None:
        """Сохранение подключения."""
        host = self._host_edit.text().strip()
        if not host:
            QMessageBox.warning(self, "Ошибка", "Хост обязателен для заполнения.")
            return

        self.accept()

    def get_connection(self) -> Connection:
        """Получить объект подключения из данных формы.

        Returns:
            Объект Connection с данными из формы.
        """
        password = self._password_edit.text()
        encrypted_pw = ""

        if password:
            encrypted_pw = crypto.encrypt(password)
        elif self._is_edit and self._connection:
            # Если пароль не изменён, сохраняем старый
            encrypted_pw = self._connection.encrypted_password

        conn = Connection(
            id=self._connection.id if self._connection else None,
            name=self._name_edit.text().strip(),
            host=self._host_edit.text().strip(),
            port=self._port_spin.value(),
            username=self._username_edit.text().strip(),
            encrypted_password=encrypted_pw,
            ssh_key_path=self._key_edit.text().strip(),
            group_name=self._group_edit.text().strip(),
        )

        if self._is_edit and self._connection:
            conn.created_at = self._connection.created_at

        return conn

    # --- Управление подключениями к БД ---

    def _refresh_db_list(self) -> None:
        """Обновить список подключений к БД для текущего SSH-сервера."""
        self._db_list.clear()
        if not self._app_db or not self._connection or not self._connection.id:
            return

        all_db_conns = self._app_db.get_all_db_connections()
        for dc in all_db_conns:
            if dc.ssh_connection_id == self._connection.id:
                item = QListWidgetItem(
                    f"{dc.db_type.upper()}: {dc.name} "
                    f"({dc.db_host}:{dc.db_port}/{dc.database_name})"
                )
                item.setData(Qt.ItemDataRole.UserRole, dc.id)
                self._db_list.addItem(item)

    def _add_db_connection(self) -> None:
        """Добавить подключение к БД."""
        if not self._app_db:
            return
        if not self._connection or not self._connection.id:
            QMessageBox.information(
                self, "Информация",
                "Сначала сохраните SSH-подключение.",
            )
            return

        dlg = DbConnectionDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            config = dlg.get_config()
            config.ssh_connection_id = self._connection.id
            self._app_db.save_db_connection(config)
            self._refresh_db_list()

    def _edit_db_connection(self) -> None:
        """Редактировать выбранное подключение к БД."""
        if not self._app_db:
            return
        item = self._db_list.currentItem()
        if not item:
            QMessageBox.information(
                self, "Редактирование", "Выберите подключение из списка.",
            )
            return

        dc_id = item.data(Qt.ItemDataRole.UserRole)
        dc = self._app_db.get_db_connection(dc_id)
        if not dc:
            return

        dlg = DbConnectionDialog(self, db_config=dc)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            config = dlg.get_config()
            config.id = dc.id
            config.ssh_connection_id = dc.ssh_connection_id
            self._app_db.save_db_connection(config)
            self._refresh_db_list()

    def _delete_db_connection(self) -> None:
        """Удалить выбранное подключение к БД."""
        if not self._app_db:
            return
        item = self._db_list.currentItem()
        if not item:
            return

        reply = QMessageBox.question(
            self, "Удаление",
            f"Удалить подключение '{item.text()}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            dc_id = item.data(Qt.ItemDataRole.UserRole)
            self._app_db.delete_db_connection(dc_id)
            self._refresh_db_list()


class DbConnectionDialog(QDialog):
    """Мини-диалог для ввода параметров подключения к БД.

    Args:
        parent: Родительский виджет.
        db_config: Существующая конфигурация для редактирования.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        db_config: DbConnectionConfig | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = db_config
        self.setWindowTitle(
            "Редактирование БД" if db_config else "Новое подключение к БД"
        )
        self.setMinimumWidth(400)
        self.setModal(True)
        self._setup_ui()
        if db_config:
            self._fill(db_config)

    def _setup_ui(self) -> None:
        """Создание интерфейса."""
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(8)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Prod PostgreSQL")
        form.addRow("Имя:", self._name_edit)

        from PySide6.QtWidgets import QComboBox
        self._type_combo = QComboBox()
        self._type_combo.addItems(["postgresql", "mysql", "sqlite"])
        self._type_combo.currentTextChanged.connect(self._on_type_changed)
        form.addRow("Тип:", self._type_combo)

        self._host_edit = QLineEdit("localhost")
        form.addRow("Хост БД:", self._host_edit)

        self._port_spin = QSpinBox()
        self._port_spin.setRange(1, 65535)
        self._port_spin.setValue(5432)
        form.addRow("Порт:", self._port_spin)

        self._user_edit = QLineEdit()
        self._user_edit.setPlaceholderText("postgres")
        form.addRow("Пользователь:", self._user_edit)

        self._pass_edit = QLineEdit()
        self._pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Пароль БД:", self._pass_edit)

        self._db_name_edit = QLineEdit()
        self._db_name_edit.setPlaceholderText("mydb")
        form.addRow("База данных:", self._db_name_edit)

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

    def _on_type_changed(self, db_type: str) -> None:
        """Смена типа - обновить порт по умолчанию."""
        ports = {"postgresql": 5432, "mysql": 3306, "sqlite": 0}
        self._port_spin.setValue(ports.get(db_type, 5432))

    def _fill(self, dc: DbConnectionConfig) -> None:
        """Заполнить форму из конфигурации."""
        self._name_edit.setText(dc.name)
        idx = self._type_combo.findText(dc.db_type)
        if idx >= 0:
            self._type_combo.setCurrentIndex(idx)
        self._host_edit.setText(dc.db_host)
        self._port_spin.setValue(dc.db_port)
        self._user_edit.setText(dc.db_user)
        self._db_name_edit.setText(dc.database_name)
        if dc.encrypted_db_password:
            self._pass_edit.setPlaceholderText("••••••••")

    def _on_save(self) -> None:
        """Валидация и сохранение."""
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Ошибка", "Имя обязательно.")
            return
        self.accept()

    def get_config(self) -> DbConnectionConfig:
        """Получить конфигурацию из формы."""
        password = self._pass_edit.text()
        encrypted = ""
        if password:
            encrypted = crypto.encrypt(password)
        elif self._config and self._config.encrypted_db_password:
            encrypted = self._config.encrypted_db_password

        return DbConnectionConfig(
            name=self._name_edit.text().strip(),
            db_type=self._type_combo.currentText(),
            db_host=self._host_edit.text().strip() or "localhost",
            db_port=self._port_spin.value(),
            db_user=self._user_edit.text().strip(),
            encrypted_db_password=encrypted,
            database_name=self._db_name_edit.text().strip(),
        )
