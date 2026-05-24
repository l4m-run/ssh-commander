# -*- coding: utf-8 -*-
"""Точка входа приложения SSH Commander.

Запуск: python -m src.main
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from src.core.config import config
from src.core.crypto import crypto
from src.core.database import Database
from src.ui.main_window import MainWindow
from src.ui.styles import get_app_stylesheet

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class MasterPasswordDialog(QDialog):
    """Диалог ввода мастер-пароля.

    При первом запуске - создание пароля с подтверждением.
    При последующих - ввод существующего.
    """

    def __init__(self, is_first_run: bool) -> None:
        super().__init__()
        self._is_first_run = is_first_run
        self.setWindowTitle("SSH Commander")
        self.setMinimumWidth(380)
        self.setModal(True)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Создание UI диалога."""
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        if self._is_first_run:
            title = QLabel("Создайте мастер-пароль")
            title.setStyleSheet("font-size: 18px; font-weight: bold;")
            layout.addWidget(title)

            hint = QLabel(
                "Мастер-пароль защищает ваши SSH-пароли.\n"
                "Его нельзя восстановить, запомните его."
            )
            hint.setStyleSheet("color: #6B7280; font-size: 12px;")
            hint.setWordWrap(True)
            layout.addWidget(hint)

            self._password_edit = QLineEdit()
            self._password_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self._password_edit.setPlaceholderText("Мастер-пароль")
            layout.addWidget(self._password_edit)

            self._confirm_edit = QLineEdit()
            self._confirm_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self._confirm_edit.setPlaceholderText("Подтвердите пароль")
            layout.addWidget(self._confirm_edit)
        else:
            title = QLabel("Введите мастер-пароль")
            title.setStyleSheet("font-size: 18px; font-weight: bold;")
            layout.addWidget(title)

            self._password_edit = QLineEdit()
            self._password_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self._password_edit.setPlaceholderText("Мастер-пароль")
            self._password_edit.returnPressed.connect(self._on_accept)
            layout.addWidget(self._password_edit)

            self._confirm_edit = None

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._password_edit.setFocus()

    def _on_accept(self) -> None:
        """Проверка и принятие пароля."""
        password = self._password_edit.text()

        if not password:
            QMessageBox.warning(self, "Ошибка", "Введите пароль.")
            return

        if self._is_first_run:
            if len(password) < 4:
                QMessageBox.warning(
                    self, "Ошибка",
                    "Минимальная длина пароля - 4 символа.",
                )
                return

            if self._confirm_edit and password != self._confirm_edit.text():
                QMessageBox.warning(
                    self, "Ошибка", "Пароли не совпадают."
                )
                return

        self.accept()

    @property
    def password(self) -> str:
        """Получить введённый пароль."""
        return self._password_edit.text()


def main() -> int:
    """Главная функция запуска приложения.

    Returns:
        Код выхода.
    """
    app = QApplication(sys.argv)
    app.setApplicationName("SSH Commander")
    app.setOrganizationName("ssh-commander")

    # Применяем стили
    app.setStyleSheet(get_app_stylesheet())

    # --- Мастер-пароль ---
    is_first_run = not crypto.is_initialized
    max_attempts = 3

    for attempt in range(max_attempts):
        dialog = MasterPasswordDialog(is_first_run)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            logger.info("Пользователь отменил ввод мастер-пароля")
            return 0

        if crypto.unlock(dialog.password):
            logger.info("Хранилище разблокировано")
            break
        else:
            remaining = max_attempts - attempt - 1
            if remaining > 0:
                QMessageBox.warning(
                    None,
                    "Неверный пароль",
                    f"Неверный мастер-пароль.\nОсталось попыток: {remaining}",
                )
            else:
                QMessageBox.critical(
                    None,
                    "Доступ заблокирован",
                    "Превышено количество попыток.",
                )
                return 1
    else:
        return 1

    # --- Инициализация БД ---
    db = Database(config.db_path)
    db.connect()
    logger.info("БД инициализирована: %s", config.db_path)

    # --- Главное окно ---
    window = MainWindow(db)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
