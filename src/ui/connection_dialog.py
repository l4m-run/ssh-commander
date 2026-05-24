# -*- coding: utf-8 -*-
"""Диалог создания и редактирования SSH-подключения."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.core.crypto import crypto
from src.models.connection import Connection


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
    ) -> None:
        super().__init__(parent)
        self._connection = connection
        self._is_edit = connection is not None

        self.setWindowTitle(
            "Редактирование подключения" if self._is_edit else "Новое подключение"
        )
        self.setMinimumWidth(450)
        self.setModal(True)

        self._setup_ui()

        if self._is_edit and connection:
            self._fill_from_connection(connection)

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

        # --- Кнопки ---
        btn_layout = QHBoxLayout()

        self._test_btn = QPushButton("Проверить подключение")
        self._test_btn.setStyleSheet(
            "background-color: #27AE60; font-weight: bold;"
        )
        self._test_btn.clicked.connect(self._test_connection)
        btn_layout.addWidget(self._test_btn)

        btn_layout.addStretch()

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._on_save)
        button_box.rejected.connect(self.reject)
        btn_layout.addWidget(button_box)

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
            self._test_btn.setText("Проверить подключение")
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
