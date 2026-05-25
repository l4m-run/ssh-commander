# -*- coding: utf-8 -*-
"""Главное окно приложения SSH Commander.

Layout:
┌──────────────────────────────────────────────────┐
│ Toolbar: [+ Новое] [Настройки]                   │
├────────────┬──────────────────────┬──────────────┤
│  Sidebar   │  Tab1 | Tab2 | Tab3 │  Команды     │
│  (дерево   │ ┌──────────────────┐│  (панель)    │
│  подключе- ││   Терминал       ││              │
│  ний)      ││                  ││              │
│            ││                  ││              │
│            │ └──────────────────┘│              │
├────────────┴──────────────────────┴──────────────┤
│ Statusbar: Подключено к server1 | 80x24          │
└──────────────────────────────────────────────────┘
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QTabWidget,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.config import config
from src.core.crypto import crypto
from src.core.database import Database
from src.core.ssh_manager import SSHSession
from src.models.connection import Connection
from src.ui.command_panel import CommandPanel
from src.ui.connection_dialog import ConnectionDialog
from src.ui.file_manager import FileManager
from src.ui.db_browser import DatabaseBrowser
from src.ui.terminal_widget import TerminalWidget

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Главное окно приложения.

    Attributes:
        _db: База данных подключений и команд.
        _sessions: Словарь активных SSH-сессий {tab_index: SSHSession}.
    """

    def __init__(self, db: Database) -> None:
        super().__init__()
        self._db = db
        self._sessions: dict[int, SSHSession] = {}
        self._file_managers: list[FileManager] = []
        self._db_browsers: list[DatabaseBrowser] = []

        self.setWindowTitle("SSH Commander")
        self.setMinimumSize(1024, 600)
        self.resize(1400, 800)

        # Иконка приложения
        icon_path = Path(__file__).parent.parent.parent / "resources" / "icons" / "app.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self._ui_ready = False
        self._setup_ui()
        self._setup_toolbar()
        self._setup_statusbar()
        self._ui_ready = True

        self._refresh_connections()

    def _setup_toolbar(self) -> None:
        """Создание панели инструментов."""
        toolbar = QToolBar("Основные действия")
        toolbar.setMovable(False)
        toolbar.setIconSize(toolbar.iconSize())
        self.addToolBar(toolbar)

        # Основная навигация
        conn_action = QAction("Подключения", self)
        conn_action.triggered.connect(self._show_connections)
        toolbar.addAction(conn_action)

        files_action = QAction("Файлы", self)
        files_action.setShortcut("Ctrl+F")
        files_action.triggered.connect(self._open_file_manager)
        toolbar.addAction(files_action)

        db_action = QAction("Базы данных", self)
        db_action.setShortcut("Ctrl+D")
        db_action.triggered.connect(self._open_db_browser)
        toolbar.addAction(db_action)

        # Spacer + смена пароля (справа)
        from PySide6.QtWidgets import QSizePolicy
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        pwd_action = QAction("Сменить пароль", self)
        pwd_action.triggered.connect(self._change_master_password)
        toolbar.addAction(pwd_action)

    def _setup_ui(self) -> None:
        """Создание основного интерфейса."""
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Основной сплиттер
        self._splitter = QSplitter(Qt.Orientation.Horizontal)

        # --- Левая панель: дерево подключений ---
        self._sidebar = QWidget()
        sidebar_layout = QVBoxLayout(self._sidebar)
        sidebar_layout.setContentsMargins(4, 4, 4, 4)
        sidebar_layout.setSpacing(4)

        # Кнопки над списком подключений
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)

        from PySide6.QtWidgets import QPushButton
        new_btn = QPushButton("+ Новое")
        new_btn.setShortcut("Ctrl+N")
        new_btn.clicked.connect(self._new_connection)
        btn_row.addWidget(new_btn)

        quick_btn = QPushButton("Быстрое")
        quick_btn.setShortcut("Ctrl+K")
        quick_btn.clicked.connect(self._quick_connect)
        btn_row.addWidget(quick_btn)

        sidebar_layout.addLayout(btn_row)

        # Вторая строка: импорт/экспорт
        io_row = QHBoxLayout()
        io_row.setSpacing(4)

        export_btn = QPushButton("Экспорт")
        export_btn.clicked.connect(self._export_connections)
        io_row.addWidget(export_btn)

        import_btn = QPushButton("Импорт")
        import_btn.clicked.connect(self._import_connections)
        io_row.addWidget(import_btn)

        sidebar_layout.addLayout(io_row)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Подключения"])
        self._tree.setMinimumWidth(180)
        self._tree.itemDoubleClicked.connect(self._on_tree_double_click)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._tree_context_menu)
        sidebar_layout.addWidget(self._tree)

        self._splitter.addWidget(self._sidebar)

        # --- Центральная часть: вкладки с терминалами ---
        self._tabs = QTabWidget()
        self._tabs.setTabsClosable(True)
        self._tabs.setMovable(True)
        self._tabs.tabCloseRequested.connect(self._close_tab)
        self._tabs.currentChanged.connect(self._on_tab_changed)

        # Placeholder при отсутствии вкладок
        self._empty_label = QLabel(
            "Двойной клик по подключению для открытия терминала\n\n"
            "Ctrl+N - новое подключение\n"
            "Ctrl+K - быстрое подключение"
        )
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet(
            "color: #9CA3AF; font-size: 16px; padding: 40px;"
        )
        self._tabs.addTab(self._empty_label, "Начало")

        self._splitter.addWidget(self._tabs)

        # --- Правая панель: избранные команды ---
        self._command_panel = CommandPanel(self._db)
        self._command_panel.setMinimumWidth(180)
        self._command_panel.command_execute.connect(self._execute_saved_command)
        self._splitter.addWidget(self._command_panel)

        # Пропорции сплиттера
        self._splitter.setSizes([220, 800, 250])

        layout.addWidget(self._splitter)

    def _setup_statusbar(self) -> None:
        """Создание статусбара."""
        self._status_label = QLabel("Готово")
        self.statusBar().addPermanentWidget(self._status_label)

    def _refresh_connections(self) -> None:
        """Обновить дерево подключений из БД."""
        self._tree.clear()
        connections = self._db.get_all_connections()

        # Группировка по group_name
        groups: dict[str, list[Connection]] = {}
        for conn in connections:
            group = conn.group_name or "Без группы"
            groups.setdefault(group, []).append(conn)

        for group_name, conns in sorted(groups.items()):
            if len(groups) == 1 and group_name == "Без группы":
                # Если всё без группы, не создаём родительский узел
                for conn in conns:
                    item = QTreeWidgetItem(self._tree)
                    item.setText(0, conn.display_name)
                    item.setData(0, Qt.ItemDataRole.UserRole, conn.id)
                    item.setToolTip(0, f"{conn.username}@{conn.host}:{conn.port}")
            else:
                group_item = QTreeWidgetItem(self._tree)
                group_item.setText(0, f"📁 {group_name}")
                group_item.setExpanded(True)
                for conn in conns:
                    item = QTreeWidgetItem(group_item)
                    item.setText(0, conn.display_name)
                    item.setData(0, Qt.ItemDataRole.UserRole, conn.id)
                    item.setToolTip(0, f"{conn.username}@{conn.host}:{conn.port}")

        self._tree.expandAll()

    def _new_connection(self) -> None:
        """Создать новое подключение через диалог."""
        dialog = ConnectionDialog(self)
        if dialog.exec() == ConnectionDialog.DialogCode.Accepted:
            conn = dialog.get_connection()
            self._db.save_connection(conn)
            self._refresh_connections()
            self._command_panel.refresh()

    def _quick_connect(self) -> None:
        """Быстрое подключение без сохранения."""
        # QInputDialog импортирован выше

        text, ok = QInputDialog.getText(
            self,
            "Быстрое подключение",
            "user@host:port (или user@host):",
        )
        if not ok or not text.strip():
            return

        # Парсинг строки user@host:port
        text = text.strip()
        port = 22
        username = "root"

        if "@" in text:
            username, host_part = text.split("@", 1)
        else:
            host_part = text

        if ":" in host_part:
            host, port_str = host_part.rsplit(":", 1)
            try:
                port = int(port_str)
            except ValueError:
                host = host_part
        else:
            host = host_part

        # Запрос пароля

        password, ok = QInputDialog.getText(
            self,
            "Пароль",
            f"Пароль для {username}@{host}:",
            QLineEdit.EchoMode.Password if hasattr(QInputDialog, 'getText') else 0,
        )
        if not ok:
            return

        conn = Connection(
            name=f"{username}@{host}",
            host=host,
            port=port,
            username=username,
        )
        self._open_terminal(conn, password)

    def _on_tree_double_click(self, item: QTreeWidgetItem, column: int) -> None:
        """Обработка двойного клика по подключению."""
        conn_id = item.data(0, Qt.ItemDataRole.UserRole)
        if conn_id is None:
            return  # Клик по группе

        conn = self._db.get_connection(conn_id)
        if conn is None:
            return

        # Расшифровка пароля
        password = ""
        if conn.encrypted_password:
            try:
                password = crypto.decrypt(conn.encrypted_password)
            except Exception as e:
                QMessageBox.warning(
                    self, "Ошибка",
                    f"Не удалось расшифровать пароль:\n{e}",
                )
                return

        # Обновить last_used
        self._db.update_last_used(conn_id, datetime.now().isoformat())

        self._open_terminal(conn, password)

    def _open_terminal(self, conn: Connection, password: str = "") -> None:
        """Открыть новую вкладку с терминалом.

        Args:
            conn: Объект подключения.
            password: Расшифрованный пароль.
        """
        # Создаём виджет терминала
        terminal = TerminalWidget()

        # Создаём SSH-сессию
        session = SSHSession(
            host=conn.host,
            port=conn.port,
            username=conn.username,
            password=password,
            key_path=conn.ssh_key_path,
        )

        # Привязываем сессию к терминалу
        terminal.attach_session(session)

        # Добавляем вкладку
        tab_name = conn.display_name
        # Удаляем placeholder если он есть
        if self._tabs.count() == 1 and self._tabs.widget(0) == self._empty_label:
            self._tabs.removeTab(0)

        tab_idx = self._tabs.addTab(terminal, tab_name)
        self._tabs.setCurrentIndex(tab_idx)
        self._sessions[tab_idx] = session

        # Обработчики событий сессии
        session.connected.connect(
            lambda: self._on_session_connected(tab_idx, conn)
        )
        session.disconnected.connect(
            lambda: self._on_session_disconnected(tab_idx)
        )
        session.error_occurred.connect(
            lambda msg: self._on_session_error(tab_idx, msg)
        )
        terminal.size_changed.connect(
            lambda cols, rows: self._update_status_size(cols, rows)
        )

        # Запускаем сессию
        session.start()
        self._status_label.setText(f"Подключение к {conn.host}...")

    def _on_session_connected(self, tab_idx: int, conn: Connection) -> None:
        """Обработка успешного подключения."""
        self._status_label.setText(
            f"Подключено к {conn.host}:{conn.port}"
        )
        # Обновляем заголовок вкладки с индикатором
        if tab_idx < self._tabs.count():
            current_text = self._tabs.tabText(tab_idx)
            if not current_text.startswith("🟢"):
                self._tabs.setTabText(tab_idx, f"🟢 {current_text}")

        # Обновить панель команд для текущего подключения
        if conn.id is not None:
            self._command_panel.set_current_connection(conn.id)

    def _on_session_disconnected(self, tab_idx: int) -> None:
        """Обработка отключения."""
        if tab_idx < self._tabs.count():
            current_text = self._tabs.tabText(tab_idx)
            # Заменяем зелёный индикатор на красный
            if current_text.startswith("🟢"):
                self._tabs.setTabText(
                    tab_idx, f"🔴 {current_text[2:]}"
                )
            else:
                self._tabs.setTabText(tab_idx, f"🔴 {current_text}")

    def _on_session_error(self, tab_idx: int, message: str) -> None:
        """Обработка ошибки сессии."""
        self._status_label.setText(f"Ошибка: {message}")
        QMessageBox.warning(self, "Ошибка подключения", message)

    def _update_status_size(self, cols: int, rows: int) -> None:
        """Обновить размер терминала в статусбаре."""
        current_text = self._status_label.text()
        # Убираем старый размер если есть
        if " | " in current_text:
            current_text = current_text.split(" | ")[0]
        self._status_label.setText(f"{current_text} | {cols}x{rows}")

    def _close_tab(self, index: int) -> None:
        """Закрыть вкладку и отключить сессию."""
        widget = self._tabs.widget(index)

        # Отключаем сессию
        if index in self._sessions:
            session = self._sessions[index]
            session.disconnect()
            session.wait(2000)
            del self._sessions[index]

        # Отвязываем терминал
        if isinstance(widget, TerminalWidget):
            widget.detach_session()

        self._tabs.removeTab(index)

        # Обновляем индексы сессий
        new_sessions = {}
        for old_idx, session in self._sessions.items():
            new_idx = old_idx if old_idx < index else old_idx - 1
            new_sessions[new_idx] = session
        self._sessions = new_sessions

        # Если нет вкладок - показываем placeholder
        if self._tabs.count() == 0:
            self._tabs.addTab(self._empty_label, "Начало")
            self._status_label.setText("Готово")

    def _on_tab_changed(self, index: int) -> None:
        """Обработка переключения вкладок."""
        if not self._ui_ready:
            return

        widget = self._tabs.widget(index)
        if isinstance(widget, TerminalWidget):
            widget.setFocus()

        # Скрываем боковые панели для файлового менеджера и браузера БД
        hide_panels = isinstance(widget, (FileManager, DatabaseBrowser))
        self._sidebar.setVisible(not hide_panels)
        self._command_panel.setVisible(not hide_panels)

    def _execute_saved_command(self, command_text: str) -> None:
        """Выполнить сохранённую команду в активном терминале."""
        current_widget = self._tabs.currentWidget()
        if not isinstance(current_widget, TerminalWidget):
            QMessageBox.warning(
                self, "Ошибка",
                "Нет активного терминала. Подключитесь к серверу.",
            )
            return

        current_idx = self._tabs.currentIndex()
        session = self._sessions.get(current_idx)
        if session and session.is_connected:
            session.execute_command(command_text)
        else:
            QMessageBox.warning(
                self, "Ошибка",
                "Сессия не активна.",
            )

    def _tree_context_menu(self, pos) -> None:
        """Контекстное меню для дерева подключений."""
        item = self._tree.itemAt(pos)
        menu = QMenu(self)

        new_action = menu.addAction("Новое подключение")
        new_action.triggered.connect(self._new_connection)

        if item:
            conn_id = item.data(0, Qt.ItemDataRole.UserRole)
            if conn_id is not None:
                menu.addSeparator()
                connect_action = menu.addAction("Подключиться")
                connect_action.triggered.connect(
                    lambda: self._on_tree_double_click(item, 0)
                )
                edit_action = menu.addAction("Редактировать")
                edit_action.triggered.connect(
                    lambda: self._edit_connection(conn_id)
                )
                menu.addSeparator()
                delete_action = menu.addAction("Удалить")
                delete_action.triggered.connect(
                    lambda: self._delete_connection(conn_id)
                )

        menu.exec(self._tree.mapToGlobal(pos))

    def _edit_connection(self, conn_id: int) -> None:
        """Редактировать подключение."""
        conn = self._db.get_connection(conn_id)
        if not conn:
            return

        dialog = ConnectionDialog(self, connection=conn)
        if dialog.exec() == ConnectionDialog.DialogCode.Accepted:
            updated = dialog.get_connection()
            self._db.save_connection(updated)
            self._refresh_connections()

    def _delete_connection(self, conn_id: int) -> None:
        """Удалить подключение."""
        conn = self._db.get_connection(conn_id)
        if not conn:
            return

        reply = QMessageBox.question(
            self,
            "Удаление",
            f"Удалить подключение '{conn.display_name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._db.delete_connection(conn_id)
            self._refresh_connections()

    def _export_connections(self) -> None:
        """Экспорт подключений в JSON файл.

        Показывает диалог с опцией включения паролей.
        При экспорте с паролями - расшифровывает и сохраняет
        в открытом виде (поле "password"), чтобы файл можно было
        импортировать на другой машине.
        """
        from PySide6.QtWidgets import QCheckBox, QFileDialog
        import json

        connections = self._db.get_all_connections()
        if not connections:
            QMessageBox.information(self, "Экспорт", "Нет подключений для экспорта.")
            return

        # Диалог выбора: с паролями или без
        export_dialog = QDialog(self)
        export_dialog.setWindowTitle("Экспорт подключений")
        export_dialog.setMinimumWidth(350)
        export_dialog.setModal(True)

        dlg_layout = QVBoxLayout(export_dialog)
        dlg_layout.setSpacing(10)

        info_label = QLabel(
            f"Будет экспортировано: {len(connections)} подключений"
        )
        dlg_layout.addWidget(info_label)

        include_passwords = QCheckBox("Включить пароли (открытым текстом)")
        dlg_layout.addWidget(include_passwords)

        password_warning = QLabel(
            "⚠ Пароли будут сохранены в файл без шифрования.\n"
            "Храните экспортированный файл в безопасном месте."
        )
        password_warning.setStyleSheet("color: #B45309; font-size: 12px;")
        password_warning.setWordWrap(True)
        password_warning.setVisible(False)
        dlg_layout.addWidget(password_warning)

        include_passwords.toggled.connect(password_warning.setVisible)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(export_dialog.accept)
        buttons.rejected.connect(export_dialog.reject)
        dlg_layout.addWidget(buttons)

        if export_dialog.exec() != QDialog.DialogCode.Accepted:
            return

        with_passwords = include_passwords.isChecked()

        path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт подключений",
            "ssh_connections.json",
            "JSON (*.json)",
        )
        if not path:
            return

        # Формируем данные для экспорта
        ssh_data = []
        password_errors = 0
        for conn in connections:
            item = conn.to_dict()
            item.pop("id", None)
            item.pop("encrypted_password", None)
            item.pop("created_at", None)
            item.pop("last_used", None)

            # Расшифровка пароля при необходимости
            if with_passwords and conn.encrypted_password:
                try:
                    item["password"] = crypto.decrypt(conn.encrypted_password)
                except Exception as e:
                    logger.warning(
                        "Не удалось расшифровать пароль для %s: %s",
                        conn.display_name, e,
                    )
                    password_errors += 1

            ssh_data.append(item)

        # Подключения к БД
        db_data = []
        db_conns = self._db.get_all_db_connections()
        for dc in db_conns:
            dc_item = dc.to_dict()
            dc_item.pop("id", None)
            # Заменяем ssh_connection_id на имя SSH-сервера для портативности
            if dc.ssh_connection_id:
                ssh_conn = self._db.get_connection(dc.ssh_connection_id)
                if ssh_conn:
                    dc_item["ssh_connection_ref"] = {
                        "host": ssh_conn.host,
                        "port": ssh_conn.port,
                        "username": ssh_conn.username,
                    }
            dc_item.pop("ssh_connection_id", None)

            # Расшифровка пароля БД
            dc_item.pop("encrypted_db_password", None)
            if with_passwords and dc.encrypted_db_password:
                try:
                    dc_item["db_password"] = crypto.decrypt(
                        dc.encrypted_db_password
                    )
                except Exception:
                    password_errors += 1

            db_data.append(dc_item)

        export_data = {
            "connections": ssh_data,
            "db_connections": db_data,
        }

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(export_data, f, ensure_ascii=False, indent=2)

            total = len(ssh_data) + len(db_data)
            msg = (
                f"Экспортировано: {len(ssh_data)} SSH "
                f"+ {len(db_data)} БД подключений."
            )
            if with_passwords:
                msg += "\nПароли включены (открытый текст)."
                if password_errors:
                    msg += f"\nНе удалось расшифровать: {password_errors}."
            else:
                msg += "\nБез паролей."
            QMessageBox.information(self, "Экспорт", msg)
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Ошибка экспорта:\n{e}")

    def _import_connections(self) -> None:
        """Импорт подключений из JSON файла.

        Поддерживает файлы с паролями (поле "password")
        и без них. Пароли шифруются текущим мастер-ключом.
        """
        from PySide6.QtWidgets import QFileDialog
        import json

        path, _ = QFileDialog.getOpenFileName(
            self, "Импорт подключений",
            "", "JSON (*.json)",
        )
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Ошибка чтения файла:\n{e}")
            return

        # Обратная совместимость: старый формат (list) и новый (dict)
        if isinstance(raw_data, list):
            ssh_list = raw_data
            db_list: list = []
        elif isinstance(raw_data, dict):
            ssh_list = raw_data.get("connections", [])
            db_list = raw_data.get("db_connections", [])
        else:
            QMessageBox.warning(self, "Ошибка", "Неверный формат файла.")
            return

        # Существующие подключения для проверки дублей
        existing = self._db.get_all_connections()
        existing_keys = {
            (c.host, c.port, c.username) for c in existing
        }

        imported = 0
        skipped = 0
        with_passwords = 0
        for item in ssh_list:
            if not isinstance(item, dict) or "host" not in item:
                continue
            host = item.get("host", "")
            port = item.get("port", 22)
            username = item.get("username", "")

            if (host, port, username) in existing_keys:
                skipped += 1
                continue

            # Шифруем пароль, если он есть в файле
            encrypted_password = ""
            plain_password = item.get("password", "")
            if plain_password:
                try:
                    encrypted_password = crypto.encrypt(plain_password)
                    with_passwords += 1
                except Exception as e:
                    logger.warning(
                        "Не удалось зашифровать пароль для %s@%s: %s",
                        username, host, e,
                    )

            conn = Connection(
                name=item.get("name", ""),
                host=host,
                port=port,
                username=username,
                encrypted_password=encrypted_password,
                ssh_key_path=item.get("ssh_key_path", ""),
                group_name=item.get("group_name", ""),
            )
            self._db.save_connection(conn)
            imported += 1

        # Импорт подключений к БД
        from src.models.db_connection import DbConnectionConfig
        db_imported = 0
        all_conns = self._db.get_all_connections()  # обновлённый список
        for dc_item in db_list:
            if not isinstance(dc_item, dict):
                continue

            # Находим SSH-подключение по референсу
            ssh_conn_id = None
            ref = dc_item.get("ssh_connection_ref")
            if ref and isinstance(ref, dict):
                for c in all_conns:
                    if (c.host == ref.get("host")
                            and c.port == ref.get("port")
                            and c.username == ref.get("username")):
                        ssh_conn_id = c.id
                        break

            # Шифруем пароль БД
            encrypted_db_pass = ""
            plain_db_pass = dc_item.get("db_password", "")
            if plain_db_pass:
                try:
                    encrypted_db_pass = crypto.encrypt(plain_db_pass)
                except Exception:
                    pass

            dc = DbConnectionConfig(
                name=dc_item.get("name", ""),
                ssh_connection_id=ssh_conn_id,
                db_type=dc_item.get("db_type", "postgresql"),
                db_host=dc_item.get("db_host", "localhost"),
                db_port=dc_item.get("db_port", 5432),
                db_user=dc_item.get("db_user", ""),
                encrypted_db_password=encrypted_db_pass,
                database_name=dc_item.get("database_name", ""),
            )
            self._db.save_db_connection(dc)
            db_imported += 1

        self._refresh_connections()
        msg = f"Импортировано: {imported} SSH"
        if db_imported:
            msg += f" + {db_imported} БД"
        if with_passwords:
            msg += f"\nС паролями: {with_passwords}"
        if skipped:
            msg += f"\nПропущено дублей: {skipped}"
        QMessageBox.information(self, "Импорт", msg)

    def _open_file_manager(self) -> None:
        """Открыть файловый менеджер как новую вкладку."""
        # Если уже есть открытая вкладка файлов, переключиться на неё
        for i in range(self._tabs.count()):
            if isinstance(self._tabs.widget(i), FileManager):
                self._tabs.setCurrentIndex(i)
                return

        fm = FileManager(self._db)
        self._file_managers.append(fm)

        # Удаляем placeholder если он есть
        if self._tabs.count() == 1 and self._tabs.widget(0) == self._empty_label:
            self._tabs.removeTab(0)

        tab_idx = self._tabs.addTab(fm, "Файлы")
        self._tabs.setCurrentIndex(tab_idx)

    def _open_db_browser(self) -> None:
        """Открыть браузер баз данных как новую вкладку."""
        # Если уже есть открытая вкладка БД, переключиться на неё
        for i in range(self._tabs.count()):
            if isinstance(self._tabs.widget(i), DatabaseBrowser):
                self._tabs.setCurrentIndex(i)
                return

        db_browser = DatabaseBrowser(self._db)
        self._db_browsers.append(db_browser)

        # Удаляем placeholder если он есть
        if self._tabs.count() == 1 and self._tabs.widget(0) == self._empty_label:
            self._tabs.removeTab(0)

        tab_idx = self._tabs.addTab(db_browser, "Базы данных")
        self._tabs.setCurrentIndex(tab_idx)

    def _show_connections(self) -> None:
        """Переключиться на режим подключений."""
        # Ищем первую не-файловую и не-БД вкладку (терминал или placeholder)
        for i in range(self._tabs.count()):
            widget = self._tabs.widget(i)
            if not isinstance(widget, (FileManager, DatabaseBrowser)):
                self._tabs.setCurrentIndex(i)
                return
        # Если все вкладки - файлы, создаём placeholder
        self._tabs.addTab(self._empty_label, "Начало")
        self._tabs.setCurrentWidget(self._empty_label)

    def _change_master_password(self) -> None:
        """Диалог смены мастер-пароля."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Смена мастер-пароля")
        dialog.setMinimumWidth(380)
        dialog.setModal(True)

        layout = QVBoxLayout(dialog)
        layout.setSpacing(10)

        form = QFormLayout()
        old_pwd = QLineEdit()
        old_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        old_pwd.setPlaceholderText("Текущий пароль")
        form.addRow("Текущий:", old_pwd)

        new_pwd = QLineEdit()
        new_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        new_pwd.setPlaceholderText("Новый пароль")
        form.addRow("Новый:", new_pwd)

        confirm_pwd = QLineEdit()
        confirm_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        confirm_pwd.setPlaceholderText("Подтвердите новый пароль")
        form.addRow("Подтвердите:", confirm_pwd)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        # Валидация
        if not old_pwd.text():
            QMessageBox.warning(self, "Ошибка", "Введите текущий пароль.")
            return
        if len(new_pwd.text()) < 4:
            QMessageBox.warning(
                self, "Ошибка",
                "Минимальная длина нового пароля - 4 символа.",
            )
            return
        if new_pwd.text() != confirm_pwd.text():
            QMessageBox.warning(self, "Ошибка", "Новые пароли не совпадают.")
            return

        # Смена пароля
        success, error = crypto.change_password(
            old_pwd.text(), new_pwd.text(),
        )
        if not success:
            QMessageBox.warning(self, "Ошибка", error)
            return

        # Перешифровка всех сохранённых паролей
        errors_count = 0
        for conn in self._db.get_all_connections():
            if conn.encrypted_password:
                try:
                    new_enc = crypto.reencrypt(conn.encrypted_password)
                    self._db.update_password(conn.id, new_enc)
                except Exception as e:
                    logger.error(
                        "Ошибка перешифровки пароля %s: %s",
                        conn.display_name, e,
                    )
                    errors_count += 1

        if errors_count > 0:
            QMessageBox.warning(
                self, "Внимание",
                f"Пароль изменён, но {errors_count} паролей не удалось перешифровать.",
            )
        else:
            QMessageBox.information(
                self, "Успех",
                "Мастер-пароль успешно изменён.",
            )

    def closeEvent(self, event) -> None:
        """Закрытие приложения - отключаем все сессии."""
        for session in self._sessions.values():
            session.disconnect()
            session.wait(1000)
        for fm in self._file_managers:
            fm.cleanup()
        for db_br in self._db_browsers:
            db_br.cleanup()
        self._db.close()
        event.accept()
